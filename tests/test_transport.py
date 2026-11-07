import json,threading,time
import pytest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from PIL import Image
from maze_bench.transport import Transport,Proxy,metadata_response
from maze_bench.common import endpoint


def test_native_messages_json_response_is_serialized_as_complete_sse(tmp_path):
    import urllib.request
    public='{"actions":["left"]}'
    payload={'type':'message','role':'assistant','id':'test-message','model':'fixture',
             'content':[{'type':'thinking','thinking':'synthetic private fixture','signature':'test-signature'},
                        {'type':'text','text':public}],
             'stop_reason':'end_turn','usage':{'input_tokens':12,'output_tokens':34}}
    class SavedTransport:
        calls=[];log=tmp_path/'wire.json';round=1;key='test-only-key';model='fixture'
        def request(self,incoming,path,main,headers):
            assert incoming['stream']is True and path=='/v1/messages'
            self.calls.append(path)
            data=json.dumps(payload).encode()
            return 200,data,metadata_response(data)
    transport=SavedTransport();proxy=Proxy(transport,'claude')
    try:
        req=urllib.request.Request(proxy.base+'/v1/messages',data=json.dumps({'messages':[],'stream':True}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5)as response:data=response.read()
    finally:proxy.close()
    meta=metadata_response(data)
    assert meta['is_stream']and meta['terminal']and meta['text']==public
    assert meta['finish_status']=='end_turn'and len(transport.calls)==1
    assert b'test-signature'in data and b'synthetic private fixture'in data
    assert data.count(b'event: message_stop\n')==1


def test_account_quota_defers_same_case_without_pruning_or_repeated_requests(tmp_path):
    count=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']));count.append(1)
            raw=json.dumps({'error':{'message':'Individual quota reached. Resets in 28h44m25s.','code':'model_cooldown'}}).encode()
            self.send_response(429);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    transport=Transport('test-only-key',f'http://127.0.0.1:{http.server_port}','same-medium-source',tmp_path,{'request_attempts':5})
    transport.set_feed({'round':5,'frames':frames(tmp_path,[3,4])})
    try:
        with pytest.raises(RuntimeError,match='upstream_account_quota_exhausted'):
            transport.request({'messages':[]},'/v1/chat/completions')
    finally:http.shutdown();http.server_close();thread.join()
    deferred=json.loads((tmp_path/'retry-after.json').read_text())
    assert deferred['source_alias']=='same-medium-source'and deferred['observation_round']==5
    assert deferred['not_before']>time.time()+3500
    assert len(count)==1 and len(transport.selection.kept)==2 and not transport.selection.drops

def frames(tmp_path,rounds):
    result=[]
    for i,n in enumerate(rounds):
        path=tmp_path/f'frame{i}.png';Image.new('RGB',(1280,720),(30+i*15,80,120)).save(path)
        result.append({'file':path.name,'path':str(path),'round':n,'index':i,'label':f'round{n} frame{i}'})
    return result

@contextmanager
def server(limit=3,transient=0):
    accepted=[];all_requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));all_requests.append(body)
            images=[b for m in body['messages']for b in(m['content']if isinstance(m['content'],list)else[])if b.get('type')=='image_url']
            status=502 if len(all_requests)<=transient else 413 if len(images)>limit else 200
            if status==200:accepted.append(body)
            payload={'choices':[{'message':{'content':'{"actions":["left"]}'}}]}if status==200 else{'error':{'message':'request too large'if status==413 else'upstream_unavailable'}}
            raw=json.dumps(payload).encode();self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{http.server_port}',accepted,all_requests
    finally:http.shutdown();http.server_close();thread.join()

def test_server_size_limit_removes_whole_earliest_round(tmp_path):
    fs=frames(tmp_path,[0,0,1,1,2])
    with server(limit=3)as(base,accepted,attempts):
        tr=Transport('test-only-key',base,'fake',tmp_path,{'request_attempts':2});tr.set_feed({'round':3,'frames':fs})
        _,_,meta=tr.request({'messages':[{'role':'user','content':'keep all text'}]},'/v1/chat/completions')
    assert len(attempts)==2 and len(accepted)==1
    assert [f['round']for f in tr.selection.kept]==[1,1,2]
    assert len(tr.selection.drops[0]['removed'])==2
    assert accepted[0]['messages'][0]['content']=='keep all text'
    assert tr.last_delivery['images'][-1]['file']==fs[-1]['file']
    assert meta['text']=='{"actions":["left"]}'

