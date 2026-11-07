"""Recover a committed native AGY ACP reply without another provider request."""
import gzip,hashlib,json,time
from pathlib import Path
from .common import dump,sha

KIND='native_agy_acp_image_attachment'


def result_sha(result):return hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()


def declaration_images_match(declaration,call,cfg=None):
    assert declaration['wire_index']==call['index']and declaration['observation_round']==call['observation_round']
    fields=('file','sha256','width','height','index')
    expected=[{key:image.get(key)for key in fields}for image in call['images']]
    actual=[{key:image.get(key)for key in fields}for image in declaration['images']]
    assert actual==expected and actual,'Native AGY image declaration differs from delivered observation order'
    assert all((image['width'],image['height'])==(1280,720)for image in actual)
    if cfg:
        for image in actual:assert sha(Path(cfg['cache_dir'])/image['file'])==image['sha256']


def native_agy_completions(case):
    from .agy_protocol import completed_agy_reply,ForbiddenAgyToolError
    source=Path(case)/'native.jsonl.gz';events=[];lines=[];truncated=False
    if not source.exists():return events,[],lines,truncated
    try:
        with gzip.open(source,'rt')as file:
            for line in file:
                if not line.strip():continue
                try:event=json.loads(line)
                except ValueError as exc:raise RuntimeError('Malformed native AGY archive; inspect before another request')from exc
                if not isinstance(event,dict):raise RuntimeError('Native AGY archive contains a non-object event')
                events.append(event);lines.append(line)
    except EOFError:truncated=True
    group=[];declaration=None;offset=0;completed=[];rounds=set()
    for index,event in enumerate(events):
        if event.get('type')=='native_agy_control_request':
            declaration=None;group=[];continue
        if event.get('type')=='native_agy_rpc_request'and event.get('method')=='session/prompt':
            declaration=event;group=[];offset=index
        if declaration is None:continue
        group.append(event)
        if event.get('jsonrpc')!='2.0'or event.get('id')!=declaration['id']or event.get('method')or not('result'in event or'error'in event):continue
        try:text,proof,result=completed_agy_reply(group,request_id=declaration['id'])
        except ForbiddenAgyToolError:raise  # A native end_turn cannot bypass live tool policy.
        except RuntimeError:continue  # Explicit failure/partial output never becomes a plan.
        number=declaration['observation_round']
        if not isinstance(number,int)or isinstance(number,bool)or number<1:raise RuntimeError('Native AGY completion has no valid planning round')
        if number in rounds:raise RuntimeError('Multiple native AGY completions for one planning round; do not resample')
        rounds.add(number);proof['native_json_event_offset']=offset
        assert proof['native_request_id']==declaration['id']and proof['native_session_id']==declaration['params']['sessionId']
        assert proof['native_stop_reason']=='end_turn'
        completed.append({'round':number,'response':text,'proof':proof,'result':result,'declaration':declaration,'result_sha256':result_sha(result)})
    return events,completed,lines,truncated


def native_agy_operation_notes(events):
    """Summarize failed tool attempts and historical replay without scoring them."""
    from .agy_protocol import prompt_tool_assessment
    windows=[];historical=[];declaration=None;group=[];offset=0;control=None
    def finish():
        if not declaration:return
        end=next((i for i,event in enumerate(group[1:],1)if event.get('jsonrpc')=='2.0'and event.get('id')==declaration['id']and('result'in event or'error'in event)),len(group))
        assessment=prompt_tool_assessment(group[1:end],declaration['params']['sessionId'])
        if assessment['attempts']:
            terminal=group[end]if end<len(group)else{}
            windows.append({'observation_round':declaration['observation_round'],'wire_index':declaration['wire_index'],'request_id':declaration['id'],'native_json_event_offset':offset,'terminal_error':bool(terminal.get('error')),'terminal_stop_reason':terminal.get('result',{}).get('stopReason'),'attempts':assessment['attempts'],'violations':assessment['violations'],'verified_denied_tool_attempts':assessment['verified_denied_tools'],'counts_as_additional_planning_decision':False,'counts_as_additional_collision':False})
    for index,event in enumerate(events):
        if event.get('type')in('native_agy_control_request','native_agy_rpc_request'):
            finish();declaration=None;group=[]
            if event.get('type')=='native_agy_rpc_request'and event.get('method')=='session/prompt':declaration=event;offset=index;control=None
            else:control=event.get('method')
        if declaration:group.append(event)
        elif event.get('method')=='session/update'and event.get('params',{}).get('update',{}).get('sessionUpdate')in('tool_call','tool_call_update'):
            update=event['params']['update'];historical.append({'event_index':index,'control_phase':control,'tool_call_id':update.get('toolCallId'),'tool_name':update.get('name')or update.get('toolName')or'unknown'})
    finish()
    return {'schema':'mirror-maze-native-agy-tool-operations/v1','prompt_windows_with_tool_attempts':windows,'historical_tool_replay_notifications':historical,'planning_budget_unchanged':True,'native_completion_fabricated':False}


