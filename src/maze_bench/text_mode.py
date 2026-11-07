"""Native-harness navigation with only ordered per-action text feedback."""
import copy,gzip,hashlib,json,re,threading,time,uuid
from pathlib import Path
from mirror_maze import MirrorMaze
from .common import dump,sha,parse_plan
from .settings import environment_options,geometry,upstream_session_key
from .transport import Transport,Proxy,IMAGE_TYPES,metadata_response
from .native import Native
from .agy_native import AgyNative,AgyObservation
from .agy_protocol import completed_agy_reply,ForbiddenAgyToolError

PROTOCOL='text-action-feedback-native-v1'
COMPACT_HEADER='This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.\n\nSummary:'
OUTPUT_LIMIT_CONTINUE='Output token limit hit. Resume directly — no apology, no recap of what you were doing. Pick up mid-thought if that is where the cut happened. Break remaining work into smaller pieces.'

class TextSourceError(RuntimeError):
    def __init__(self,kind,retry_after=None):
        self.kind,self.retry_after=kind,retry_after;super().__init__(kind)

def source_fault(text):
    if not isinstance(text,str)or not text.strip():return TextSourceError('empty_text_harness_reply')
    value=text.strip()
    if re.match(r'^(?:⚠\ufe0f?\s*)?Individual\s+quota\s+reached\b',value,re.I):
        hours=re.search(r'(\d+)h',value);minutes=re.search(r'(\d+)m',value);seconds=re.search(r'(\d+)s',value)
        wait=sum(int(m[1])*factor for m,factor in [(hours,3600),(minutes,60),(seconds,1)]if m)
        return TextSourceError('native_provider_quota',min(86400,max(60,wait+30 if wait else 3600)))
    return None

def no_images(value):
    if isinstance(value,dict):
        if value.get('type')in IMAGE_TYPES:raise TextSourceError('text_observation_leakage_image_block')
        for v in value.values():no_images(v)
    elif isinstance(value,list):
        for v in value:no_images(v)
    elif isinstance(value,str)and value.startswith('data:image/'):raise TextSourceError('text_observation_leakage_image_data')

def user_texts(payload):
    messages=payload.get('messages',payload.get('input',[]))
    if isinstance(messages,str):return[messages]
    result=[]
    for message in messages:
        if not isinstance(message,dict)or message.get('role')!='user':continue
        value=message.get('content','')
        if isinstance(value,str):result.append(value)
        elif isinstance(value,list):result.append(''.join(b.get('text','')for b in value if isinstance(b,dict)and b.get('type')in('text','input_text')))
    return result

def has_host_prompt(payload,text):
    texts=user_texts(payload)
    # Native clients can surround the user message with their own reminders.
    # The exact host feedback still has to be present in the latest user turn.
    return bool(texts and text.strip()in texts[-1])

def native_continuation_parent(calls,case,n,text,payload,_depth=0):
    """Prove a native max-token continuation, including its unchanged output."""
    if _depth>=16:return None
    texts=user_texts(payload)
    if len(texts)<2 or not any(text.strip()in t for t in texts[:-1]):return None
    messages=payload.get('messages',[])
    assistants=[m.get('content')for m in messages if isinstance(m,dict)and m.get('role')=='assistant']
    if not assistants:return None
    last=assistants[-1];assistant=last if isinstance(last,str)else''.join(b.get('text','')for b in last if b.get('type')=='text')
    previous=[c for c in calls if c.get('observation_round')==n and c.get('http_status')==200 and not any(c.get(k)for k in('error','transport_error','provider_fault'))]
    if not previous:return None
    parent=previous[-1]
    with gzip.open(case/parent['private_response_file'],'rb')as f:meta=metadata_response(f.read())
    if meta['response_error']or not meta['terminal']or meta['finish_status']!='max_tokens'or not meta['text']or assistant!=meta['text']:return None
    prior=native_request_payload(parent,case)
    def uncached(value):
        if isinstance(value,dict):return{k:uncached(v)for k,v in value.items()if k!='cache_control'}
        if isinstance(value,list):return[uncached(v)for v in value]
        return value
    anchor=parent.get('main_inference')and has_host_prompt(prior,text)
    if not anchor and native_continuation_parent([c for c in calls if c['index']<parent['index']],case,n,text,prior,_depth+1)is None:return None
    if len(messages)!=len(prior.get('messages',[]))+2:return None
    if [m.get('role')for m in messages[-2:]]!=['assistant','user']or uncached(messages[:-2])!=uncached(prior['messages']):return None
    return parent['index']

