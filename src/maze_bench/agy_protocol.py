"""Validate one completed native AGY ACP turn without inspecting action quality."""
import hashlib,json


class ForbiddenAgyToolError(RuntimeError):
    """A completed prompt used an operation outside the observation contract."""
    def __init__(self,violations):
        self.violations=violations
        super().__init__('Native AGY completed a prompt containing forbidden tool operations; manual review required')


class StaleDeniedToolReplayError(RuntimeError):
    def __init__(self,tool_call_ids):
        self.tool_call_ids=sorted(str(ident)for ident in tool_call_ids)
        super().__init__('stale_denied_tool_replay: an already charged denied tool intent was replayed; no new planning decision may be charged')


class EmptyAgyReplyError(RuntimeError):
    def __init__(self,proof):
        self.proof=proof
        super().__init__('Native AGY completed with empty public text')


def ensure_fresh_denied_tools(proof,seen_ids):
    values=[tool['tool_call_id']for tool in proof.get('native_verified_denied_tool_attempts',[])]
    unique=set(values)
    if len(unique)!=len(values)or unique.intersection(seen_ids):raise StaleDeniedToolReplayError(unique.intersection(seen_ids)or unique)
    return unique


def committed_denied_tool_ids(progress,wire):
    by_index={call['index']:call for call in wire};seen=set()
    for event in progress.get('events',[]):
        call=by_index.get(event.get('input_request_index'))
        if call is None:raise RuntimeError('Committed AGY decision has no native wire record')
        proof=call.get('native_output_proof',{})
        if proof.get('native_verified_denied_tool_attempts',[])!=event.get('native_output_proof',{}).get('native_verified_denied_tool_attempts',[]):raise RuntimeError('Committed AGY denial proof differs from host record')
        seen.update(ensure_fresh_denied_tools(proof,seen))
    return seen


def is_finish_tool(tool):
    """Only the native final-response wrapper is allowed, never a titled shell."""
    if tool.get('kind')in('execute','read','edit','delete','move','search','fetch','terminal'):return False
    names=[tool[key]for key in('name','toolName')if tool.get(key)not in(None,'')]
    if names:return all(isinstance(name,str)and name.strip().lower()=='finish'for name in names)
    return isinstance(tool.get('title'),str)and tool['title'].strip().lower()=='finish'


def _raw_prompt_tool_attempts(events,session_id):
    """Inspect only one declared prompt window; setup/load history is excluded.

    Returned notes deliberately omit command arguments and tool output.
    """
    violations=[];finish_ids=set()
    for index,event in enumerate(events):
        if event.get('jsonrpc')!='2.0':continue
        tool=None;reason=None;params=event.get('params',{})
        if event.get('method')=='session/update':
            update=params.get('update',{})
            if update.get('sessionUpdate')not in('tool_call','tool_call_update'):continue
            tool=update;ident=tool.get('toolCallId')
            explicit_name=bool(tool.get('name')or tool.get('toolName'))
            allowed=is_finish_tool(tool)or(not explicit_name and ident in finish_ids and tool.get('kind')not in('execute','read','edit','delete','move','search','fetch','terminal'))
            if params.get('sessionId')!=session_id:reason='foreign_session_tool_notification'
            elif allowed:
                if ident:finish_ids.add(ident)
                continue
            else:reason='non_finish_'+update['sessionUpdate']
        elif 'id'in event and event.get('method')=='session/request_permission':
            tool=params.get('toolCall',{})
            if params.get('sessionId')not in(None,session_id):reason='foreign_session_permission_request'
            elif is_finish_tool(tool):
                if tool.get('toolCallId'):finish_ids.add(tool['toolCallId'])
                continue
            else:reason='non_finish_permission_request'
        elif 'id'in event and event.get('method'):
            tool={'name':event['method']};reason='native_server_tool_request'
        if reason:
            name=tool.get('name')or tool.get('toolName')or'unknown'
            violations.append({'event_index':index,'reason':reason,'tool_call_id':tool.get('toolCallId'),'tool_name':name if isinstance(name,str)and len(name)<128 else'unknown','event_sha256':hashlib.sha256(json.dumps(event,sort_keys=True).encode()).hexdigest()})
    return violations


