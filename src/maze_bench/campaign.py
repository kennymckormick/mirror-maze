"""Balanced campaign: immutable reuse ledger and durable per-model case slots."""
import argparse,collections,datetime,fcntl,getpass,hashlib,json,os,shutil,signal,subprocess,sys,time
from pathlib import Path
from .audit import validate
from .common import dump,sha
from .recovery import recover_reply
from .runner import run
from .settings import BALANCED_SIDES,BALANCED_CAPS,environment_options,frame_name
from .difficulty_gate import SKIPPED,apply_difficulty_policy
from .host_interventions import load_interventions,intervention_metadata
from .comparison_stats import clean_eligible,clean_statistics

TERMINAL={'success','planning_cap'}
LEVELS=('easy','medium','hard')


class AdoptedProcess:
    def __init__(self,pid,ident):self.pid=pid;self.ident=ident;self.returncode=None
    def poll(self):
        p=subprocess.run(['ps','-p',str(self.pid),'-o','stat=,command='],capture_output=True,text=True)
        if p.returncode==0 and'maze_bench.campaign worker'in p.stdout and self.ident in p.stdout and not p.stdout.lstrip().startswith('Z'):return None
        self.returncode=-1;return self.returncode


def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (FileNotFoundError,json.JSONDecodeError):return default


def case_id(row):return '-'.join((row['mode'],row['difficulty'],row['model'],row['topology'],row['seed']))


def pending_levels(rows,finished):
    return {model:next((level for level in LEVELS if any(r['model']==model and r['difficulty']==level and r['id']not in finished for r in rows)),'complete')for model in dict.fromkeys(r['model']for r in rows)}


def launchable_rows(rows,finished,active,blocked,limit=4,rolling=False):
    stages=pending_levels(rows,finished);models={r['id']:r['model']for r in rows}
    counts=collections.Counter(models[ident]for ident in active);ready=[]
    assert all(n<=limit for n in counts.values())
    for row in rows:
        ident=row['id'];model=row['model']
        if ident in finished or ident in active or ident in blocked or(not rolling and row['difficulty']!=stages[model]):continue
        if counts[model]<limit:ready.append(row);counts[model]+=1
    return stages,ready


def pin_harnesses(out,evidence):
    m=read(out/'manifest.json');evidence=Path(evidence).resolve();legacy=evidence.parent/'mirror-maze-eval-20261101/final-runs'
    examples={'codex':legacy/'gpt-square-MIRROR-042','claude':legacy/'kimi-square-MIRROR-042','agy':legacy/'gemini-square-MIRROR-042'}
    pinned={}
    for name,case in examples.items():
        invocation=read(case/'native-invocation.json',{})
        recorded=(invocation.get('command')or[None])[0]
        candidates=[recorded,shutil.which(name),str(Path.home()/'.local/share/agent-harness/bin'/name)]
        found=next((str(Path(p).resolve())for p in candidates if p and Path(p).is_file()and os.access(p,os.X_OK)),None)
        if not found:raise RuntimeError('Native executable missing: '+name)
        pinned[name]={'path':found,'sha256':sha(found),'recorded_invocation':recorded}
    for row in m['rows']:
        if row['config'].get('harness'):row['config']['harness_executable']=pinned[row['config']['harness']]['path']
    m['harness_executables']=pinned;dump(Path(out)/'manifest.json',m);return pinned