def prompt(cfg,n,feedback):
    if n!=1:return json.dumps(feedback,separators=(',',':'))
    level,side=geometry(cfg);shape='square'if cfg['topology']=='square'else'hexagonal';degrees=90 if shape=='square'else 60
    return f'''Navigate an unknown maze to enter its hidden goal cell. The layout has a {shape} outer boundary and regular {shape} cells, with side length {side}. Your location, absolute heading, wall openings and goal location are unknown.
Action space: exact lowercase left, right, forward. left/right rotate one heading ({degrees} degrees); forward attempts to cross one shared edge to an adjacent cell, preserving heading. A closed wall blocks forward. Rotations always succeed. Walls are mirrors, but this experiment supplies NO images or visual observations.
One planning response may contain multiple actions with no sequence-length cap. The host executes every legal action in order, continuing after wall collisions, and stops immediately if the actual goal is reached. After each sequence, you receive ONLY ordered per-action outcomes: ok or wall. No map, coordinates, heading, coverage, distance or goal-direction hints are provided. The goal is verified by the host and ends the episode. Do not claim success yourself.
Objective: reach the goal in as few planning calls as possible, then minimize collisions. At most {cfg['max_planning']} planning calls. An invalid sequence is rejected as a whole without movement, costs one planning call and one collision, and returns sequence_rejected=true.
Return one JSON object {{"plan":"brief intention","actions":["left","forward"]}}. A nonempty JSON action array is also accepted. Keep intentions brief; do not expose private chain-of-thought. Do not use tools, shell, files, code execution, browser, web, other agents, or fetch observations. Only return a plan; the host executes it. A native finish wrapper is allowed only to return this JSON.
No actions have been attempted yet. Return your first plan.'''

def feedback_for(steps,invalid=False):
    return {'results':[{'action':s['action'],'outcome':'wall'if s['result']['collision']else'ok'}for s in steps],'sequence_rejected':bool(invalid)}

class NoImages:
    original=();kept=();drops=()
    def drop_oldest_round(self,*_):return False

def compatible_assistant_history(payload,fold_text=False):
    """Retain every thought character when Ark cannot replay empty signatures."""
    value=copy.deepcopy(payload);converted=[]
    for message in value.get('messages',[]):
        if message.get('role')!='assistant'or not isinstance(message.get('content'),list):continue
        blocks=[]
        for block in message['content']:
            if block.get('type')=='thinking'and block.get('signature')in(None,'')and isinstance(block.get('thinking'),str):
                text=block['thinking'];replacement={'type':'text','text':text}
                if 'cache_control'in block:replacement['cache_control']=block['cache_control']
                blocks.append(replacement);converted.append({'chars':len(text),'sha256':hashlib.sha256(text.encode()).hexdigest()})
            else:blocks.append(block)
        message['content']=''.join(b.get('text','')for b in blocks)if fold_text and blocks and all(b.get('type')=='text'for b in blocks)else blocks
    return value,converted

