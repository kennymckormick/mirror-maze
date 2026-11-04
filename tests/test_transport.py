import json,threading,time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from PIL import Image
from maze_bench.transport import Transport,metadata_response
from maze_bench.common import endpoint

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

def test_base_url_preserves_gateway_prefix():
    assert endpoint('https://host/proxy/gateway/v1','/v1/responses')=='https://host/proxy/gateway/v1/responses'
    assert endpoint('https://host/proxy/gateway/','/v1/messages')=='https://host/proxy/gateway/v1/messages'

def test_truncated_reasoning_stream_is_not_a_completed_reply():
    stream=b'data: {"type":"response.created","response":{"status":"in_progress"}}\n\ndata: {"type":"response.reasoning_summary_text.delta","delta":"private fixture"}\n\n'
    meta=metadata_response(stream)
    assert meta['is_stream']and not meta['terminal']and not meta['text']

def test_completed_responses_accepts_null_incomplete_details():
    stream=b'data: {"type":"response.completed","response":{"status":"completed","incomplete_details":null,"output":[{"type":"message","content":[{"type":"output_text","text":"{\\"actions\\":[\\"left\\"]}"}]}]}}\n\n'
    meta=metadata_response(stream)
    assert meta['terminal']and meta['finish_status']=='completed'
    assert meta['text']=='{"actions":["left"]}'

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
