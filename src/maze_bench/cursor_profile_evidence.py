"""Verify an already executed Cursor reply against immutable official profile data.

This is a separate evidence path, never a fabricated native stdout result.
Profile snapshots contain private conversation data and must not be published.
"""
import hashlib,json,os,shutil,sqlite3
from pathlib import Path
from .common import dump,sha


def strings(obj):
    if isinstance(obj,str):yield obj
    elif isinstance(obj,dict):
        for value in obj.values():yield from strings(value)
    elif isinstance(obj,list):
        for value in obj:yield from strings(value)


def child_path(root,name):
    root=Path(root).resolve();path=(root/name).resolve()
    if Path(name).is_absolute()or not path.is_relative_to(root):raise RuntimeError('Profile evidence path escapes its protected directory')
    return path


def create_profile_certificate(case,cfg,round_number,destination):
    case=Path(case);destination=Path(destination);destination.mkdir(parents=True,exist_ok=True);destination.chmod(0o700)
    progress=json.loads((case/'progress.json').read_text());event=progress['events'][round_number-1]
    assert event['round']==round_number
    wire=json.loads((case/'wire.json').read_text());call=next(w for w in wire if w['index']==event['input_request_index'])
    session=call['native_session_id'];profile=Path(cfg['profile_root'])
    transcripts=list(profile.glob('data/projects/*/agent-transcripts/'+session+'/'+session+'.jsonl'))
    databases=list(profile.glob('config/chats/*/'+session+'/store.db'))
    assert len(transcripts)==len(databases)==1,'Official profile sources must be unambiguous'
    transcript,source_db=transcripts[0],databases[0]
    shutil.copy2(transcript,destination/'transcript.jsonl');(destination/'transcript.jsonl').chmod(0o600)
    # sqlite backup includes a committed WAL while producing one immutable file.
    source=sqlite3.connect(source_db.resolve().as_uri()+'?mode=ro',uri=True)
    target=sqlite3.connect(destination/'store.db')
    try:source.backup(target)
    finally:target.close();source.close()
    (destination/'store.db').chmod(0o600)
    records=[json.loads(line)for line in (destination/'transcript.jsonl').read_text().splitlines()]
    text=event['response'];digest=hashlib.sha256(text.encode()).hexdigest()
    assistant_lines=[i+1 for i,r in enumerate(records)if r.get('role')=='assistant'and any(s==text for s in strings(r))]
    request_lines=[i+1 for i,r in enumerate(records)if r.get('role')=='user'and any(f'MAZE_PLANNING_REQUEST {round_number}/'in s for s in strings(r))]
    assert assistant_lines and request_lines and request_lines[-1]<assistant_lines[-1]
    assert records[-1].get('type')=='turn_ended'and records[-1].get('status')=='success'
    db=sqlite3.connect((destination/'store.db').resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:blobs=[{'id':ident,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}for ident,data in db.execute('select id,data from blobs where instr(data,?)>0',(text.encode(),))]
    finally:db.close()
    assert blobs
    certificate={'schema':'mirror-maze-cursor-profile-completion/v1','round':round_number,'native_session_id':session,'public_response_sha256':digest,
                 'wire_index':call['index'],'native_result_sha256_recorded_by_host':call['native_result_sha256'],
                 'images':[{'file':image['file'],'sha256':image['sha256']}for image in call['images']],
                 'transcript_file':'transcript.jsonl','transcript_sha256':sha(destination/'transcript.jsonl'),'assistant_lines':assistant_lines,'request_lines':request_lines,'terminal_line':len(records),
                 'store_file':'store.db','store_sha256':sha(destination/'store.db'),'matching_blobs':blobs,
                 'source_transcript_relative':str(transcript.relative_to(profile)),'source_db_relative':str(source_db.relative_to(profile)),
                 'source_progress_sha256':sha(case/'progress.json'),'source_native_stdout_sha256':sha(case/'native.jsonl.gz'),
                 'original_stdout_preserved':True,'native_stdout_result_fabricated':False,'private_evidence_do_not_publish':True}
    dump(destination/'certificate.json',certificate);(destination/'certificate.json').chmod(0o600)
    verify_profile_certificate(case,cfg,destination/'certificate.json',progress,wire)
    return certificate


def verify_profile_certificate(case,cfg,path,progress=None,wire=None):
    case=Path(case);path=Path(path);certificate=json.loads(path.read_text());base=path.parent
    assert certificate['schema']=='mirror-maze-cursor-profile-completion/v1'
    assert certificate['original_stdout_preserved']and not certificate['native_stdout_result_fabricated']
    progress=progress or json.loads((case/'progress.json').read_text());wire=wire or json.loads((case/'wire.json').read_text())
    number=certificate['round'];assert 1<=number<=len(progress['events'])
    event=progress['events'][number-1];text=event['response'];digest=hashlib.sha256(text.encode()).hexdigest()
    assert digest==certificate['public_response_sha256']
    selected=next(w for w in wire if w['index']==event['input_request_index'])
    assert selected['index']==certificate['wire_index']and selected['observation_round']==number and selected.get('native_client_finished')
    assert selected['native_session_id']==certificate['native_session_id']
    assert selected['native_result_sha256']==certificate['native_result_sha256_recorded_by_host']
    assert selected['native_output_proof']['selected_public_text_sha256']==digest
    assert certificate['images']==[{'file':i['file'],'sha256':i['sha256']}for i in selected['images']]
    assert [i['file']for i in certificate['images']]==event.get('input_frames_sent',event['input_frames'])
    for image in certificate['images']:assert sha(Path(cfg['cache_dir'])/image['file'])==image['sha256']
    transcript=child_path(base,certificate['transcript_file']);store=child_path(base,certificate['store_file'])
    assert sha(transcript)==certificate['transcript_sha256']and sha(store)==certificate['store_sha256']
    records=[json.loads(line)for line in transcript.read_text().splitlines()]
    for line in certificate['assistant_lines']:
        record=records[line-1];assert record.get('role')=='assistant'and any(s==text for s in strings(record))
    for line in certificate['request_lines']:
        record=records[line-1];assert record.get('role')=='user'and any(f'MAZE_PLANNING_REQUEST {number}/'in s for s in strings(record))
    assert certificate['request_lines']and certificate['assistant_lines']and max(certificate['request_lines'])<max(certificate['assistant_lines'])
    terminal=records[certificate['terminal_line']-1]
    assert certificate['terminal_line']==len(records)and terminal.get('type')=='turn_ended'and terminal.get('status')=='success'
    db=sqlite3.connect(store.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        assert certificate['matching_blobs']
        for blob in certificate['matching_blobs']:
            data=db.execute('select data from blobs where id=?',(blob['id'],)).fetchone()[0]
            assert len(data)==blob['bytes']and hashlib.sha256(data).hexdigest()==blob['sha256']and text.encode()in data
    finally:db.close()
    return {'round':number,'public_response_sha256':digest,'native_session_id':certificate['native_session_id'],'evidence_type':'official_cursor_profile_completion','native_stdout_result_fabricated':False,'certificate_sha256':sha(path)}


def profile_completions(case,cfg,progress=None,wire=None):
    case=Path(case);index=case/'profile-completions.json'
    if not index.exists():return {}
    manifest=json.loads(index.read_text());assert manifest['schema']=='mirror-maze-cursor-profile-completions/v1'
    result={}
    for name in manifest['certificates']:
        certificate=child_path(case,name);proof=verify_profile_certificate(case,cfg,certificate,progress,wire)
        assert proof['round']not in result;result[proof['round']]=proof
    return result