class TextTransport(Transport):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.request_local=threading.local()
    def set_feed(self,feed):
        self.round=feed['round'];self.expected_text=feed['text'];self.selection=NoImages();self.deadline=time.monotonic()+self.cfg.get('planning_timeout',3600)
    def _body(self,incoming,path,main):
        no_images(incoming);value=copy.deepcopy(incoming);value['model']=self.model
        if self.cfg.get('isolate_upstream_session'):value['prompt_cache_key']=upstream_session_key(self.cfg,'main'if main else'aux')
        self.request_local.before=None;self.request_local.converted=[]
        if self.cfg.get('text_history_compatibility')in('assistant_thinking_as_text_v1','assistant_text_string_v2'):
            assert self.model=='DeepSeek-V4.1-Flash-1017-Ark'
            before=json.dumps(value,ensure_ascii=False).encode();value,converted=compatible_assistant_history(value,self.cfg['text_history_compatibility']=='assistant_text_string_v2')
            if before!=json.dumps(value,ensure_ascii=False).encode():self.request_local.before=before;self.request_local.converted=converted
        encoded=json.dumps(value,ensure_ascii=False).encode()
        if len(encoded)>self.cfg.get('max_body_bytes',120000000):raise TextSourceError('text_request_too_large')
        return encoded
    def request(self,incoming,path,main=True,extra_headers=None):
        no_images(incoming);main=has_host_prompt(incoming,self.expected_text);self.request_local.continuation=None
        if not main:
            self.request_local.continuation=native_continuation_parent(self.calls,self.case,self.round,self.expected_text,incoming)
            main=self.request_local.continuation is not None
        return super().request(incoming,path,main,extra_headers)
    def record_request_body(self,record,body):
        assert not self.key or self.key.encode()not in body
        private=self.case/'private';private.mkdir(exist_ok=True);private.chmod(0o700);name='request-'+uuid.uuid4().hex+'.json.gz'
        with gzip.open(private/name,'wb')as f:f.write(body)
        record.update(observation_modality='text',images=[],private_request_file='private/'+name,request_sha256=hashlib.sha256(body).hexdigest(),host_prompt_sha256=hashlib.sha256(self.expected_text.encode()).hexdigest(),no_image_payload=True)
        if getattr(self.request_local,'continuation',None):record['native_continuation_parent_index']=self.request_local.continuation
        if self.request_local.before is not None:
            before=self.request_local.before;assert not self.key or self.key.encode()not in before;original='pre-compat-'+uuid.uuid4().hex+'.json.gz'
            with gzip.open(private/original,'wb')as f:f.write(before)
            record.update(history_compatibility=self.cfg['text_history_compatibility'],private_pre_compat_request='private/'+original,pre_compat_request_sha256=hashlib.sha256(before).hexdigest(),preserved_assistant_thinking=self.request_local.converted)
        dump(self.log,self.calls)

class TextBroker:
    def __init__(self,key,cfg,case):
        self.transport=TextTransport(key,cfg['api_base'],cfg['source_alias'],case,cfg);self.proxy=Proxy(self.transport,cfg['harness']);self.base=self.proxy.base;self.calls=self.transport.calls;self.log=self.transport.log
    def set_feed(self,feed):self.transport.set_feed(feed)
    def close(self):self.proxy.close()

class TextAgyObservation(AgyObservation):
    def set_feed(self,feed):self.round=feed['round'];self.expected_text=feed['text'];self.selection=NoImages()
    def begin_turn(self):
        record={'index':len(self.calls)+1,'kind':'native_agy_text_turn','observation_round':self.round,'main_inference':True,'images':[],'trim_events':[],'source_alias':self.cfg['source_alias'],'native_client_finished':False,'started_at':time.time(),'no_custom_model_api_bridge':True,'observation_modality':'text','host_prompt_sha256':hashlib.sha256(self.expected_text.encode()).hexdigest(),'no_image_payload':True}
        self.calls.append(record);dump(self.log,self.calls);return record
    def finish_turn(self,record,session,proof,result):
        super().finish_turn(record,session,proof,result);self.last_delivery['evidence_type']='native_agy_text_prompt'

class TextAgyNative(AgyNative):
    def _send(self,payload):
        if payload.get('method')=='session/prompt':
            assert payload['params']['prompt']==[{'type':'text','text':self.broker.expected_text}]
            body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode();no_images(payload);record=self.broker.calls[-1]
            private=self.case/'private';private.mkdir(exist_ok=True);private.chmod(0o700);name='request-'+uuid.uuid4().hex+'.json.gz'
            with gzip.open(private/name,'wb')as f:f.write(body)
            record.update(private_request_file='private/'+name,request_sha256=hashlib.sha256(body).hexdigest());dump(self.broker.log,self.broker.calls)
        super()._send(payload)
    def record_host_event(self,event):self._record(event)