def test_single_oversized_batch_keeps_latest_view(tmp_path):
    fs=frames(tmp_path,[7]*5);one=fs[0]
    limit=2*4*((__import__('pathlib').Path(one['path']).stat().st_size+2)//3)+2500
    with server(limit=99)as(base,accepted,attempts):
        tr=Transport('test-only-key',base,'fake',tmp_path,{'max_body_bytes':limit});tr.set_feed({'round':8,'frames':fs})
        tr.request({'messages':[]},'/v1/chat/completions')
    assert len(attempts)==1 and len(tr.selection.kept)==1
    assert tr.selection.kept[0]['file']==fs[-1]['file']
    assert tr.calls[0]['body_bytes']<=limit
    assert len(tr.selection.drops[0]['removed'])==4

def test_transient_retry_does_not_drop_images_immediately(tmp_path,monkeypatch):
    monkeypatch.setattr('maze_bench.transport.time.sleep',lambda _:None)
    fs=frames(tmp_path,[0,0])
    with server(limit=99,transient=1)as(base,accepted,attempts):
        tr=Transport('test-only-key',base,'fake',tmp_path,{});tr.set_feed({'round':1,'frames':fs});tr.request({'messages':[]},'/v1/chat/completions')
    assert len(attempts)==2 and not tr.selection.drops
    assert len(tr.last_delivery['images'])==2

@pytest.mark.parametrize('first_reply',['rate_limit_event','empty_native_body'])
def test_native_retries_http200_transport_failures_before_delivering_reply(tmp_path,monkeypatch,first_reply):
    monkeypatch.setattr('maze_bench.transport.time.sleep',lambda _:None)
    attempts=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            attempts.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            event={'type':'response.failed','response':{'status':'failed','error':{'code':'rate_limit_reached','message':'Provisioned throughput exceeded'}}}if len(attempts)==1 else{'type':'response.completed','response':{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"actions":["left"]}'}]}]}}
            raw=b''if len(attempts)==1 and first_reply=='empty_native_body'else('data: '+json.dumps(event)+'\n\n').encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    tr=Transport('test-only-key',f'http://127.0.0.1:{http.server_port}','fake',tmp_path,{'harness':'codex','request_attempts':2})
    tr.set_feed({'round':7,'frames':frames(tmp_path,[6,6])})
    try:
        _,_,meta=tr.request({'input':[]},'/v1/responses')
        assert len(attempts)==2 and attempts[0]==attempts[1]
        assert meta['text']=='{"actions":["left"]}'and tr.last_delivery['request_index']==2
        assert tr.calls[0]['public_text_chars']==0 and tr.calls[0]['error']
        assert tr.last_delivery['round']==7 and not tr.selection.drops
    finally:http.shutdown();http.server_close();thread.join()

def test_base_url_preserves_gateway_prefix():
    assert endpoint('https://host/proxy/gateway/v1','/v1/responses')=='https://host/proxy/gateway/v1/responses'
    assert endpoint('https://host/proxy/gateway/','/v1/messages')=='https://host/proxy/gateway/v1/messages'

def test_agy_main_planning_without_advertised_tools_still_receives_image(tmp_path):
    import urllib.request
    with server(limit=99)as(base,accepted,attempts):
        tr=Transport('test-only-key',base,'fake',tmp_path,{'harness':'agy'});tr.set_feed({'round':1,'frames':frames(tmp_path,[0])});proxy=Proxy(tr,'agy')
        try:
            body={'messages':[{'role':'user','content':'MAZE_PLANNING_REQUEST 1/200. Inspect current images.'}],'tools':[]}
            request=urllib.request.Request(proxy.base+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=5)as response:response.read()
            assert tr.last_delivery['round']==1 and len(tr.last_delivery['images'])==1
            assert any(b.get('type')=='image_url'for m in accepted[0]['messages']for b in m['content']if isinstance(b,dict))
        finally:proxy.close()

def test_agy_title_with_planning_transcript_has_separate_session_and_no_observation(tmp_path):
    import urllib.request
    from maze_bench.settings import upstream_session_key
    cfg={'harness':'agy','isolate_upstream_session':True,'run_id':'fixture-run','case_id':'case-a'}
    with server(limit=99)as(base,accepted,attempts):
        tr=Transport('test-only-key',base,'fake',tmp_path,cfg);tr.set_feed({'round':1,'frames':frames(tmp_path,[0])});proxy=Proxy(tr,'agy')
        try:
            user={'role':'user','content':'MAZE_PLANNING_REQUEST 1/200. Inspect current images.'}
            title={'messages':[{'role':'system','content':'You are a conversation title generator. Respond with ONLY a short conversation title.'},user],'tools':[]}
            primary={'messages':[user],'tools':[]}
            for body in(title,primary):
                req=urllib.request.Request(proxy.base+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=5)as response:response.read()
            assert tr.calls[0]['main_inference']is False and not tr.calls[0]['images']
            assert tr.calls[1]['main_inference']is True and len(tr.calls[1]['images'])==1
            assert accepted[0]['prompt_cache_key']==upstream_session_key(cfg,'title')
            assert accepted[1]['prompt_cache_key']==upstream_session_key(cfg)
            assert tr.last_delivery['request_index']==2
            assert upstream_session_key(cfg)!=upstream_session_key({**cfg,'case_id':'case-b'})
            assert upstream_session_key(cfg)!=upstream_session_key({**cfg,'run_id':'other-run'})
        finally:proxy.close()

def test_truncated_reasoning_stream_is_not_a_completed_reply():
    stream=b'data: {"type":"response.created","response":{"status":"in_progress"}}\n\ndata: {"type":"response.reasoning_summary_text.delta","delta":"private fixture"}\n\n'
    meta=metadata_response(stream)
    assert meta['is_stream']and not meta['terminal']and not meta['text']

def test_completed_responses_accepts_null_incomplete_details():
    stream=b'data: {"type":"response.completed","response":{"status":"completed","incomplete_details":null,"output":[{"type":"message","content":[{"type":"output_text","text":"{\\"actions\\":[\\"left\\"]}"}]}]}}\n\n'
    meta=metadata_response(stream)
    assert meta['terminal']and meta['finish_status']=='completed'
    assert meta['text']=='{"actions":["left"]}'

def test_seed_messages_compatibility_removes_empty_containers_without_changing_public_history(tmp_path):
    import copy
    tr=Transport('test-only-key','http://127.0.0.1:1','Seed2.1-Pro',tmp_path,{'harness':'claude'})
    tr.set_feed({'round':2,'frames':frames(tmp_path,[1])})
    payload={'messages':[{'role':'user','content':[{'type':'image','source':{'type':'base64','data':'old-image'}}]},{'role':'assistant','content':[{'type':'thinking','thinking':'synthetic-private-block'}]},{'role':'assistant','content':[{'type':'text','text':'{"actions":["forward"]}'}]},{'role':'user','content':[{'type':'text','text':'Round2 feedback'}]}]}
    original=copy.deepcopy(payload);out=json.loads(tr._body(payload,'/v1/messages',True))
    assert payload==original
    assert all(m.get('content')!=[]for m in out['messages'])
    assert out['messages'][0]=={'role':'assistant','content':original['messages'][2]['content'][0]['text']}
    assert out['messages'][1]==original['messages'][3]
    assert out['messages'][-1]['content'][-1]['type']=='image'

def test_public_text_at_block_start_is_retained():
    stream=b'data:{"type":"content_block_start","content_block":{"type":"text","text":"hello"}}\n\ndata:{"type":"message_stop"}\n\n'
    meta=metadata_response(stream)
    assert meta['text']=='hello'and meta['terminal']

def test_heartbeats_cannot_extend_total_planning_deadline(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            try:
                for _ in range(50):
                    self.wfile.write(b': heartbeat\n\n');self.wfile.flush();time.sleep(.03)
            except OSError:pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.daemon_threads=True
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    tr=Transport('test-only-key',f'http://127.0.0.1:{http.server_port}','fake',tmp_path,{'planning_timeout':.18,'request_timeout':10,'request_attempts':5})
    tr.set_feed({'round':1,'frames':frames(tmp_path,[0])});start=time.monotonic()
    try:
        import pytest
        with pytest.raises(TimeoutError):tr.request({'messages':[]},'/v1/chat/completions')
        assert time.monotonic()-start<1 and len(tr.calls)==1
        assert not tr.last_delivery
    finally:http.shutdown();http.server_close();thread.join()

def test_empty_completed_stream_is_retried_without_accepting_plan(tmp_path,monkeypatch):
    monkeypatch.setattr('maze_bench.transport.time.sleep',lambda _:None)
    class Handler(BaseHTTPRequestHandler):
        calls=0
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']));Handler.calls+=1
            event={'type':'message_stop'}if Handler.calls==1 else{'choices':[{'message':{'content':'{"actions":["left"]}'}}]}
            raw=json.dumps(event).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    tr=Transport('test-only-key',f'http://127.0.0.1:{http.server_port}','fake',tmp_path,{})
    tr.set_feed({'round':1,'frames':frames(tmp_path,[0])})
    try:
        _,_,meta=tr.request({'messages':[]},'/v1/chat/completions')
        assert Handler.calls==2 and meta['text']=='{"actions":["left"]}'
        assert 'empty_public_response'in tr.calls[0]['error']and tr.last_delivery['request_index']==2
    finally:http.shutdown();http.server_close();thread.join()

def test_reasoning_consumed_budget_grows_cap_and_remembers_it(tmp_path):
    requested=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requested.append(body['max_tokens'])
            if body['max_tokens']==32000:
                raw=b'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}\n\ndata: {"type":"message_stop"}\n\n'
            else:
                raw=b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"{\\"actions\\":[\\"left\\"]}"}}\n\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\ndata: {"type":"message_stop"}\n\n'
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{http.server_port}';payload={'max_tokens':32000,'messages':[]};cfg={'request_attempts':1,'max_output_budget':131072}
    try:
        tr=Transport('test-only-key',base,'fake',tmp_path,cfg);tr.set_feed({'round':1,'frames':frames(tmp_path,[0])})
        _,_,meta=tr.request(payload,'/v1/messages')
        assert meta['text']=='{"actions":["left"]}'and requested==[32000,64000]
        assert tr.calls[0]['next_output_cap']=={'max_tokens':64000}and tr.calls[1]['requested_settings']['max_tokens']==64000
        assert payload['max_tokens']==32000 and not tr.selection.drops
        resumed=Transport('test-only-key',base,'fake',tmp_path,cfg);resumed.set_feed({'round':2,'frames':frames(tmp_path,[1])});resumed.request(payload,'/v1/messages')
        assert requested==[32000,64000,64000]
    finally:http.shutdown();http.server_close();thread.join()