def prepare(evidence,out,base_url):
    evidence=Path(evidence).resolve();out=Path(out).resolve()
    if(out/'manifest.json').exists():
        existing=read(out/'manifest.json');assert existing['base_url']==base_url
        return existing
    inventory=read(evidence/'artifacts/revised-protocol-review-20261104/inventory.json')
    policy=read(evidence/'artifacts/revised-protocol-review-20261104/approved-policy.json');assert policy['reuse_budget_mismatched_sol_successes']
    spec=read(Path(__file__).resolve().parents[2]/'examples/five-models.json')
    models={m['model_key']:m for m in spec['models']}
    assert set(models)=={'deepseek','kimi','gpt','gemini','seed'}
    defaults=dict(spec['defaults']);defaults.pop('side_length',None)
    rows=[];out.mkdir(parents=True,exist_ok=True);cache=out/'image-cache';cache.mkdir(exist_ok=True)
    run_id=hashlib.sha256(str(out).encode()).hexdigest()[:16]
    sources_seen={};audits=[]
    for original in inventory['rows']:
        r=dict(original);r['id']=case_id(r);r['target_cap']=BALANCED_CAPS[r['difficulty']]
        assert r['side_length']==BALANCED_SIDES[r['topology']][r['difficulty']]
        r['reuse_type']='same_map_and_cap'if r['reuse_class']=='same_map_and_cap'else'budget_mismatched_success'if r['reuse_class']=='different_budget_success'else None
        r['kind']='reused'if r['reuse_type']else'fresh';r['case_dir']=str(out/'cases'/r['mode']/r['difficulty']/(r['model']+'-'+r['topology']+'-'+r['seed']))
        model=dict(models[r['model']]);model['model_label']=model['model_label'].replace('（替代源）','')
        cfg={**defaults,**model,'difficulty_schedule':'balanced-v1','difficulty':r['difficulty'],'side_length':r['side_length'],'max_planning':r['target_cap'],'topology':r['topology'],'seed':r['seed'],'case_id':r['id'],'run_id':run_id,'api_base':base_url,'cache_dir':str(cache),'direct_observations':True,'observation_only':False,'profile_root':str(Path.home()/'.local/share/mirror-maze-benchmark/profiles'/run_id/r['id'])}
        cfg['harness']=model['harness']if r['mode']=='harness'else None
        if cfg['harness']:cfg['model_label']=model.get('native_model',cfg['model_label'])
        r['config']=cfg
        if r['kind']=='reused':
            source_name=r['model']+'-'+r['topology']+'-'+r['seed']
            if r['mode']=='api':source=evidence/'runs/api'/source_name;source_cache=evidence.parent/'mirror-maze-eval-20261101/image-cache'
            elif r['topology']=='square'and r['side_length']==11:source=evidence/'runs/sol-medium-codex'/source_name;source_cache=source.parent/'image-cache'
            else:source=evidence.parent/'mirror-maze-eval-20261101/final-runs'/source_name;source_cache=source.parent.parent/'image-cache'
            result=read(source/'result.json');progress=read(source/'progress.json');assert result['status']in TERMINAL
            assert result['planning_rounds']==r['existing_planning']and result['planning_cap']==r['existing_cap']
            assert result['planning_rounds']<=r['target_cap']
            if r['reuse_type']=='budget_mismatched_success':assert result['status']=='success'
            hashes={name:sha(source/name)for name in('config.json','result.json','progress.json','episode.json','wire.json')}
            audit_path=out/'reuse-audits'/(r['id']+'.json')
            audit=validate([source],source_cache,audit_path);assert audit['all_checked_invariants_passed']
            assert hashes=={name:sha(source/name)for name in hashes}
            r.update(source_dir=str(source),source_cache=str(source_cache),source_hashes=hashes,source_status=result['status'],source_planning_cap=result['planning_cap'],source_difficulty=r['existing_difficulty'],source_planning_rounds=result['planning_rounds'],source_actions=result['actions'],source_collisions=result['collisions'],source_visited=progress['state']['visited'],reuse_audit=str(audit_path),reuse_audit_passed=True)
            sources_seen[str(source_cache)]=source_cache;audits.append(r['id'])
        rows.append(r)
    assert len(rows)==80 and len({r['id']for r in rows})==80
    assert sum(r['kind']=='reused'for r in rows)==24 and sum(r['kind']=='fresh'for r in rows)==56
    assert sum(r['kind']=='fresh'and r['mode']=='harness'for r in rows)==46
    assert all(r['difficulty']=='easy'for r in rows if r['mode']=='api')
    # Immutable PNGs are safe to share across labels, models and modes at one geometry.
    linked=0
    for source_cache in sources_seen.values():
        for p in source_cache.glob('*.png'):
            dest=cache/p.name
            if not dest.exists():
                try:os.link(p,dest)
                except OSError:dest.write_bytes(p.read_bytes())
                linked+=1
    manifest={'schema':'mirror-maze-balanced-campaign/v1','created_at':time.time(),'base_url':base_url,'models':list(models),'model_specs':models,'levels':list(LEVELS),'sides':BALANCED_SIDES,'caps':BALANCED_CAPS,'per_model_concurrency':4,'stage_policy':'per_model_easy_then_medium_then_hard','api_difficulties':['easy'],'targets':80,'reuse':24,'fresh':56,'conditional_reuse':4,'rows':rows,'reuse_audits':audits,'cached_original_pngs_available':linked,'credentials_saved':False,'material_regeneration_after_complete_only':True,'public_launch_models':['deepseek','kimi','gpt','gemini']}
    dump(out/'manifest.json',manifest);return manifest


