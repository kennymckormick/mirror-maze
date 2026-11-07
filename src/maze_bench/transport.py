"""Shared API/native transport. Prune whole observation rounds, pin newest view."""
import base64,collections,copy,gzip,http.client,http.server,json,struct,threading,time,urllib.parse,urllib.request,urllib.error,uuid
from pathlib import Path
from .common import dump,endpoint,sha
from .settings import upstream_session_key

IMAGE_TYPES={'image','image_url','input_image'}

def remove_images(value):
    if isinstance(value,list):
        value[:]=[v for v in value if not(isinstance(v,dict)and v.get('type')in IMAGE_TYPES)]
        for v in value:remove_images(v)
    elif isinstance(value,dict):
        for v in value.values():remove_images(v)

def frame_metadata(frame):
    raw=Path(frame['path']).read_bytes()
    if raw[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('Expected original PNG observation')
    w,h=struct.unpack('>II',raw[16:24])
    if(w,h)!=(1280,720):raise ValueError('Observation must be 1280x720')
    return {'file':frame.get('file',Path(frame['path']).name),'sha256':sha(frame['path']),'bytes':len(raw),'width':w,'height':h,'round':frame['round'],'index':frame['index']}

class Selection:
    def __init__(self,frames):
        if not frames:raise ValueError('Current observation missing')
        self.original=list(frames);self.kept=list(frames);self.drops=[]
        self.pinned=(frames[-1]['round'],frames[-1]['index'])
    def drop_oldest_round(self,cause):
        candidates=sorted({f['round']for f in self.kept if(f['round'],f['index'])!=self.pinned})
        if not candidates:return False
        n=candidates[0];removed=[f for f in self.kept if f['round']==n and(f['round'],f['index'])!=self.pinned]
        self.kept=[f for f in self.kept if f not in removed]
        self.drops.append({'round':n,'cause':cause,'removed':[{'file':f.get('file',Path(f['path']).name),'index':f['index']}for f in removed],'latest_view_pinned':True})
        return True

def metadata_response(data):
    events=[];is_stream=False;done_marker=False
    for line in data.decode(errors='replace').splitlines():
        if line.startswith('data:'):
            is_stream=True
            if line[5:].strip()=='[DONE]':done_marker=True
            try:events.append(json.loads(line[5:].strip()))
            except ValueError:pass
        elif is_stream and line.lstrip().startswith('{'):
            # Some gateways append an unframed JSON error after SSE chunks.
            # It remains a provider failure even when earlier text is JSON.
            try:tail=json.loads(line)
            except ValueError:continue
            if isinstance(tail,dict)and tail.get('error'):events.append(tail)
    if not events:
        try:events=[json.loads(data)]
        except ValueError:events=[]
    models=[];usage=[];error=None;pieces=[];fallback=[];terminal=done_marker or not is_stream;finish=None
    for e in events:
        if not isinstance(e,dict):continue
        response=e.get('response')if isinstance(e.get('response'),dict)else{}
        message=e.get('message')if isinstance(e.get('message'),dict)else{}
        model=e.get('model')or response.get('model')or message.get('model')
        if model and model not in models:models.append(model)
        for obj in(e,response,message):
            if isinstance(obj.get('usage'),dict):usage.append(obj['usage'])
        if e.get('error'):error=e['error']
        if e.get('stop_reason'):finish=e['stop_reason']
        if e.get('type')in('response.completed','response.incomplete','response.failed','message_stop'):terminal=True
        if response.get('status'):finish=(response.get('incomplete_details')or{}).get('reason')or response.get('status')
        if e.get('type')=='response.failed':error=response.get('error')or response.get('status')
        if e.get('type')=='response.output_text.delta':pieces.append(e.get('delta',''))
        if e.get('type')=='content_block_start'and e.get('content_block',{}).get('type')=='text':pieces.append(e['content_block'].get('text',''))
        if e.get('type')=='content_block_delta'and e.get('delta',{}).get('type')!='thinking_delta':pieces.append(e['delta'].get('text',''))
        if e.get('type')=='message_delta':finish=e.get('delta',{}).get('stop_reason',finish)
        for c in e.get('choices',[]):
            if c.get('finish_reason'):finish=c['finish_reason']
            delta=c.get('delta',{}).get('content')
            if isinstance(delta,str):pieces.append(delta)
            msg=c.get('message',{}).get('content')
            if isinstance(msg,str):fallback.append(msg)
        content=e.get('content',[])
        if isinstance(content,list):fallback.extend(b.get('text','')for b in content if isinstance(b,dict)and b.get('type')=='text')
        for out in response.get('output',e.get('output',[])):
            if out.get('type')=='message':fallback.extend(b.get('text','')for b in out.get('content',[])if b.get('type')=='output_text')
    return {'reported_models':models,'usage':usage,'response_error':error,'text':''.join(pieces)or ''.join(fallback),'is_stream':is_stream,'terminal':terminal,'finish_status':finish}


def complete_json_document(text):
    """Check syntax only: illegal actions must still receive their penalty."""
    value=(text or '').strip()
    if value.startswith('```'):
        import re
        match=re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```',value)
        if not match:return False
        value=match.group(1)
    def reject_constant(value):raise ValueError('Non-JSON constant')
    try:json.loads(value,parse_constant=reject_constant);return True
    except(ValueError,TypeError):return False


def close_chat_stream(data):
    """Add a client-local end marker; original provider bytes stay archived."""
    if metadata_response(data)['response_error']:raise ValueError('A provider error cannot be normalized as a completed reply')
    previous={}
    for line in data.decode(errors='replace').splitlines():
        if line.startswith('data:'):
            try:value=json.loads(line[5:])
            except ValueError:continue
            if isinstance(value,dict)and value.get('choices'):previous=value
    final={k:previous[k]for k in('id','created','model','system_fingerprint')if k in previous}
    final.update(object='chat.completion.chunk',choices=[{'index':0,'delta':{},'finish_reason':'stop'}])
    return data.rstrip(b'\r\n')+b'\n\ndata: '+json.dumps(final).encode()+b'\n\ndata: [DONE]\n\n'


def closed_json_evidence(call,case,text):
    return {'rule':'single_complete_json_document_at_observed_eof','source_wire_index':call['index'],
            'provider_terminal_received':False,'public_text_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),
            'private_response_sha256':sha(Path(case)/call['private_response_file'])}


def agy_title_request(incoming):
    system=json.dumps([m.get('content')for m in incoming.get('messages',[])if m.get('role')=='system'],ensure_ascii=False).casefold()
    return 'you are a conversation title generator'in system and 'short conversation title'in system


def message_sse(data):
    """Serialize one complete Anthropic message as the requested SSE events."""
    message=json.loads(data);assert message.get('type')=='message'and message.get('role')=='assistant'
    assert message.get('stop_reason')in('end_turn','stop_sequence','tool_use','max_tokens','refusal')
    events=[]
    def emit(kind,**fields):events.append({'type':kind,**fields})
    initial={**message,'content':[],'stop_reason':None,'stop_sequence':None,'usage':{**message.get('usage',{}),'output_tokens':0}}
    emit('message_start',message=initial)
    for index,block in enumerate(message.get('content',[])):
        kind=block['type']
        if kind=='text':
            emit('content_block_start',index=index,content_block={'type':'text','text':''})
            emit('content_block_delta',index=index,delta={'type':'text_delta','text':block.get('text','')})
        elif kind=='thinking':
            emit('content_block_start',index=index,content_block={'type':'thinking','thinking':'','signature':''})
            emit('content_block_delta',index=index,delta={'type':'thinking_delta','thinking':block.get('thinking','')})
            if block.get('signature'):emit('content_block_delta',index=index,delta={'type':'signature_delta','signature':block['signature']})
        elif kind=='tool_use':
            emit('content_block_start',index=index,content_block={**block,'input':{}})
            emit('content_block_delta',index=index,delta={'type':'input_json_delta','partial_json':json.dumps(block.get('input',{}),ensure_ascii=False)})
        else:emit('content_block_start',index=index,content_block=block)
        emit('content_block_stop',index=index)
    emit('message_delta',delta={'stop_reason':message['stop_reason'],'stop_sequence':message.get('stop_sequence')},usage={'output_tokens':message.get('usage',{}).get('output_tokens',0)})
    emit('message_stop')
    return ''.join('event: '+e['type']+'\ndata: '+json.dumps(e,ensure_ascii=False)+'\n\n'for e in events).encode()

class Transport:
    def __init__(self,key,base_url,model,case,cfg):
        self.key=key;self.base_url=base_url;self.model=model;self.case=Path(case);self.cfg=cfg
        self.log=self.case/'wire.json';self.calls=json.loads(self.log.read_text())if self.log.exists()else[]
        self.lock=threading.RLock();self.selection=None;self.round=None;self.last_delivery=None;self.deadline=None
        self.output_caps={}
        for c in self.calls:
            if c.get('next_output_cap'):self.output_caps.update(c['next_output_cap'])
            if c.get('stream_terminal')and c.get('finish_status')in('max_tokens','max_output_tokens','length')and c.get('public_text_chars')==0:
                for field,value in c.get('requested_settings',{}).items():
                    if field in('max_tokens','max_output_tokens','max_completion_tokens')and isinstance(value,int):self.output_caps[field]=max(self.output_caps.get(field,0),min(self.cfg.get('max_output_budget',131072),value*2))
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.native_replay=cfg.get('_native_wire_replay')
    def set_feed(self,feed):
        self.deadline=time.monotonic()+self.cfg.get('planning_timeout',3600)
        self.round=feed['round'];frames=[]
        for i,f in enumerate(feed['frames']):frames.append(dict(f,round=f.get('round',self.round-1),index=f.get('index',i)))
        self.selection=Selection(frames)
        if feed.get('prune_previous_timeout')and len(frames)>1:self.selection.drop_oldest_round('previous_repeated_upstream_timeout')
    def remaining(self):
        remaining=(self.deadline-time.monotonic())if self.deadline is not None else self.cfg.get('planning_timeout',3600)
        if remaining<=0:raise TimeoutError('planning_round_deadline_exceeded')
        return remaining
    def read_response(self,resp):
        chunks=[]
        while True:
            remaining=self.remaining()
            # Refresh the socket deadline on every chunk; heartbeat traffic must
            # not extend the total planning budget indefinitely.
            sock=getattr(getattr(getattr(resp,'fp',None),'raw',None),'_sock',None)
            if sock is not None:sock.settimeout(min(remaining,self.cfg.get('request_timeout',3600)))
            chunk=resp.read1(65536)if hasattr(resp,'read1')else resp.read(65536)
            if not chunk:break
            chunks.append(chunk)
        return b''.join(chunks)
    def _body(self,incoming,path,main):
        p=copy.deepcopy(incoming);p['model']=self.model
        if self.cfg.get('isolate_upstream_session'):
            p['prompt_cache_key']=upstream_session_key(self.cfg,'main'if main else'title'if agy_title_request(incoming)else'aux')
        if main and not self.cfg.get('harness'):
            for field,value in self.output_caps.items():
                if field in p:p[field]=max(p[field],value)
        if main:
            remove_images(p)
            limit=self.cfg.get('max_body_bytes',120_000_000);max_images=self.cfg.get('max_images')
            # Count Base64 bytes before constructing very large bodies.
            while True:
                estimate=len(json.dumps(p,ensure_ascii=False).encode())+sum(4*((Path(f['path']).stat().st_size+2)//3)+len(f['label'].encode())+512 for f in self.selection.kept)
                if estimate<=limit and(not max_images or len(self.selection.kept)<=max_images):break
                if not self.selection.drop_oldest_round('preflight_body_limit'if estimate>limit else'preflight_image_count_limit'):raise RuntimeError('Pinned current view and text cannot fit request limit')
            while True:
                body=copy.deepcopy(p);blocks=[];n=len(self.selection.original);kept=len(self.selection.kept)
                notice=f'Current observation batch: {kept}/{n} image(s), in chronological order. The LAST is the current first-person state.'
                if self.selection.drops:notice+=' Earlier observation rounds were removed for transport limits; the latest current-state image is retained. Text feedback is unchanged. Removed images do not count as collisions.'
                text_type='input_text'if path=='/v1/responses'else'text';blocks.append({'type':text_type,'text':notice})
                for f in self.selection.kept:
                    b64=base64.b64encode(Path(f['path']).read_bytes()).decode();blocks.append({'type':text_type,'text':f['label']})
                    if path=='/v1/messages':blocks.append({'type':'image','source':{'type':'base64','media_type':'image/png','data':b64}})
                    elif path=='/v1/responses':blocks.append({'type':'input_image','image_url':'data:image/png;base64,'+b64})
                    else:blocks.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+b64}})
                body.setdefault('input'if path=='/v1/responses'else'messages',[]).append({'type':'message','role':'user','content':blocks}if path=='/v1/responses'else{'role':'user','content':blocks})
                encoded=json.dumps(body,ensure_ascii=False).encode()
                if len(encoded)<=limit:break
                if not self.selection.drop_oldest_round('exact_body_limit'):raise RuntimeError('Pinned current view and text cannot fit request limit')
        else:body=p;encoded=json.dumps(body,ensure_ascii=False).encode()
        # Native finish tools are client operations, not callable upstream tools.
        if self.model in self.cfg.get('text_tool_compatibility_models',['MC-ModelHub-Gemini-4-Argon','Seed2.1-Pro']):
            for m in body.get('messages',[]):
                if m.get('role')=='tool':m['role']='user';m.pop('tool_call_id',None);m.pop('name',None)
                if m.get('role')=='assistant'and m.get('tool_calls'):
                    texts=[]
                    for call in m.pop('tool_calls',[]):
                        fun=call.get('function',{})
                        if fun.get('name')=='finish':
                            try:texts.extend(v for k,v in json.loads(fun.get('arguments','{}')).items()if k in('response','text','message','answer')and isinstance(v,str))
                            except ValueError:pass
                    m['content']=m.get('content')or'\n'.join(texts)or'Completed a client-side tool operation.'
                if path=='/v1/messages'and isinstance(m.get('content'),list):
                    m['content']=[b for b in m['content']if b.get('type')in('text','image')]
            if path=='/v1/messages':
                body['messages']=[m for m in body.get('messages',[])if not(isinstance(m.get('content'),list)and not m['content'])]
                for m in body['messages']:
                    if m.get('role')=='assistant'and isinstance(m.get('content'),list)and all(b.get('type')=='text'for b in m['content']):
                        m['content']=''.join(b.get('text','')for b in m['content'])
        encoded=json.dumps(body,ensure_ascii=False).encode()
        return encoded
    def request(self,incoming,path,main=True,extra_headers=None):
        if main and self.native_replay:
            proof=self.native_replay;self.native_replay=None
            call=next(c for c in self.calls if c['index']==proof['wire_index'])
            assert path==call['path']and call['observation_round']==self.round==proof['round']
            source=self.case/call['private_response_file'];assert sha(source)==proof['private_response_sha256']
            with gzip.open(source,'rb')as f:data=f.read()
            meta=metadata_response(data);assert meta['text']and not meta['response_error']
            self.selection.kept=[self.selection.original[i['index']]for i in call['images']]
            assert [f['file']for f in self.selection.kept]==[i['file']for i in call['images']]
            self.selection.drops=call.get('trim_events',[])
            self.last_delivery={'round':self.round,'request_index':call['index'],'images':call['images'],'trim_events':self.selection.drops,'original_frames':len(self.selection.original)}
            if proof.get('closed_json_stream_recovery'):
                assert complete_json_document(meta['text'])
                meta['closed_json_stream_recovery']=proof['closed_json_stream_recovery']
                self.last_delivery['closed_json_stream_recovery']=proof['closed_json_stream_recovery']
            dump(self.case/'native-wire-replay.json',{'round':self.round,'wire_index':call['index'],'original_provider_bytes_sha256':sha(source),'no_new_inference':True,'at':time.time()})
            return 200,data,meta
        transient=0;max_attempts=self.cfg.get('request_attempts',5)
        # A size rejection must be allowed to prune every available old round.
        max_attempts+=len({f['round']for f in self.selection.kept})if main else 0
        if main and not self.cfg.get('harness'):max_attempts+=3
        for attempt in range(max_attempts):
            body=self._body(incoming,path,main);record={'index':len(self.calls)+1,'observation_round':self.round,'source_alias':self.model,'path':path,'started_at':time.time(),'body_bytes':len(body),'images':[frame_metadata(f)for f in self.selection.kept]if main else[],'trim_events':copy.deepcopy(self.selection.drops)if main else[],'main_inference':main}
            if self.cfg.get('isolate_upstream_session'):record['upstream_session_key']=upstream_session_key(self.cfg,'main'if main else'title'if agy_title_request(incoming)else'aux')
            if self.cfg.get('request_structure_diagnostic_round')==self.round:
                shaped=json.loads(body)
                record['request_structure']={'top_level_keys':list(shaped),'messages':[{'role':m.get('role'),'content_kind':type(m.get('content')).__name__,'block_types':[b.get('type')for b in m.get('content',[])if isinstance(b,dict)]if isinstance(m.get('content'),list)else None,'empty_content':m.get('content')in([],None,'')}for m in shaped.get('messages',[])]}
            with self.lock:self.calls.append(record);dump(self.log,self.calls)
            if hasattr(self,'record_request_body'):self.record_request_body(record,body)
            headers={k:v for k,v in(extra_headers or{}).items()if k.lower()not in('authorization','x-api-key','host','connection','content-length','accept-encoding')}
            headers.update({'Authorization':'Bearer '+self.key,'Content-Type':'application/json','Accept-Encoding':'identity'})
            if path=='/v1/messages':headers.update({'x-api-key':self.key,'anthropic-version':'2023-06-01'})
            status=None;data=b'';transport_error=None
            try:
                req=urllib.request.Request(endpoint(self.base_url,path),data=body,headers=headers)
                try:
                    with self.opener.open(req,timeout=min(self.remaining(),self.cfg.get('request_timeout',3600)))as resp:status=resp.status;data=self.read_response(resp)
                except urllib.error.HTTPError as exc:status=exc.code;data=self.read_response(exc)
            except Exception as exc:transport_error=(type(exc).__name__+': '+str(exc)).replace(self.key,'[redacted]')
            if data and self.cfg.get('retain_private_streams',True):
                private=self.case/'private';private.mkdir(exist_ok=True)
                name=f'upstream-{record["index"]:06d}-{uuid.uuid4().hex}.sse.gz'
                with gzip.open(private/name,'wb',compresslevel=3)as f:f.write(data)
                record['private_response_file']='private/'+name
            meta=metadata_response(data)
            if status==200 and meta['is_stream']and not meta['terminal']and not meta['response_error']:
                if path=='/v1/chat/completions'and self.cfg.get('recover_closed_json_streams')and record.get('private_response_file')and complete_json_document(meta['text'])and not transport_error:
                    meta['closed_json_stream_recovery']=closed_json_evidence(record,self.case,meta['text'])
                    record['closed_json_stream_recovery']=meta['closed_json_stream_recovery']
                else:meta['response_error']={'type':'truncated_stream','message':'Provider stream ended without a terminal event'}
            if status==200 and main and(not self.cfg.get('harness')or not data)and not meta['text']and not meta['response_error']:meta['response_error']={'type':'empty_public_response','message':'Provider returned no public answer; no maze action was executed'}
            error=(str(meta['response_error'])if meta['response_error']else data.decode(errors='replace')if status!=200 else'')
            record.update(http_status=status,seconds=time.time()-record['started_at'],response_bytes=len(data),reported_models=meta['reported_models'],usage=meta['usage'],stream_terminal=meta['terminal'],finish_status=meta['finish_status'],public_text_chars=len(meta['text']),planning_timeout=self.cfg.get('planning_timeout',3600),request_timeout=self.cfg.get('request_timeout',3600))
            record['requested_settings']={k:max(incoming[k],self.output_caps.get(k,0))if k in('max_tokens','max_output_tokens','max_completion_tokens')and main and not self.cfg.get('harness')else incoming[k]for k in('max_tokens','max_output_tokens','max_completion_tokens','thinking','reasoning','reasoning_effort','output_config')if k in incoming}
            if error:record['error']=error.replace(self.key,'[redacted]')[:1800]
            if transport_error:record['transport_error']=transport_error
            with self.lock:dump(self.log,self.calls)
            if status==200 and main and not self.cfg.get('harness')and not transport_error and meta['terminal']and not meta['text']and meta['finish_status']in('max_tokens','max_output_tokens','length'):
                field=next((k for k in('max_tokens','max_output_tokens','max_completion_tokens')if k in record['requested_settings']),None)
                cap=record['requested_settings'].get(field,0);maximum=self.cfg.get('max_output_budget',131072)
                if field and cap<maximum:
                    self.output_caps[field]=min(maximum,cap*2);record['retry_reason']='output_budget_exhausted_before_public_answer';record['next_output_cap']={field:self.output_caps[field]}
                    with self.lock:dump(self.log,self.calls)
                    continue
            if status==200 and not meta['response_error']and not transport_error:
                if main:self.last_delivery={'round':self.round,'request_index':record['index'],'images':record['images'],'trim_events':record['trim_events'],'original_frames':len(self.selection.original)}
                if main and meta.get('closed_json_stream_recovery'):self.last_delivery['closed_json_stream_recovery']=meta['closed_json_stream_recovery']
                return status,data,meta
            lower=(error+' '+str(transport_error)).lower();size_error=status==413 or any(s in lower for s in ['context length','context_length','too many images','maximum number of images','request body exceeds','request too large','prompt is too long'])
            if status==429 and 'individual quota reached'in lower:
                # Account quota is independent of image size. Preserve the
                # observation batch and wait before another source request.
                dump(self.case/'retry-after.json',{'not_before':time.time()+3600,'source_alias':self.model,'observation_round':self.round,'reason':'upstream_account_quota_exhausted','message':'Provider account quota reached; retry the same source after backoff.','recorded_at':time.time()})
                raise RuntimeError('upstream_account_quota_exhausted; original checkpoint retained, retry deferred for one hour')
            if main and size_error and self.selection.drop_oldest_round('upstream_size_or_context_limit'):continue
            if status in (None,408,429,500,502,503,504)or any(s in lower for s in ['rate limit','rate_limit','throughput limit','timeout','upstream_unavailable','truncated_stream','incompleteread','empty_public_response']):
                transient+=1
                if main and transient>=2 and len(self.selection.kept)>1:self.selection.drop_oldest_round('repeated_upstream_timeout');transient=0
                if attempt+1<max_attempts:
                    wait=max(2**(attempt+1),self.cfg.get('rate_limit_backoff_seconds',0))if status==429 else min(20,2**(attempt+1))
                    time.sleep(min(wait,self.remaining()));continue
            raise RuntimeError(('API request failed: '+str(status)+' '+(error or str(transport_error))[:500]).replace(self.key,'[redacted]'))
        raise RuntimeError('API retry budget exhausted')

class Proxy:
    def __init__(self,transport,harness):
        self.transport=transport;self.calls=transport.calls;self.log=transport.log;owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_GET(self):
                raw=json.dumps({'object':'list','data':[{'id':transport.model,'object':'model'}]}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_POST(self):
                path=urllib.parse.urlparse(self.path).path
                if 'v1beta'in path:self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}');return
                if path not in('/v1/messages','/v1/chat/completions','/v1/responses'):self.send_error(404);return
                incoming=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                users=[m for m in incoming.get('messages',[])if m.get('role')=='user']
                latest_user=json.dumps(users[-1].get('content',''),ensure_ascii=False)if users else''
                planning_tag='MAZE_PLANNING_REQUEST '+str(transport.round)+'/'
                main=harness!='agy'or(not agy_title_request(incoming)and(bool(incoming.get('tools'))or planning_tag in latest_user));stream=bool(incoming.get('stream'))
                done=threading.Event();write_lock=threading.Lock()
                if stream:
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers();self.close_connection=True
                    def heartbeat():
                        while not done.is_set():
                            try:
                                with write_lock:self.wfile.write(b': waiting for upstream\n\n');self.wfile.flush()
                            except OSError:break
                            done.wait(15)
                    threading.Thread(target=heartbeat,daemon=True).start()
                try:
                    status,data,meta=transport.request(incoming,path,main,dict(self.headers))
                    if stream and path=='/v1/chat/completions'and meta.get('closed_json_stream_recovery'):data=close_chat_stream(data)
                    if stream and path=='/v1/messages'and not meta['is_stream']:data=message_sse(data)
                    if not stream:self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers()
                    with write_lock:self.wfile.write(data);self.wfile.flush()
                except Exception as exc:
                    msg=str(exc).replace(transport.key,'[redacted]')
                    dump(transport.case/'proxy-last-error.json',{'type':type(exc).__name__,'message':msg,'round':transport.round,'main_inference':main,'path':path,'at':time.time()})
                    if stream:
                        with write_lock:self.wfile.write(('data: '+json.dumps({'error':{'message':msg,'type':'upstream_error','code':502}})+'\n\ndata: [DONE]\n\n').encode());self.wfile.flush()
                    else:self.send_error(502,msg[:400])
                finally:done.set()
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base=f'http://127.0.0.1:{self.server.server_port}'
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2)