def _denial_output_only(value):
    if value in(None,'',[],{}):return True
    if isinstance(value,str):
        normalized=' '.join(value.strip().lower().strip('.!').split())
        return normalized in{'permission denied','permission denied by user','permission denied by the user','tool execution denied','tool execution denied by user','tool execution denied by the user','tool execution was denied by user','tool execution was denied by the user','user denied tool execution','user denied this action','user rejected tool execution','operation cancelled','operation canceled','tool execution cancelled','tool execution canceled','tool execution cancelled by user','tool execution canceled by user','tool execution was cancelled by user','tool execution was canceled by user','tool call cancelled','tool call canceled','cancelled','canceled'}
    if isinstance(value,list):return all(_denial_output_only(item)for item in value)
    if isinstance(value,dict):
        for key,item in value.items():
            if key in('error','message','text','content'):
                if not _denial_output_only(item):return False
            elif key=='type':
                if item not in('text','content'):return False
            elif key=='isError':
                if item is not True:return False
            elif key=='code':
                if not(isinstance(item,int)and not isinstance(item,bool))and item not in('permission_denied','permission_cancelled','permission_canceled','user_cancelled','user_canceled','tool_denied'):return False
            elif key=='status':
                if item not in('failed','cancelled','canceled','error'):return False
            elif key=='output':
                # AGY1.5.1's permission-denied terminal envelope. An executed
                # operation returning any output must never pass this check.
                if item!='':return False
            else:return False
        return True
    return False


def prompt_tool_assessment(events,session_id):
    """Allow only finish, or a completely evidenced denied operation with no data.

    Event indices are relative to the prompt body (declaration excluded).
    A failed status alone is insufficient: an executed command can fail after
    returning private data. Every permission must have a matching cancellation.
    """
    attempts=_raw_prompt_tool_attempts(events,session_id);groups={};permissions={};cancellations={}
    for index,event in enumerate(events):
        if event.get('jsonrpc')=='2.0'and event.get('method')=='session/update':
            params=event.get('params',{});tool=params.get('update',{})
            if tool.get('sessionUpdate')in('tool_call','tool_call_update')and params.get('sessionId')==session_id and tool.get('toolCallId'):
                groups.setdefault(tool['toolCallId'],[]).append((index,tool))
        if event.get('jsonrpc')=='2.0'and event.get('method')=='session/request_permission'and'id'in event:
            params=event.get('params',{});tool=params.get('toolCall',{})
            if not is_finish_tool(tool)and tool.get('toolCallId')and params.get('sessionId')in(None,session_id):permissions[event['id']]={'tool_call_id':tool['toolCallId'],'index':index,'options':params.get('options',[])}
        if event.get('type')=='native_agy_denied_tool_request'and event.get('method')=='session/request_permission':
            response=event.get('response',{})
            outcome=response.get('result',{}).get('outcome',{})
            if response.get('jsonrpc')=='2.0'and outcome.get('outcome')in('cancelled','selected'):cancellations[response.get('id')]={'index':index,'outcome':outcome}
    denied=[];verified=set()
    for ident,updates in groups.items():
        requests=[(rpc,proof)for rpc,proof in permissions.items()if proof['tool_call_id']==ident]
        if not requests or any(rpc not in cancellations or cancellations[rpc]['index']<=proof['index']for rpc,proof in requests):continue
        permitted_denials=True
        for rpc,request in requests:
            outcome=cancellations[rpc]['outcome']
            if outcome['outcome']=='selected':
                option=next((option for option in request['options']if option.get('optionId',option.get('id'))==outcome.get('optionId')),None)
                if not option or option.get('kind')not in('reject_once','reject_always'):permitted_denials=False
        if not permitted_denials:continue
        if any(tool.get('status')=='completed'or not _denial_output_only(tool.get('rawOutput'))or not _denial_output_only(tool.get('content'))for _,tool in updates):continue
        terminal_index,terminal=updates[-1]
        if terminal.get('status')not in('failed','cancelled','canceled')or any(cancellations[rpc]['index']>=terminal_index for rpc,_ in requests):continue
        related=[attempt for attempt in attempts if attempt['tool_call_id']==ident]
        if not related:continue
        # Foreign-session records, direct filesystem/terminal RPCs or unknown
        # operations are never excused by another tool's cancellation.
        if any(attempt['reason']not in('non_finish_tool_call','non_finish_tool_call_update','non_finish_permission_request')for attempt in related):continue
        verified.add(ident);denied.append({'tool_call_id':ident,'permission_request_ids':[rpc for rpc,_ in requests],'permission_event_indices':[proof['index']for _,proof in requests],'cancellation_event_indices':[cancellations[rpc]['index']for rpc,_ in requests],'permission_outcomes':[cancellations[rpc]['outcome']for rpc,_ in requests],'terminal_tool_event_index':terminal_index,'terminal_status':terminal['status'],'returned_external_data':False})
    return {'attempts':attempts,'violations':[attempt for attempt in attempts if attempt['tool_call_id']not in verified],'verified_denied_tools':denied}


