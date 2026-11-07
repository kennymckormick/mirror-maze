"""Reconcile completed public replies before resuming an interrupted case."""
import gzip,json,time
from pathlib import Path
from .common import dump,sha
from .settings import planning_request
from .transport import metadata_response,complete_json_document,closed_json_evidence


def recover_reply(case,cfg):
    if cfg.get('observation_modality')=='text':
        from .text_mode import recover_text_reply
        return recover_text_reply(case,cfg)
    if cfg.get('harness')=='agy'and cfg.get('native_provider')is True:
        from .agy_recovery import recover_agy_reply
        return recover_agy_reply(case,cfg)
    if cfg.get('harness')=='cursor':
        from .cursor_recovery import recover_cursor_reply
        return recover_cursor_reply(case,cfg)
    case=Path(case);p=json.loads((case/'progress.json').read_text());n=len(p['events'])
    wire=json.loads((case/'wire.json').read_text())
    calls=[w for w in wire if w.get('observation_round')==n+1 and w.get('http_status')==200 and w.get('main_inference',bool(w.get('images')))and w.get('images')and not(w.get('error')or w.get('transport_error'))and (w.get('public_text_chars',0)>0 or cfg.get('harness'))]
    invalidated=set(json.loads((case/'invalidated-wire-indices.json').read_text()).get('indices',[]))if(case/'invalidated-wire-indices.json').exists()else set()
    calls=[w for w in calls if w['index']not in invalidated]
    if cfg.get('recover_closed_json_streams'):
        checked=[]
        for w in calls:
            if w.get('private_response_file'):
                with gzip.open(case/w['private_response_file'],'rb')as f:meta=metadata_response(f.read())
                if meta['response_error']:continue
            checked.append(w)
        calls=checked
    answers=[];truncated=False;lines=[]
    if not cfg.get('harness'):
        history=json.loads((case/'dialogue.json').read_text())if(case/'dialogue.json').exists()else[]
        answers=[m['content']for m in history if m['role']=='assistant']
    else:
        candidate=None;h=cfg['harness']
        try:
            with gzip.open(case/'native.jsonl.gz','rt')as f:
                for line in f:
                    try:e=json.loads(line)
                    except ValueError:continue
                    lines.append(line)
                    if h=='codex':
                        if e.get('type')=='item.completed'and e.get('item',{}).get('type')=='agent_message':candidate=e['item']['text']
                        if e.get('type')=='turn.completed':
                            assert candidate is not None;answers.append(candidate);candidate=None
                        if e.get('type')=='turn.failed':candidate=None
                    elif h=='claude':
                        if e.get('type')=='result'and not e.get('is_error'):answers.append(e.get('result',''))
                    elif h=='agy':
                        if e.get('event')=='result':
                            r=e['result']
                            if r.get('status')=='SUCCESS':
                                response=r.get('response','')
                                committed=len(answers)<n and response==p['events'][len(answers)]['response']
                                # AGY can emit a blank success envelope after every
                                # upstream request failed. It is not a model reply.
                                if response.strip()or committed or(len(answers)==n and calls):answers.append(response)
                            elif r.get('response')and len(answers)<n and r['response']==p['events'][len(answers)]['response']:
                                answers.append(r['response'])
        except EOFError:truncated=True
    if len(answers)not in(n,n+1):raise RuntimeError('Ambiguous native/public answer count; do not resample')
    if not all(e['response']==a for e,a in zip(p['events'],answers)):raise RuntimeError('Committed response prefix differs; do not resample')
    if truncated:
        # Repair the container before returning any native replay descriptor:
        # the original client resume path also reads this gzip archive.
        backup=case/'interruptions'/('truncated-native-'+str(time.time_ns()));backup.mkdir(parents=True)
        (backup/'native-original.jsonl.gz').write_bytes((case/'native.jsonl.gz').read_bytes())
        temp=case/'native-repaired.gz'
        with gzip.open(temp,'wt')as f:f.writelines(lines)
        temp.replace(case/'native.jsonl.gz')
    closed_evidence=None
    if not calls and cfg.get('recover_closed_json_streams'):
        # Choose the first full JSON document, never the last/best retry. Keep
        # the original missing-terminal error and provider stream untouched.
        for candidate in wire:
            if candidate['index']in invalidated:continue
            if candidate.get('observation_round')!=n+1 or candidate.get('http_status')!=200 or candidate.get('transport_error')or not candidate.get('private_response_file'):continue
            if not candidate.get('main_inference')or not candidate.get('images'):continue
            if cfg.get('source_alias')and candidate.get('source_alias')and cfg['source_alias']!=candidate['source_alias']:continue
            if 'truncated_stream'not in str(candidate.get('error','')):continue
            with gzip.open(case/candidate['private_response_file'],'rb')as f:data=f.read()
            meta=metadata_response(data)
            if meta['response_error']or not complete_json_document(meta['text']):continue
            if len(answers)==n+1 and answers[-1].strip()!=meta['text'].strip():raise RuntimeError('Saved native/public completion differs from the earliest closed JSON reply; do not resample')
            closed_evidence=closed_json_evidence(candidate,case,meta['text']);calls=[candidate]
            if cfg.get('harness')and len(answers)==n:
                assert cfg['harness']=='agy'and candidate['path']=='/v1/chat/completions'
                archive=case/'interruptions'/('closed-json-native-replay-'+str(time.time_ns()));archive.mkdir(parents=True)
                (archive/'native-before-replay.jsonl.gz').write_bytes((case/'native.jsonl.gz').read_bytes())
                proof={'kind':'native_upstream_replay','round':n+1,'wire_index':candidate['index'],'private_response_sha256':closed_evidence['private_response_sha256'],'prior_public_replies_match':True,'no_new_inference':True,'closed_json_stream_recovery':closed_evidence,'snapshot':str(archive.relative_to(case))}
                dump(archive/'replay-proof.json',proof);return proof
            break
    raw=None;call=None
    if len(answers)==n+1:
        if not calls:raise RuntimeError('Completed native answer has no corresponding accepted wire record')
        raw=answers[-1];call=calls[-1]
    elif calls:
        if cfg.get('harness'):
            # A complete provider JSON message can arrive despite stream=True.
            # Re-deliver these exact bytes as SSE to the original native client,
            # so it commits its own transcript before the host executes actions.
            if cfg['harness']=='claude'and len(calls)==1 and calls[0].get('private_response_file'):
                call=calls[0];source=case/call['private_response_file']
                with gzip.open(source,'rb')as f:data=f.read()
                try:message=json.loads(data)
                except ValueError:message={}
                if message.get('type')=='message'and message.get('role')=='assistant'and message.get('stop_reason')in('end_turn','stop_sequence')and metadata_response(data)['text']:
                    archive=case/'interruptions'/('native-wire-replay-'+str(time.time_ns()));archive.mkdir(parents=True)
                    (archive/'native-before-replay.jsonl.gz').write_bytes((case/'native.jsonl.gz').read_bytes())
                    proof={'kind':'native_upstream_replay','round':n+1,'wire_index':call['index'],'private_response_sha256':sha(source),'prior_public_replies_match':True,'no_new_inference':True,'snapshot':str(archive.relative_to(case))}
                    dump(archive/'replay-proof.json',proof);return proof
            raise RuntimeError('Upstream public answer exists without reconciled native completion; inspect saved records before resampling')
        call=calls[-1];source=case/call['private_response_file']
        with gzip.open(source,'rb')as f:raw=metadata_response(f.read())['text']
        assert raw
        feed=json.loads((case/'feed.json').read_text());assert feed['round']==n+1
        feedback=json.loads(feed['feedback']);text=planning_request(cfg,n+1,cfg['max_planning'],len(feed['frames']),feedback)
        dump(case/'dialogue.json',history+[{'role':'user','content':text},{'role':'assistant','content':raw}])
    if raw is None:return None
    archive=case/'interruptions'/('committed-reply-'+str(time.time_ns()));archive.mkdir(parents=True)
    if cfg.get('harness'):(archive/'native-before-recovery.jsonl.gz').write_bytes((case/'native.jsonl.gz').read_bytes())
    proof={'reason':'Replay saved completed public answer once; original session and decision budget retained','source_wire_index':call['index'],'source_wire_sha256':sha(case/'wire.json'),'source_snapshot':str(archive.relative_to(case)),'prior_public_replies_match':True}
    if closed_evidence:proof['closed_json_stream_recovery']=closed_evidence
    recovered={'round':n+1,'response':raw,'seconds':None,'wire':call,'evidence':proof};dump(archive/'recovered-public-reply.json',recovered)
    return recovered
