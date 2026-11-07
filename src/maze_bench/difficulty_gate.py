"""Prospective difficulty stop rule; skipped trials never become scored failures."""
import json,time
from pathlib import Path
from .common import dump,sha

LEVELS=('easy','medium','hard')
SKIPPED='skipped_by_difficulty_rule'
SCORED={'success','planning_cap'}


def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except(FileNotFoundError,json.JSONDecodeError):return default


def difficulty_decisions(rows,policy):
    if not policy.get('enabled'):return {}
    decisions={}
    for target in rows:
        if target.get('started')or target.get('audited')or target['status']in SCORED|{SKIPPED,'withdrawn_by_user'}:continue
        earlier=LEVELS[:LEVELS.index(target['difficulty'])]
        groups=[(level,[r for r in rows if r['model']==target['model']and r['difficulty']==level])for level in earlier]
        failed=next(((level,group)for level,group in reversed(groups)if group and all(r.get('audited')and r['status']=='planning_cap'for r in group)),None)
        if failed:
            level,group=failed
            decisions[target['id']]={'action':'skip','failed_level':level,'prerequisite_ids':[r['id']for r in group]}
        elif any(group and not any(r.get('audited')and r['status']=='success'for r in group)for _,group in groups):
            decisions[target['id']]={'action':'wait','reason':'Waiting for a lower difficulty to pass or finish all audited trials.'}
    return decisions


def observations(manifest):
    rows=[]
    for r in manifest['rows']:
        case=Path(r['case_dir']);reused=r['kind']=='reused'
        result_path=Path(r['source_dir'])/'result.json'if reused else case/'result.json'
        audit_path=Path(r['reuse_audit'])if reused else case/'audited.json'
        result=read(result_path,{})
        audit=read(audit_path,{})
        audited=bool(r.get('reuse_audit_passed'))if reused else bool(audit.get('audit_passed')and result and audit.get('result_sha256')==sha(result_path))
        status=result.get('status','queued')
        if(case/'skipped.json').exists():status=SKIPPED;audited=False
        if(case/'withdrawn-by-user.json').exists():status='withdrawn_by_user';audited=False
        rows.append({'id':r['id'],'model':r['model'],'difficulty':r['difficulty'],'mode':r['mode'],'status':status,'audited':audited,
                     'started':any((case/f).exists()for f in('worker.json','progress.json','wire.json')),
                     'evidence':{'id':r['id'],'status':status,'result_path':str(result_path),'result_sha256':sha(result_path)if result else None,'audit_path':str(audit_path),'audit_sha256':sha(audit_path)if audit else None}})
    return rows


def apply_difficulty_policy(out,manifest):
    policy=manifest.get('difficulty_stop_policy',{})
    if not policy.get('enabled'):return {'skipped':set(),'waiting':set()}
    observed=observations(manifest);by_id={r['id']:r for r in observed}
    decisions=difficulty_decisions(observed,policy);skipped=set();waiting=set()
    for row in manifest['rows']:
        case=Path(row['case_dir']);receipt=read(case/'skipped.json')
        if receipt:
            # Recompute the rule from independently hashed original results.
            target=dict(by_id[row['id']],status='queued',started=False,audited=False)
            check=difficulty_decisions([target if r['id']==target['id']else r for r in observed],policy).get(target['id'],{})
            assert check.get('action')=='skip'and check['failed_level']==receipt['failed_level']
            assert receipt['prerequisite_evidence']==[by_id[i]['evidence']for i in check['prerequisite_ids']]
            assert not by_id[row['id']]['started'],'An already-started trial cannot be skipped'
            proof=read(case/'skip-audit.json',{})
            assert proof.get('audit_passed')and proof.get('skip_sha256')==sha(case/'skipped.json')
            skipped.add(row['id']);continue
        decision=decisions.get(row['id'],{})
        if decision.get('action')=='wait':waiting.add(row['id'])
        if decision.get('action')!='skip':continue
        assert row['kind']=='fresh'and not by_id[row['id']]['started']
        reason='Skipped: all '+str(len(decision['prerequisite_ids']))+' scheduled '+decision['failed_level']+' trials for this model ended at their planning caps and passed replay audit.'
        dump(case/'skipped.json',{'schema':'mirror-maze-difficulty-skip/v1','status':SKIPPED,'case_id':row['id'],'model':row['model'],'difficulty':row['difficulty'],'failed_level':decision['failed_level'],'reason':reason,'at':time.time(),'policy':policy,
                                'prerequisite_evidence':[by_id[i]['evidence']for i in decision['prerequisite_ids']],'model_inference_performed':False,'counts_as_failure':False,'original_target_cap':row['target_cap']})
        dump(case/'skip-audit.json',{'audit_passed':True,'at':time.time(),'skip_sha256':sha(case/'skipped.json'),'all_prerequisites_audited_planning_cap':True,'trial_never_started':True,'counts_as_scored_outcome':False})
        skipped.add(row['id'])
    return {'skipped':skipped,'waiting':waiting}