def prompt_tool_violations(events,session_id):return prompt_tool_assessment(events,session_id)['violations']


def completed_agy_reply(events,request_id=None):
    declarations=[(i,e)for i,e in enumerate(events)if e.get('type')=='native_agy_rpc_request'and e.get('method')=='session/prompt'and(request_id is None or e.get('id')==request_id)]
    if not declarations:raise RuntimeError('Native AGY prompt declaration is missing')
    start,request=declarations[-1];request_id=request['id'];session=request.get('params',{}).get('sessionId')
    if not session:raise RuntimeError('Native AGY session is missing from its prompt declaration')
    responses=[(i,e)for i,e in enumerate(events)if i>start and e.get('jsonrpc')=='2.0'and e.get('id')==request_id and('result'in e or'error'in e)]
    if len(responses)!=1:raise RuntimeError('Native AGY has no unique completed RPC response')
    end,result=responses[0]
    if result.get('error')or result.get('result',{}).get('stopReason')!='end_turn':
        raise RuntimeError('Native AGY turn did not finish with end_turn')
    assessment=prompt_tool_assessment(events[start+1:end],session);violations=assessment['violations']
    if violations:raise ForbiddenAgyToolError(violations)
    chunks=[];indices=[]
    for index,event in enumerate(events[start+1:end],start+1):
        if event.get('method')!='session/update':continue
        params=event.get('params',{});update=params.get('update',{})
        if update.get('sessionUpdate')!='agent_message_chunk':continue
        if params.get('sessionId')!=session:raise RuntimeError('Native AGY public chunks belong to a different session')
        content=update.get('content',{})
        if content.get('type')=='text':
            text=content.get('text','')
            if not isinstance(text,str):raise RuntimeError('Native AGY public text has an invalid type')
            chunks.append(text);indices.append(index-start)
    text=''.join(chunks)
    # User policy: even a proven tool denial ending the ACP turn is an
    # infrastructure interruption when no public plan was returned.
    proof={'native_json_event_offset':request.get('native_json_event_offset',start),
           'native_request_event_index':0,'native_result_event_index':end-start,
           'native_request_id':request_id,'native_session_id':session,
           'native_chunk_event_indices':indices,'selected_public_text_sha256':hashlib.sha256(text.encode()).hexdigest(),
           'native_stop_reason':'end_turn','observation_round':request.get('observation_round'),
           'wire_index':request.get('wire_index'),'native_request_sha256':request.get('request_sha256'),
           'prompt_sha256':request.get('prompt_sha256')}
    proof['native_verified_denied_tool_attempts']=assessment['verified_denied_tools']
    if not text.strip():raise EmptyAgyReplyError(proof)
    return text,proof,result
