"""Recover a fully recorded native answer after host exit, without resampling it."""
import argparse,getpass,gzip,json,os,shutil,time,uuid
from pathlib import Path
from maze_bench.common import dump,sha
from maze_bench.runner import run

p=argparse.ArgumentParser();p.add_argument('case',type=Path);a=p.parse_args();case=a.case.resolve()
key=os.environ.get('MIRROR_MAZE_API_KEY')or getpass.getpass('API key (hidden): ')
progress=json.loads((case/'progress.json').read_text());episode=json.loads((case/'episode.json').read_text());cfg=json.loads((case/'config.json').read_text());wire=json.loads((case/'wire.json').read_text())
assert progress['status']=='running'and progress['state']==episode['state']and cfg['harness']=='claude'
lines=[];answers=[];ended=False
try:
    with gzip.open(case/'native.jsonl.gz','rt')as f:
        for line in f:
            try:e=json.loads(line)
            except ValueError:continue
            lines.append(line)
            if e.get('type')=='result'and not e.get('is_error'):answers.append(e)
except EOFError:ended=True
n=len(progress['events'])
assert len(answers)in(n,n+1)
assert all(e['response']==answer.get('result')for e,answer in zip(progress['events'],answers))
backup=case/'interruptions'/('orphan-'+str(time.time_ns()));backup.mkdir(parents=True)
for name in ('config.json','progress.json','episode.json','result.json','wire.json','native.jsonl.gz'):shutil.copy2(case/name,backup/name)
if ended:
    temp=case/('native.'+uuid.uuid4().hex+'.tmp')
    with gzip.open(temp,'wt')as f:f.writelines(lines)
    temp.replace(case/'native.jsonl.gz')
recovered=None
if len(answers)==n+1:
    calls=[c for c in wire if c.get('observation_round')==n+1 and c.get('http_status')==200 and c.get('main_inference')]
    assert calls and answers[-1].get('result')
    recovered={'round':n+1,'response':answers[-1]['result'],'seconds':answers[-1]['duration_ms']/1000,'wire':calls[-1],'evidence':{'reason':'Complete native public result existed after last host checkpoint; replay exactly once, no new inference','source_file':str((backup/'native.jsonl.gz').relative_to(case)),'source_sha256':sha(backup/'native.jsonl.gz'),'result_index':len(answers),'prior_public_replies_match':True}}
    dump(backup/'recovered-public-reply.json',recovered)
progress.update(status='infrastructure_error',error='Host stopped after last saved checkpoint; preserve pending complete native reply')
dump(case/'progress.json',progress)
dump(case/'result.json',{'case_id':case.name,'status':'infrastructure_error','initial_frame':progress['events'][0]['input_frames'][0]})
cfg.update(request_timeout=3600,harness_timeout=3600,planning_timeout=3600)
print(json.dumps({'checkpoint':n,'recovering_existing_reply':bool(recovered),'no_resampling':True}),flush=True)
result=run(cfg,key,case,cap=cfg.get('max_planning',200),resume=True,recovered_reply=recovered)
print('RECOVERY_RESULT '+json.dumps(result,ensure_ascii=False),flush=True)
