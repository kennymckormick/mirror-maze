"""Recover committed native Cursor results without invoking a model again."""
import gzip,hashlib,json,re,time
from pathlib import Path
from .common import dump
from .cursor_protocol import completed_cursor_reply
from .cursor_profile_evidence import profile_completions,strings


def recover_cursor_reply(case,cfg):
    case=Path(case);progress=json.loads((case/'progress.json').read_text());n=len(progress['events'])
    source=case/'native.jsonl.gz';lines=[];events=[];truncated=False
    if not source.exists():
        if n==0:return None
        raise RuntimeError('Native Cursor transcript is missing after committed decisions')
    try:
        with gzip.open(source,'rt')as f:
            for line in f:
                try:event=json.loads(line)
                except ValueError:continue
                lines.append(line);events.append(event)
    except EOFError:truncated=True
    answers=[];completed=[];native_rounds=[];result_hashes=[];group=[];offset=0
    for index,event in enumerate(events):
        if event.get('type')=='system'and event.get('subtype')=='init':group=[];offset=index
        group.append(event)
        if event.get('type')=='result':
            try:text,proof,result=completed_cursor_reply(group)
            except RuntimeError:continue
            proof['native_json_event_offset']=offset;answers.append(text);completed.append((proof,result))
            requests={int(number)for item in group if item.get('type')=='user'for value in strings(item)for number in re.findall(r'MAZE_PLANNING_REQUEST\s+(\d+)/',value)}
            native_rounds.append(next(iter(requests))if len(requests)==1 else None)
            result_hashes.append(hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest())
    wire=json.loads((case/'wire.json').read_text())
    certificates=profile_completions(case,cfg,progress,wire);position=0
    for number,event in enumerate(progress['events'],1):
        expected=next(w for w in wire if w['index']==event['input_request_index'])
        # Equal public text can recur in successive rounds. A profile-backed
        # missing result must not consume a later round's native completion.
        if number in certificates and(position==len(answers)or native_rounds[position]is None or native_rounds[position]!=number):continue
        if position<len(answers)and event['response']==answers[position]and result_hashes[position]==expected['native_result_sha256']and native_rounds[position]in(None,number):position+=1
        elif number in certificates:continue
        else:raise RuntimeError('Native Cursor committed response prefix differs or is missing; do not resample')
    if len(answers)-position not in(0,1):raise RuntimeError('Native Cursor completion count is ambiguous; do not resample')
    if len(answers)>position and native_rounds[position]not in(None,n+1):raise RuntimeError('Pending Cursor completion belongs to a different planning round; do not resample')
    if truncated:
        archive=case/'interruptions'/('cursor-truncated-native-'+str(time.time_ns()));archive.mkdir(parents=True)
        (archive/'native-original.jsonl.gz').write_bytes(source.read_bytes());temp=case/'native-repaired.gz'
        with gzip.open(temp,'wt')as f:f.writelines(lines)
        temp.replace(source)
    if len(answers)==position:return None
    candidates=[w for w in wire if w.get('observation_round')==n+1 and w.get('kind')=='native_cursor_image_attachment']
    if not candidates:raise RuntimeError('Committed Cursor result has no declared image attachments')
    proof,result=completed[position];digest=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    call=next((w for w in candidates if w.get('native_result_sha256')==digest),candidates[-1])
    archive=case/'interruptions'/('cursor-committed-result-'+str(time.time_ns()));archive.mkdir(parents=True)
    (archive/'wire-before-recovery.json').write_bytes((case/'wire.json').read_bytes())
    call.update(native_client_finished=True,native_output_proof=proof,native_result_sha256=digest,
                native_session_id=result.get('session_id'),recovered_from_committed_native_result=True)
    dump(case/'wire.json',wire)
    evidence={'native_result_success':True,'native_result_sha256':digest,'native_output_proof':proof,
              'original_cursor_session_retained':True,'no_new_model_invocation':True,'snapshot':str(archive.relative_to(case))}
    recovered={'round':n+1,'response':answers[position],'seconds':None,'wire':call,'evidence':evidence}
    dump(archive/'recovered-public-reply.json',recovered);return recovered