def audit_case(row):
    case=Path(row['case_dir']);result=read(case/'result.json')
    assert result and result['status']in TERMINAL
    if row['config'].get('observation_modality')=='text':
        from .text_mode import audit_text
        return audit_text(case,row['config'])
    audit=validate([case],row['config']['cache_dir'],case/'validation.json')
    assert audit['all_checked_invariants_passed'];dump(case/'audited.json',{'complete':True,'audit_passed':True,'result_sha256':sha(case/'result.json'),'validation_sha256':sha(case/'validation.json'),'ended_at':time.time()})
    return result


def worker(out,ident,key):
    if(out/'PAUSED_BY_USER.json').exists():raise RuntimeError('All evaluations are paused by user; explicit resume is required')
    if(out/'PAUSED_BY_REPLACEMENT.json').exists():raise RuntimeError('Native cohort replaced by the audited candidate; no new inference allowed')
    manifest=read(out/'manifest.json');row=next(r for r in manifest['rows']if r['id']==ident);assert row['kind']=='fresh'
    case=Path(row['case_dir']);case.mkdir(parents=True,exist_ok=True)
    if(case/'skipped.json').exists():raise RuntimeError('Trial skipped by the authorized difficulty rule; no inference allowed')
    if(case/'withdrawn-by-user.json').exists():raise RuntimeError('Trial withdrawn by user; no further inference allowed')
    lock=(case/'.case.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cfg=row['config'];dump(case/'worker.json',{'pid':os.getpid(),'started_at':time.time(),'config_sha256':hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest(),'credentials_saved':False})
    attempt=0
    while attempt<cfg.get('infrastructure_attempt_limit',100):
        if cfg.get('harness')=='cursor'and(out/'PAUSED_FOR_KEYCHAIN.json').exists():
            time.sleep(30);continue
        deferred=read(case/'retry-after.json',{})
        if deferred.get('not_before',0)>time.time():
            time.sleep(min(30,deferred['not_before']-time.time()));continue
        if deferred:(case/'retry-after.json').unlink(missing_ok=True)
        attempt+=1
        prior=read(case/'result.json');p=read(case/'progress.json')
        if prior and prior['status']in TERMINAL:
            audit_case(row);return
        resumed=bool(p)
        if resumed:
            if not prior or prior.get('status')=='running':
                # A terminal physical checkpoint can be audited after teardown trouble.
                if p['status']in TERMINAL:
                    result_from_terminal(row,p);audit_case(row);return
                raise RuntimeError('Unclean host checkpoint requires reconciliation; no resampling')
            recovered=recover_reply(case,cfg)
        else:recovered=None
        dump(case/'attempt.json',{'attempt':attempt,'saved_rounds':p['planning_rounds']if p else 0,'recovered_committed_round':recovered['round']if recovered else None,'cap':row['target_cap'],'at':time.time()})
        if cfg.get('observation_modality')=='text':
            from .text_mode import run_text
            result=run_text(cfg,key,case,cap=row['target_cap'],resume=resumed,recovered_reply=recovered)
        else:result=run(cfg,key,case,cap=row['target_cap'],resume=resumed,recovered_reply=recovered)
        print('CASE_RESULT '+json.dumps(result),flush=True)
        if result['status']in TERMINAL:audit_case(row);return
        if cfg.get('observation_modality')=='text'and any(term in str(result.get('error',''))for term in('text_observation_leakage','text_request_too_large','forbidden tool operations','stale_denied_tool_replay')):raise RuntimeError('Text observation contract requires operator review; checkpoint preserved')
        if cfg.get('native_stop_on_empty')and any(term in str(result.get('error',''))for term in('Native AGY completed with empty public text','forbidden tool operations','stale_denied_tool_replay')):
            raise RuntimeError('Native AGY tool/empty-public-reply interruption remains infrastructure; operator repair required before another attempt')
        if not read(case/'wire.json',[])and not read(case/'progress.json')['events']:
            # Nothing reached a model: preserve setup artifacts and retry setup safely.
            archive=case/'setup-failures'/str(time.time_ns());archive.mkdir(parents=True)
            for name in('result.json','progress.json','episode.json','status.json'):
                if(case/name).exists():(case/name).replace(archive/name)
        time.sleep(min(60,5*attempt))
    raise RuntimeError('Infrastructure recovery attempts exhausted; do not turn this into a model failure')


def result_from_terminal(row,p):
    case=Path(row['case_dir']);assert p['status']in TERMINAL
    if row['config'].get('observation_modality')=='text':raise RuntimeError('Terminal text checkpoint needs its saved result reconciled; no new inference allowed')
    cfg=row['config'];events=p['events'];invalid=p['invalid_sequences'];wire=read(case/'wire.json',[])
    cursor=cfg.get('harness')=='cursor';agy_direct=cfg.get('harness')=='agy'and cfg.get('native_provider');direct_native=cursor or agy_direct
    r={'case_id':case.name,'protocol':p.get('protocol','round-prune-v3'),'model':cfg['model_label'],'source_alias':cfg['source_alias'],'harness':cfg.get('harness'),'topology':cfg['topology'],'seed':cfg['seed'],'planning_cap':row['target_cap'],'status':p['status'],'success':p['status']=='success','planning_rounds':len(events),'collisions':p['total_collisions'],'invalid_sequences':invalid,'actions':p['state']['actionCount'],'seconds':p.get('elapsed_seconds'),'api_calls':0 if direct_native else len(wire),'vision_api_calls':0 if direct_native else sum(bool(w.get('images'))for w in wire),'mode':row['mode'],'initial_frame':events[0]['input_frames'][0],'error':None,'cleanup_recovery':'Terminal physical checkpoint completed before runtime teardown; no additional inference.'}
    if direct_native:r.update(native_client_attempts=len(wire),native_vision_turns=sum(bool(w.get('native_client_finished'))for w in wire),observation_evidence_type='native_cursor_cli_image_attachments'if cursor else'native_agy_acp_image_attachments')
    if agy_direct:r['denied_tool_decisions']=sum(e.get('native_output_proof',{}).get('native_invalid_plan_reason')=='verified_denied_tool_without_public_plan'for e in events)
    r.update(intervention_metadata(load_interventions(case,p)))
    if(case/'source-provenance.json').exists():r['source_provenance']=read(case/'source-provenance.json')
    dump(case/'result.json',r);return r


def terminal_audit_supersedes_attention(case,result,audit,attention):
    """A later hash-bound terminal audit retires an older interruption alert."""
    if result.get('status')not in TERMINAL or not audit or not audit.get('audit_passed')or not audit.get('complete'):return False
    if not isinstance(attention.get('at'),(int,float))or audit.get('ended_at',0)<=attention['at']:return False
    validation=read(case/'validation.json',{})
    return bool(validation.get('all_checked_invariants_passed')and audit.get('result_sha256')==sha(case/'result.json')and audit.get('validation_sha256')==sha(case/'validation.json'))


def snapshot(out,active=None,stage=None):
    m=read(out/'manifest.json');rows=[]
    for row in m['rows']:
        r={k:row[k]for k in('id','model','mode','difficulty','topology','side_length','cells','seed','target_cap','kind','reuse_type')}
        if row['kind']=='reused':r.update(status=row['source_status'],planning_rounds=row['source_planning_rounds'],actions=row['source_actions'],collisions=row['source_collisions'],visited=row['source_visited'],audited=True,source_cap=row['source_planning_cap'])
        else:
            case=Path(row['case_dir']);a=read(case/'audited.json');s=read(case/'status.json',{});result=read(case/'result.json',{})
            r.update(status=result.get('status',s.get('status','queued')),planning_rounds=s.get('planning_rounds',result.get('planning_rounds',0)),actions=s.get('actions',result.get('actions',0)),collisions=s.get('collisions',result.get('collisions',0)),visited=s.get('visited',1),audited=bool(a and a.get('audit_passed')))
            if not r['audited']and r['status']in TERMINAL:r['status']='awaiting_audit'
            attention=read(case/'needs-attention.json')
            if attention:
                if terminal_audit_supersedes_attention(case,result,a,attention):r['historical_attention_superseded']=True
                else:r.update(status='needs_attention',error=attention['error'])
            deferred=read(case/'retry-after.json',{})
            if deferred.get('not_before',0)>time.time():r.update(status='waiting_for_quota',error=deferred['message'],retry_not_before=deferred['not_before'])
            elif result.get('error')and r['status']=='infrastructure_error':r['error']=result['error']
            skipped=read(case/'skipped.json');skip_audit=read(case/'skip-audit.json',{})
            if skipped:
                assert skip_audit.get('audit_passed')and skip_audit.get('skip_sha256')==sha(case/'skipped.json')
                r.update(status=SKIPPED,audited=False,skip_audited=True,skip_reason=skipped['reason'],failed_level=skipped['failed_level'])
            withdrawn=read(case/'withdrawn-by-user.json')
            if withdrawn:
                assert withdrawn.get('user_authorized')and withdrawn.get('counts_as_failure')is False
                r.update(status='withdrawn_by_user',audited=False,withdrawal_recorded=True,withdrawal_reason=withdrawn['reason'])
        r['clean_protocol_comparison_eligible']=clean_eligible(Path(row['case_dir']),row,read(Path(row['case_dir'])/'result.json',{}))
        rows.append(r)
    done=[r for r in rows if r['audited']and r['status']in TERMINAL]
    fresh=[r for r in done if r['kind']=='fresh'];counts=collections.Counter(r['status']for r in rows)
    live=active or{};per_model=collections.Counter(next(r['model']for r in rows if r['id']==ident)for ident in live)
    limits=m.get('per_model_concurrency_by_model',{})
    assert all(n<=limits.get(model,m.get('per_model_concurrency',4))for model,n in per_model.items())
    target=len(rows);reused_done=sum(r['kind']=='reused'for r in done);fresh_target=sum(r['kind']=='fresh'for r in rows);skips=[r for r in rows if r['status']==SKIPPED and r.get('skip_audited')];withdrawn=[r for r in rows if r['status']=='withdrawn_by_user'and r.get('withdrawal_recorded')];resolved={r['id']for r in done+skips+withdrawn}
    value={'schema':m['schema'],'updated_at':time.time(),'stage':stage,'stage_per_model':pending_levels(rows,resolved),'targets':target,'complete_targets':len(done),'skipped_targets':len(skips),'withdrawn_targets':len(withdrawn),'resolved_targets':len(resolved),'reused_complete':reused_done,'fresh_complete':len(fresh),'fresh_targets':fresh_target,'all_complete':len(resolved)==target,'status_counts':dict(counts),'active_per_model':dict(per_model),'active_cases':list(live),'rows':rows,'credentials_saved':False}
    value['model_display_names']=m.get('display_names',{});value.update(clean_statistics(rows));dump(out/'progress.json',value);return value


def launch_case(out,row,key):
    case=Path(row['case_dir']);case.mkdir(parents=True,exist_ok=True);rd,wr=os.pipe();os.write(wr,key.encode());os.close(wr)
    env=os.environ.copy();env['MAZE_CAMPAIGN_KEY_FD']=str(rd);env.pop('MIRROR_MAZE_API_KEY',None);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
    log=(case/'worker.log').open('ab')
    p=subprocess.Popen([sys.executable,'-m','maze_bench.campaign','worker','--out',str(out),'--case',row['id']],cwd=Path(__file__).resolve().parents[2],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,pass_fds=(rd,),start_new_session=True)
    log.close();os.close(rd);return p


def supervise(out,key):
    lock=(out/'.campaign.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    m=read(out/'manifest.json');old_runtime=read(out/'runtime.json')
    versions={}
    for name in('codex','claude','cursor','agy'):
        if name not in m.get('harness_executables',{}):continue
        executable=m['harness_executables'][name]
        if executable.get('version'):versions[name]=executable['version'];continue
        if name=='cursor':
            from .cursor_auth_safety import apply_cursor_memory_auth,keychain_safe_cursor_command
            versions[name]=subprocess.check_output(keychain_safe_cursor_command(executable['path'],['--version']),env=apply_cursor_memory_auth(os.environ.copy()),text=True).strip()
        else:versions[name]=subprocess.check_output([executable['path'],'--version'],text=True).strip()
    if old_runtime:dump(out/'runtime-history'/(str(time.time_ns())+'.json'),old_runtime)
    dump(out/'runtime.json',{'pid':os.getpid(),'started_at':old_runtime['started_at']if old_runtime else time.time(),'supervisor_generation_started_at':time.time(),'per_model_concurrency':m['per_model_concurrency'],'stage_policy':m['stage_policy'],'python':sys.version,'harness_versions':versions,'harness_executables':m.get('harness_executables',{}),'source_hashes':{str(p.relative_to(Path(__file__).resolve().parents[1])):sha(p)for p in Path(__file__).resolve().parents[1].rglob('*')if p.is_file()and p.suffix in('.py','.js','.mjs')},'credentials_saved':False})
    active={};finished=set();blocked=set();candidates=[r for r in m['rows']if r['kind']=='fresh']
    while len(finished)<len(candidates):
        gates=apply_difficulty_policy(out,m);finished.update(gates['skipped'])
        finished.update(r['id']for r in candidates if read(Path(r['case_dir'])/'withdrawn-by-user.json',{}).get('user_authorized'))
        # Adopt every live worker before choosing a new stage or filling slots.
        for row in candidates:
            ident=row['id'];case=Path(row['case_dir'])
            if ident in finished or ident in active or ident in blocked:continue
            if read(case/'audited.json',{}).get('audit_passed'):finished.add(ident);continue
            old_worker=read(case/'worker.json')
            if old_worker:
                adopted=AdoptedProcess(old_worker['pid'],ident)
                if adopted.poll()is None:
                    active[ident]={'process':adopted,'pid':adopted.pid,'launched_at':old_worker['started_at'],'terminal_since':None};continue
            if(case/'needs-attention.json').exists():blocked.add(ident)
        for ident,entry in list(active.items()):
            process=entry['process'];row=next(r for r in candidates if r['id']==ident);case=Path(row['case_dir'])
            if process.poll()is None:continue
            if read(case/'audited.json',{}).get('audit_passed'):finished.add(ident)
            else:
                blocked.add(ident)
                if not(case/'needs-attention.json').exists():dump(case/'needs-attention.json',{'error':'Case worker exited without an audited terminal result; inspect native/public records before another request.','returncode':process.returncode,'at':time.time()})
            del active[ident]
        stages,ready=launchable_rows(candidates,finished,active,blocked|gates['waiting'],m['per_model_concurrency'],rolling=m.get('stage_policy')=='rolling_easy_medium_hard')
        for row in ready:
            process=launch_case(out,row,key);active[row['id']]={'process':process,'pid':process.pid,'launched_at':time.time(),'terminal_since':None}
        level=next((level for level in LEVELS if level in stages.values()),'complete')
        snapshot(out,active,level)
        if(out/'replacement-policy.json').exists():
            from .replacement_control import resolve_replacement
            resolve_replacement(out)
        if blocked and not active:raise RuntimeError('Campaign blocked by unreconciled infrastructure histories: '+','.join(sorted(blocked)))
        if len(finished)<len(candidates):time.sleep(5)
    gates=apply_difficulty_policy(out,m)
    final=snapshot(out,{},'complete');assert final['all_complete']and final['fresh_complete']+final['skipped_targets']+final['withdrawn_targets']==sum(r['kind']=='fresh'for r in m['rows'])
    dump(out/'complete.json',{'complete':True,'audited':True,'skip_policy_audited':True,'targets':final['targets'],'reused':final['reused_complete'],'fresh':final['fresh_complete'],'skipped':final['skipped_targets'],'withdrawn':final['withdrawn_targets'],'resolved':final['resolved_targets'],'ended_at':time.time(),'manifest_sha256':sha(out/'manifest.json'),'progress_sha256':sha(out/'progress.json'),'material_regeneration_required':True,'publication_performed':False})
    if(out/'replacement-policy.json').exists():
        from .replacement_control import resolve_replacement
        resolve_replacement(out)
    print('BALANCED_CAMPAIGN_COMPLETE_AND_AUDITED',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['prepare','launch','supervise','worker','status']);p.add_argument('--evidence-root',type=Path);p.add_argument('--out',type=Path,required=True);p.add_argument('--base-url',default='http://vlmeval.byted.org/model_proxy_hub/gateway');p.add_argument('--case');a=p.parse_args();out=a.out.resolve()
    if a.command=='prepare':
        m=prepare(a.evidence_root,out,a.base_url);pin_harnesses(out,a.evidence_root);print(json.dumps({'targets':m['targets'],'reuse':m['reuse'],'fresh':m['fresh'],'out':str(out),'audited_reused_sources':len(m['reuse_audits'])}));return
    if a.command=='status':
        x=snapshot(out);print(json.dumps({k:v for k,v in x.items()if k!='rows'}));return
    if a.command=='launch':
        assert(out/'manifest.json').exists();manifest=read(out/'manifest.json')
        if(out/'PAUSED_BY_USER.json').exists():raise RuntimeError('All evaluations are paused by user; explicit resume is required')
        if(out/'PAUSED_BY_REPLACEMENT.json').exists():raise RuntimeError('Native cohort replaced by the audited candidate; no restart allowed')
        if(out/'PAUSED_FOR_KEYCHAIN.json').exists():raise RuntimeError('Native Cursor is paused until the no-keychain fix is verified; automatic restart is blocked')
        fresh_rows=[r for r in manifest['rows']if r['kind']=='fresh']
        direct_native_only=all(r['config'].get('harness')=='cursor'or(r['config'].get('harness')=='agy'and r['config'].get('native_provider'))for r in fresh_rows)
        if direct_native_only:
            if any(r['config'].get('harness')=='cursor'for r in fresh_rows):
                from .credentials import cursor_api_key
                credential=cursor_api_key();assert credential;credential=None
            key=''
        else:key=getpass.getpass('Gateway credential (hidden; memory only): ');assert key
        rd,wr=os.pipe();os.write(wr,key.encode());os.close(wr);env=os.environ.copy();env['MAZE_CAMPAIGN_KEY_FD']=str(rd);env.pop('MIRROR_MAZE_API_KEY',None);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
        log=(out/'supervisor.log').open('ab');child=subprocess.Popen([sys.executable,'-m','maze_bench.campaign','supervise','--out',str(out)],cwd=Path(__file__).resolve().parents[2],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,pass_fds=(rd,),start_new_session=True);log.close();os.close(rd)
        if sys.platform=='darwin':subprocess.Popen(['caffeinate','-i','-w',str(child.pid)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        print(json.dumps({'pid':child.pid,'out':str(out),'credentials_saved':False}));return
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    with os.fdopen(int(os.environ.pop('MAZE_CAMPAIGN_KEY_FD')),'r')as f:key=f.read().strip()
    if a.command=='worker':
        try:worker(out,a.case,key)
        except Exception as exc:
            row=next(r for r in read(out/'manifest.json')['rows']if r['id']==a.case);message=str(exc).replace(key,'[redacted]')if key else str(exc);dump(Path(row['case_dir'])/'needs-attention.json',{'error':message,'at':time.time()});raise SystemExit(1)
    else:supervise(out,key)


if __name__=='__main__':main()