def recover_agy_reply(case,cfg):
    case=Path(case);progress=json.loads((case/'progress.json').read_text());n=len(progress['events']);source=case/'native.jsonl.gz'
    if not source.exists():
        if n==0:return None
        raise RuntimeError('Native AGY transcript is missing after committed decisions')
    try:events,completed,lines,truncated=native_agy_completions(case)
    except Exception as exc:
        from .agy_protocol import ForbiddenAgyToolError
        if isinstance(exc,ForbiddenAgyToolError):dump(case/'native-tool-attempts.json',{'schema':'mirror-maze-native-agy-tool-operations/v1','manual_review_required':True,'completed_prompt_violations':exc.violations,'planning_budget_unchanged':True,'native_completion_fabricated':False})
        raise
    operation_notes=native_agy_operation_notes(events)
    numbers=[entry['round']for entry in completed]
    if numbers not in(list(range(1,n+1)),list(range(1,n+2))):raise RuntimeError('Native AGY completion prefix is ambiguous; do not resample')
    wire=json.loads((case/'wire.json').read_text())if(case/'wire.json').exists()else[]
    sessions={entry['proof']['native_session_id']for entry in completed}
    if len(sessions)>1:raise RuntimeError('Native AGY committed history contains different sessions')
    from .agy_protocol import ensure_fresh_denied_tools,StaleDeniedToolReplayError
    charged=set()
    try:
        for host,entry in zip(progress['events'],completed):
            call=next((w for w in wire if w['index']==host['input_request_index']),None)
            if not call or call.get('kind')!=KIND or not call.get('native_client_finished'):raise RuntimeError('Committed native AGY reply has no finished observation record')
            assert host['response']==entry['response'],'Native AGY committed public response differs'
            assert call['native_result_sha256']==entry['result_sha256']and call['native_session_id']==entry['proof']['native_session_id']
            assert call.get('native_output_proof',{}).get('native_verified_denied_tool_attempts',[])==entry['proof'].get('native_verified_denied_tool_attempts',[]),'Native AGY committed tool-denial proof differs'
            assert call.get('native_output_proof',{}).get('native_invalid_plan_reason')==entry['proof'].get('native_invalid_plan_reason'),'Native AGY committed denied-tool decision classification differs'
            charged.update(ensure_fresh_denied_tools(entry['proof'],charged))
            declaration_images_match(entry['declaration'],call)
        if len(completed)>n:ensure_fresh_denied_tools(completed[-1]['proof'],charged)
    except StaleDeniedToolReplayError as exc:
        dump(case/'native-tool-attempts.json',dict(operation_notes,manual_review_required=True,classification='stale_denied_tool_replay',stale_tool_call_ids=exc.tool_call_ids,accepted_host_rounds=n))
        raise
    if truncated:
        archive=case/'interruptions'/('agy-truncated-native-'+str(time.time_ns()));archive.mkdir(parents=True)
        (archive/'native-original.jsonl.gz').write_bytes(source.read_bytes());temporary=case/'native-repaired.gz'
        with gzip.open(temporary,'wt')as file:file.writelines(lines)
        temporary.replace(source)
    if operation_notes['prompt_windows_with_tool_attempts']or operation_notes['historical_tool_replay_notifications']:
        dump(case/'native-tool-attempts.json',dict(operation_notes,accepted_host_rounds=n,source_native_sha256=sha(source),recorded_at=time.time()))
    if len(completed)==n:return None
    entry=completed[-1];call=next((w for w in wire if w['index']==entry['declaration']['wire_index']),None)
    if not call or call.get('kind')!=KIND or call.get('observation_round')!=n+1:raise RuntimeError('Pending native AGY result has no matching declared image batch')
    declaration_images_match(entry['declaration'],call,cfg)
    if call.get('native_client_finished')and call.get('native_result_sha256')!=entry['result_sha256']:raise RuntimeError('Recorded native AGY terminal envelope differs')
    archive=case/'interruptions'/('agy-committed-result-'+str(time.time_ns()));archive.mkdir(parents=True)
    (archive/'wire-before-recovery.json').write_bytes((case/'wire.json').read_bytes())
    call.update(native_client_finished=True,native_output_proof=entry['proof'],native_result_sha256=entry['result_sha256'],native_session_id=entry['proof']['native_session_id'],recovered_from_committed_native_result=True)
    dump(case/'wire.json',wire)
    evidence={'native_result_success':True,'native_stop_reason':'end_turn','native_result_sha256':entry['result_sha256'],'native_output_proof':entry['proof'],'original_agy_session_retained':True,'no_new_model_invocation':True,'snapshot':str(archive.relative_to(case))}
    reply={'round':n+1,'response':entry['response'],'seconds':None,'wire':call,'evidence':evidence};dump(archive/'recovered-public-reply.json',reply);return reply