def mark_attempt(native,n,text):
    native.record_host_event({'type':'host_text_planning_request','round':n,'prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),'at':time.time()})

def claude_checkpoint_at_accepted_reply(transcript,session,event):
    """Use the official resume boundary, retaining failed tails on disk."""
    matches=[]
    for line in Path(transcript).read_text().splitlines():
        value=json.loads(line)
        if value.get('type')!='assistant'or value.get('sessionId')!=session:continue
        message=value.get('message',{});blocks=message.get('content',[])
        public=''.join(b.get('text','')for b in blocks if b.get('type')=='text')if isinstance(blocks,list)else blocks
        if public==event['response']and value.get('uuid')and not value.get('isApiErrorMessage'):matches.append(value['uuid'])
    if not matches:raise TextSourceError('accepted_text_reply_missing_native_checkpoint')
    return {'committed_rounds':event['round'],'assistant_uuid':matches[-1],'session_id':session,'public_text_sha256':hashlib.sha256(event['response'].encode()).hexdigest(),'transcript_sha256':sha(transcript)}

def private_request(call,case):
    assert call.get('observation_modality')=='text'and call.get('no_image_payload')is True and call.get('images')==[]
    with gzip.open(case/call['private_request_file'],'rb')as f:body=f.read()
    assert hashlib.sha256(body).hexdigest()==call['request_sha256'];payload=json.loads(body);no_images(payload)
    if call.get('private_pre_compat_request'):
        with gzip.open(case/call['private_pre_compat_request'],'rb')as f:before=f.read()
        assert hashlib.sha256(before).hexdigest()==call['pre_compat_request_sha256'];original=json.loads(before);no_images(original);expected,converted=compatible_assistant_history(original,call.get('history_compatibility')=='assistant_text_string_v2');assert expected==payload and converted==call['preserved_assistant_thinking']
    return payload

def native_request_payload(call,case):
    payload=private_request(call,case)
    if call.get('private_pre_compat_request'):
        with gzip.open(case/call['private_pre_compat_request'],'rb')as f:return json.loads(f.read())
    return payload

def native_compaction_proof(case,cfg,calls,call,n,text,payload,raw):
    """Bind native context compression to its summary and final planning reply."""
    if cfg.get('harness')!='claude':return None
    messages=payload.get('messages',[])
    if len(messages)!=1 or messages[0].get('role')!='user':return None
    blocks=messages[0].get('content',[])
    if not isinstance(blocks,list)or len(blocks)!=2 or any(b.get('type')!='text'for b in blocks):return None
    wrapper=blocks[0].get('text','')
    if not wrapper.startswith(COMPACT_HEADER)or blocks[1].get('text')!=OUTPUT_LIMIT_CONTINUE:return None
    # A live native CLI keeps its gzip writer open between turns. The final
    # native result below and terminal provider response prove this turn;
    # the gzip footer is required only when auditing a stopped case.
    groups,session,_,truncated=_native_groups(case)
    wanted=hashlib.sha256(text.encode()).hexdigest()
    group=next((g for g in groups if g['marker']['round']==n and g['marker']['prompt_sha256']==wanted and any(e.get('type')=='result'and not e.get('is_error')and e.get('result','').strip()==raw.strip()for e in g['events'])),None)
    if not group:return None
    boundaries=[e for e in group['events']if e.get('type')=='system'and e.get('subtype')=='compact_boundary'and e.get('session_id')==session and e.get('compact_metadata',{}).get('trigger')=='auto']
    if not boundaries:return None
    profiles=list((Path(cfg['profile_root'])/'claude/projects').glob('*/'+session+'.jsonl'))
    if len(profiles)!=1:return None
    entries=[json.loads(line)for line in profiles[0].read_text().splitlines()];summaries=[]
    for entry in entries:
        if entry.get('type')!='user'or not entry.get('isCompactSummary')or entry.get('sessionId')!=session or entry.get('version')!='2.1.303':continue
        value=entry.get('message',{}).get('content','');value=value if isinstance(value,str)else''.join(b.get('text','')for b in value if b.get('type')=='text')
        if wrapper in(value,value+'\n'):summaries.append(entry)
    if not summaries:return None
    summary_entry=summaries[-1]
    boundary=next((b for b in boundaries if summary_entry['uuid']==b.get('compact_metadata',{}).get('preserved_segment',{}).get('anchor_uuid')),None)
    if not boundary:return None
    if not any(e.get('uuid')==boundary['uuid']and e.get('subtype')=='compact_boundary'and e.get('sessionId')==session for e in entries):return None
    tail_uuid=boundary['compact_metadata']['preserved_segment'].get('tail_uuid');tail=next((e for e in entries if e.get('uuid')==tail_uuid and e.get('type')=='user'and e.get('sessionId')==session),None)
    if not tail:return None
    tail_content=tail.get('message',{}).get('content','');tail_text=tail_content if isinstance(tail_content,str)else''.join(b.get('text','')for b in tail_content if b.get('type')=='text')
    if tail_text!=OUTPUT_LIMIT_CONTINUE:return None
    for parent in reversed(calls):
        if parent['index']>=call['index']or parent.get('observation_round')!=n or parent.get('http_status')!=200 or any(parent.get(k)for k in('error','transport_error','provider_fault')):continue
        with gzip.open(case/parent['private_response_file'],'rb')as f:meta=metadata_response(f.read())
        if meta['response_error']or not meta['terminal']or meta['finish_status']!='end_turn':continue
        extracted=re.findall(r'<summary>([\s\S]*?)</summary>',meta['text'],re.I)
        if len(extracted)!=1 or not extracted[0]or COMPACT_HEADER+extracted[0]not in wrapper:continue
        prior=native_request_payload(parent,case);anchor=parent.get('main_inference')and has_host_prompt(prior,text)
        ancestor=native_continuation_parent([c for c in calls if c['index']<parent['index']],case,n,text,prior)if not anchor else None
        if not anchor and ancestor is None:continue
        return {'rule':'official_claude_auto_compaction','boundary_uuid':boundary['uuid'],'summary_entry_uuid':summary_entry['uuid'],'summary_request_index':parent['index'],'summary_ancestor_index':ancestor,'planning_request_index':call['index'],'summary_sha256':hashlib.sha256(extracted[0].encode()).hexdigest(),'wrapper_sha256':hashlib.sha256(wrapper.encode()).hexdigest(),'profile_session':session,'host_prompt_sha256':wanted,'native_compact_metadata':boundary['compact_metadata']}
    return None

def matching_call(case,cfg,n,text,raw):
    calls=json.loads((case/'wire.json').read_text())
    for call in calls:
        if call.get('observation_round')!=n or call.get('error')or call.get('transport_error')or call.get('provider_fault'):continue
        payload=private_request(call,case)
        if cfg.get('native_provider'):
            if call.get('main_inference')and call.get('native_client_finished')and payload['params']['prompt']==[{'type':'text','text':text}]:return call
        else:
            if call.get('http_status')!=200:continue
            direct=call.get('main_inference')and has_host_prompt(payload,text)
            continuation=native_continuation_parent([c for c in calls if c['index']<call['index']],case,n,text,native_request_payload(call,case))if not direct else None
            with gzip.open(case/call['private_response_file'],'rb')as f:meta=metadata_response(f.read())
            if meta['response_error']or not meta['terminal']or meta['text'].strip()!=raw.strip():continue
            compaction=native_compaction_proof(case,cfg,calls,call,n,text,native_request_payload(call,case),raw)if not direct and continuation is None else None
            if not direct and continuation is None and compaction is None:continue
            if compaction:call=dict(call,native_compaction_proof=compaction)
            return call
    raise TextSourceError('text_native_completion_has_no_matching_committed_request')

def _native_groups(case):
    events=[];lines=[];truncated=False
    try:
        with gzip.open(case/'native.jsonl.gz','rt')as f:
            for line in f:
                value=json.loads(line);events.append(value);lines.append(line)
    except EOFError:truncated=True
    groups=[];group=None;session=None
    for event in events:
        if event.get('type')=='host_text_planning_request':
            if group:groups.append(group)
            group={'marker':event,'events':[],'session':session}
        elif group:group['events'].append(event)
        if event.get('type')=='thread.started':session=event['thread_id']
        if event.get('type')=='system'and event.get('subtype')=='init':session=event['session_id']
        if event.get('type')=='native_agy_rpc_request':session=event['params']['sessionId']
        if group:group['session']=session
    if group:groups.append(group)
    return groups,session,lines,truncated

def completed_text_replies(case,cfg):
    groups,session,lines,truncated=_native_groups(case);replies=[]
    for group in groups:
        raw=None;proof={};events=group['events'];marker=group['marker']
        if cfg.get('native_provider'):
            try:raw,proof,_=completed_agy_reply(events)
            except ForbiddenAgyToolError:raise
            except RuntimeError:continue
        elif cfg['harness']=='claude':
            results=[e for e in events if e.get('type')=='result'and not e.get('is_error')]
            if len(results)>1:raise TextSourceError('multiple_native_text_completions')
            if results:raw=results[0].get('result','')
        else:
            candidate=None
            for event in events:
                if event.get('type')=='item.completed'and event.get('item',{}).get('type')=='agent_message':candidate=event['item'].get('text','')
                if event.get('type')=='turn.failed':candidate=None
                if event.get('type')=='turn.completed':raw=candidate
        if source_fault(raw):continue
        replies.append({'round':marker['round'],'response':raw,'prompt_sha256':marker['prompt_sha256'],'native_proof':proof,'session':group['session']})
    if len({r['session']for r in replies})>1:raise TextSourceError('text_native_session_changed')
    return replies,session,lines,truncated

def verified_uncharged_claude_fault(case,call,n,text):
    """An empty provider response is retryable only with its native end turn."""
    if call.get('http_status')!=200 or not call.get('private_response_file'):return None
    with gzip.open(case/call['private_response_file'],'rb')as f:meta=metadata_response(f.read())
    fault=source_fault(meta['text'])
    if not fault or meta['response_error']or not meta['terminal']:return None
    if not has_host_prompt(private_request(call,case),text):return None
    groups,_,_,truncated=_native_groups(case)
    if truncated:return None
    for index,group in enumerate(groups):
        marker=group['marker'];end=groups[index+1]['marker']['at']if index+1<len(groups)else float('inf')
        if marker['round']!=n or marker['prompt_sha256']!=hashlib.sha256(text.encode()).hexdigest()or not marker['at']<=call['started_at']<end:continue
        results=[e for e in group['events']if e.get('type')=='result'and not e.get('is_error')]
        if len(results)!=1:continue
        native_fault=source_fault(results[0].get('result',''))
        if not native_fault or native_fault.kind!=fault.kind:continue
        if any(b.get('type')not in('thinking','text')for e in group['events']if e.get('type')=='assistant'for b in e.get('message',{}).get('content',[])):continue
        return {'wire_index':call['index'],'round':n,'fault':fault.kind,'private_response_sha256':sha(case/call['private_response_file']),'native_public_text_sha256':hashlib.sha256(results[0].get('result','').encode()).hexdigest(),'native_end_turn_verified':True,'planning_charge':0}
    return None

def recover_text_reply(case,cfg):
    case=Path(case);p=json.loads((case/'progress.json').read_text());n=len(p['events']);source=case/'native.jsonl.gz'
    if not source.exists():
        if n:raise TextSourceError('missing_native_text_history')
        return None
    replies,session,lines,truncated=completed_text_replies(case,cfg)
    if [r['round']for r in replies]not in(list(range(1,n+1)),list(range(1,n+2))):raise TextSourceError('ambiguous_native_text_completion_prefix')
    for host,reply in zip(p['events'],replies):assert host['response']==reply['response']and host['input_prompt_sha256']==reply['prompt_sha256']
    if truncated:
        archive=case/'interruptions'/('text-truncated-'+str(time.time_ns()));archive.mkdir(parents=True);(archive/'native-original.jsonl.gz').write_bytes(source.read_bytes())
        temporary=case/'native-repaired.gz'
        with gzip.open(temporary,'wt')as f:f.writelines(lines)
        temporary.replace(source)
    if session:dump(case/'native-session.json',{'thread_id':session,'harness':cfg['harness'],'text_only':True})
    if len(replies)>n:
        reply=replies[-1];feedback=p['events'][-1]['feedback']if n else{'results':[],'sequence_rejected':False};text=prompt(cfg,n+1,feedback);assert hashlib.sha256(text.encode()).hexdigest()==reply['prompt_sha256'];call=matching_call(case,cfg,n+1,text,reply['response']);return dict(reply,wire=call,recovered=True)
    # A provider completion without a native commitment is not resampled.
    for call in json.loads((case/'wire.json').read_text()):
        if call.get('observation_round')==n+1 and call.get('main_inference')and not(call.get('error')or call.get('transport_error')or call.get('provider_fault')):
            if cfg['harness']=='claude':
                text=prompt(cfg,n+1,p['events'][-1]['feedback']if n else{'results':[],'sequence_rejected':False});proof=verified_uncharged_claude_fault(case,call,n+1,text)
                if proof:dump(case/'verified-uncharged-source-faults'/('wire-'+str(call['index'])+'.json'),proof);continue
            if call.get('native_client_finished')or call.get('http_status')==200:raise TextSourceError('pending_upstream_text_completion_requires_native_reconciliation')
    return None

def run_text(cfg,key,case,cap=200,resume=False,recovered_reply=None,**_):
    case=Path(case).resolve();case.mkdir(parents=True,exist_ok=True);case.chmod(0o700);cfg=dict(cfg,direct_observations=True,observation_modality='text',max_planning=cap)
    events=[];invalid=0;native=None;broker=None;status='running';error=None;old=None;session=None;started=time.time()
    if resume:
        old=json.loads((case/'progress.json').read_text());assert old['protocol']==PROTOCOL and old['status']=='infrastructure_error';events=old['events'];invalid=old['invalid_sequences'];started-=old.get('elapsed_seconds',0)
        archive=case/'interruptions'/('text-resume-'+str(time.time_ns()));archive.mkdir(parents=True)
        for name in('config.json','progress.json','episode.json','result.json','wire.json'):
            if(case/name).exists():(archive/name).write_bytes((case/name).read_bytes())
        session=json.loads((case/'native-session.json').read_text()).get('thread_id')if(case/'native-session.json').exists()else None
        if session:
            if cfg.get('native_provider'):cfg['_resume_agy_native']=session
            elif cfg['harness']=='claude':
                matches=list((Path(cfg['profile_root'])/'claude/projects').glob('*/'+session+'.jsonl'));assert len(matches)==1;cfg['_resume_transcript']=str(matches[0])
        elif events:raise TextSourceError('missing_text_native_session')
    dump(case/'config.json',cfg)
    with MirrorMaze(**environment_options(cfg))as env:
        if resume:
            episode=json.loads((case/'episode.json').read_text());env.perform([a['action']for a in episode['actions']],stop_on_collision=False);assert env.export_episode()['state']==episode['state']
        def save():
            st=env.review_state();dump(case/'episode.json',env.export_episode());dump(case/'progress.json',{'protocol':PROTOCOL,'observation_modality':'text','status':status,'planning_rounds':len(events),'invalid_sequences':invalid,'total_collisions':st['collisions']+invalid,'state':st,'events':events,'elapsed_seconds':time.time()-started,'error':error});dump(case/'status.json',{'status':status,'planning_rounds':len(events),'actions':st['actionCount'],'collisions':st['collisions']+invalid,'visited':st['visited'],'success':st['success'],'updated_at':time.time(),'error':error})
        save()
        try:
            broker=TextAgyObservation(cfg,case)if cfg.get('native_provider')else TextBroker(key,cfg,case)
            while len(events)<cap and not env.state()['success']:
                n=len(events)+1;feedback=events[-1]['feedback']if events else{'results':[],'sequence_rejected':False};text=prompt(cfg,n,feedback);feed={'round':n,'text':text};dump(case/'feed.json',feed);broker.set_feed(feed)
                if recovered_reply:
                    assert recovered_reply['round']==n;raw=recovered_reply['response'];recovered_reply=None;replayed=True
                else:
                    replayed=False
                    if native is None:
                        if resume and cfg['harness']=='claude'and events:
                            checkpoint=claude_checkpoint_at_accepted_reply(cfg['_resume_transcript'],session,events[-1]);cfg['_resume_session_at']=checkpoint['assistant_uuid'];dump(case/'native-resume-checkpoints'/(str(time.time_ns())+'.json'),checkpoint)
                        else:cfg.pop('_resume_session_at',None)
                        native=TextAgyNative(cfg,case,broker)if cfg.get('native_provider')else Native(cfg,case,broker)
                        if resume and cfg['harness']=='codex':native.thread_id=session
                    mark_attempt(native,n,text);raw=native.ask(text,timeout=cfg.get('planning_timeout',3600))
                    if session and native.thread_id!=session:raise TextSourceError('text_native_session_changed')
                    if native.thread_id:session=native.thread_id;dump(case/'native-session.json',{'thread_id':native.thread_id,'harness':cfg['harness'],'text_only':True})
                fault=source_fault(raw)
                if fault:
                    for call in reversed(broker.calls):
                        if call.get('main_inference')and call.get('observation_round')==n:call.update(provider_fault=fault.kind,usable_model_reply=False);dump(broker.log,broker.calls);break
                    raise fault
                call=matching_call(case,cfg,n,text,raw);plan,reason=parse_plan(raw);before=env.review_state();steps=[]
                if plan:
                    for action in plan['actions']:
                        result=env.step(action)['results'][0];steps.append({'action':action,'result':result,'state':env.review_state()})
                        if env.state()['success']:break
                else:invalid+=1
                events.append({'round':n,'response':raw,'plan':plan.get('plan','')if plan else'','actions':plan['actions']if plan else[],'invalid':plan is None,'invalid_reason':reason,'before':before,'after':env.review_state(),'steps':steps,'feedback':feedback_for(steps,plan is None),'input_feedback':feedback,'input_prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),'input_request_index':call['index'],'text_delivery_verified':True,'input_frames':[],'observation_modality':'text','recovered_committed_reply':replayed,'native_compaction_proof':call.get('native_compaction_proof')});save()
            status='success'if env.state()['success']else'planning_cap'
        except Exception as exc:
            status='infrastructure_error';error=(type(exc).__name__+': '+str(exc)).replace(key,'[redacted]')if key else type(exc).__name__+': '+str(exc)
            if isinstance(exc,TextSourceError)and exc.retry_after:dump(case/'retry-after.json',{'not_before':time.time()+exc.retry_after,'reason':exc.kind,'message':'Provider quota interruption; no planning decision, action or collision was charged.','recorded_at':time.time()})
        finally:
            if native:native.close()
            if broker:broker.close()
            save()
        st=env.review_state();result={'case_id':case.name,'protocol':PROTOCOL,'observation_modality':'text','model':cfg['model_label'],'source_alias':cfg['source_alias'],'harness':cfg['harness'],'topology':cfg['topology'],'seed':cfg['seed'],'planning_cap':cap,'status':status,'success':status=='success','planning_rounds':len(events),'actions':st['actionCount'],'collisions':st['collisions']+invalid,'invalid_sequences':invalid,'seconds':time.time()-started,'images_sent':0,'render_calls':0,'native_attempts':len(broker.calls)if broker else 0,'error':error};dump(case/'result.json',result);return result

def audit_text(case,cfg):
    case=Path(case);p=json.loads((case/'progress.json').read_text());result=json.loads((case/'result.json').read_text());assert result['status']in('success','planning_cap')and result['images_sent']==result['render_calls']==0
    replies,_,_,truncated=completed_text_replies(case,cfg);assert not truncated and len(replies)==len(p['events']);invalid=0
    with MirrorMaze(**environment_options(cfg))as env:
        for i,(event,reply)in enumerate(zip(p['events'],replies),1):
            assert event['round']==reply['round']==i and event['response']==reply['response']and source_fault(event['response'])is None
            previous=p['events'][i-2]['feedback']if i>1 else{'results':[],'sequence_rejected':False};text=prompt(cfg,i,previous);assert event['input_feedback']==previous and event['input_prompt_sha256']==reply['prompt_sha256']==hashlib.sha256(text.encode()).hexdigest();call=matching_call(case,cfg,i,text,event['response']);assert call['index']==event['input_request_index'];assert not event.get('native_compaction_proof')or event['native_compaction_proof']==call.get('native_compaction_proof');assert env.review_state()==event['before'];plan,reason=parse_plan(event['response']);steps=[]
            if plan:
                for action in plan['actions']:
                    actual=env.step(action)['results'][0];steps.append({'action':action,'result':actual,'state':env.review_state()})
                    if env.state()['success']:break
            else:invalid+=1
            assert steps==event['steps']and feedback_for(steps,plan is None)==event['feedback']and env.review_state()==event['after'];assert event['input_frames']==[]
        assert env.review_state()==p['state'];assert result['planning_rounds']==len(p['events'])<=cfg['max_planning'];assert result['collisions']==env.state()['collisions']+invalid;assert result['success']==env.state()['success']
    for call in json.loads((case/'wire.json').read_text()):private_request(call,case)
    proof={'all_checked_invariants_passed':True,'protocol':PROTOCOL,'replayed_planning_rounds':len(p['events']),'all_requests_text_only':True,'images_sent':0,'feedback_whitelist_verified':True,'source_faults_never_scored':True};dump(case/'validation.json',proof);dump(case/'audited.json',{'complete':True,'audit_passed':True,'ended_at':time.time(),'result_sha256':sha(case/'result.json'),'validation_sha256':sha(case/'validation.json')});return proof
