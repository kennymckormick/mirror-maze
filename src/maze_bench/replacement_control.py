"""Deadline-gated native-model replacement with scoped, auditable cancellation.

No inference is performed here. Only the previous native Cursor cohort can be
stopped; API controls and other model processes are never cancellation targets.
"""
import fcntl,hashlib,json,math,os,shlex,shutil,signal,subprocess,time
from pathlib import Path
from .common import dump,sha
from .difficulty_gate import apply_difficulty_policy

SCORED={'success','planning_cap'}
FILES=('config.json','progress.json','episode.json','result.json','status.json','wire.json','native.jsonl.gz','native-session.json','native-invocation.json','native-model-selection.json','feed.json','source-provenance.json','host-interventions.json','profile-completions.json','statistics-policy.json')


def require(condition,message):
    if not condition:raise ValueError(message)


def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return default


def finite_epoch(value):
    require(isinstance(value,(int,float))and not isinstance(value,bool)and math.isfinite(value)and value>0,'Invalid replacement timestamp')
    return float(value)


def candidate_validation(out,policy,complete):
    manifest=read(out/'manifest.json');progress=read(out/'progress.json')
    require(complete.get('complete')is True and complete.get('audited')is True,'Candidate is not complete and audited')
    require(complete.get('manifest_sha256')==sha(out/'manifest.json'),'Candidate manifest hash differs from completion receipt')
    require(complete.get('progress_sha256')==sha(out/'progress.json'),'Candidate progress hash differs from completion receipt')
    ended=finite_epoch(complete.get('ended_at'));rows=manifest['rows']
    require(len(rows)==12 and len({r['id']for r in rows})==12,'Replacement requires exactly twelve distinct planned trials')
    expected={(level,shape,seed)for level in('easy','medium','hard')for shape in('square','hex')for seed in('MIRROR-042','MIRROR-043')}
    require({(r.get('difficulty'),r.get('topology'),r.get('seed'))for r in rows}==expected,'Candidate map/difficulty/seed matrix differs')
    require(progress.get('all_complete')is True and progress.get('resolved_targets')==12 and not progress.get('withdrawn_targets',0),'Candidate has unresolved or withdrawn trials')
    executed=[];skips=[]
    for row in rows:
        cfg=row['config'];case=Path(row['case_dir'])
        side={'square':{'easy':7,'medium':11,'hard':15},'hex':{'easy':5,'medium':7,'hard':9}}[row['topology']][row['difficulty']]
        require(row.get('side_length')==side and row['target_cap']=={'easy':200,'medium':400,'hard':800}[row['difficulty']],'Candidate geometry/budget schedule differs')
        require(all(cfg.get(key)==row[key]for key in('topology','difficulty','seed','side_length')),'Candidate executed map configuration differs')
        require(row['kind']=='fresh'and row['model']==policy['candidate_model']and row['mode']=='harness','Candidate contains a reused, API, or different-model trial')
        require(cfg.get('harness')=='agy'and cfg.get('native_provider')is True,'Candidate is not direct native AGY')
        require(cfg.get('native_model')==policy['native_model']and cfg.get('effort')=='medium','Candidate model or thought level differs')
        require(not(case/'needs-attention.json').exists()and not(case/'withdrawn-by-user.json').exists(),'Candidate has an unresolved infrastructure/withdrawal marker')
        skipped=read(case/'skipped.json')
        if skipped:
            proof=read(case/'skip-audit.json',{})
            require(proof.get('audit_passed')is True and proof.get('skip_sha256')==sha(case/'skipped.json'),'Candidate skip hash/audit is invalid')
            require(proof.get('trial_never_started')is True and proof.get('counts_as_scored_outcome')is False,'Candidate skip is not explicitly unscored')
            require(finite_epoch(proof.get('at'))<=ended and finite_epoch(skipped.get('at'))<=ended,'Candidate skip was not audited before completion')
            skips.append({'id':row['id'],'skip_sha256':sha(case/'skipped.json'),'skip_audit_sha256':sha(case/'skip-audit.json')});continue
        result=read(case/'result.json',{});audit=read(case/'audited.json',{});validation=read(case/'validation.json',{})
        require(result.get('status')in SCORED and not result.get('error'),'Candidate contains an unscored infrastructure outcome')
        require(result.get('clean_protocol_comparison_eligible')is not False and not result.get('protocol_deviations'),'Candidate has a protocol deviation')
        interventions=read(case/'host-interventions.json',{})
        require(not interventions.get('interventions'),'Candidate has host interventions')
        require(audit.get('audit_passed')is True and audit.get('result_sha256')==sha(case/'result.json'),'Candidate result hash/audit is invalid')
        require(audit.get('validation_sha256')==sha(case/'validation.json')and validation.get('all_checked_invariants_passed')is True,'Candidate validation hash/proof is invalid')
        require(finite_epoch(audit.get('ended_at'))<=ended,'Candidate result was not audited before completion')
        number=result.get('planning_rounds');cap=row['target_cap']
        require(isinstance(number,int)and not isinstance(number,bool)and 0<number<=cap and result.get('planning_cap')==cap,'Candidate planning budget differs')
        require(result.get('protocol')=='agy-acp-images-v1'and result.get('api_calls')==0 and result.get('vision_api_calls')==0,'Candidate is not a direct native image run')
        require(all((case/name).is_file()for name in('config.json','progress.json','episode.json','wire.json','native.jsonl.gz')),'Candidate native checkpoint evidence is missing')
        checkpoint=read(case/'progress.json');episode=read(case/'episode.json')
        require(len(checkpoint.get('events',[]))==number and checkpoint.get('status')==result['status']and checkpoint.get('state')==episode.get('state'),'Candidate physical/planning checkpoint differs')
        configuration=read(case/'config.json')
        require(all(configuration.get(key)==cfg.get(key)for key in('harness','native_provider','native_model','effort'))and configuration.get('max_planning',cap)==cap,'Candidate executed configuration differs from manifest')
        require((result['status']=='success')==bool(checkpoint['state'].get('success'))and(result['status']!='planning_cap'or number==cap),'Candidate terminal state does not match its scored outcome')
        checks=validation.get('cases',[])
        require(len(checks)==1 and checks[0].get('status')==result['status']and checks[0].get('replayed_planning_rounds')==number and checks[0].get('native_agy_rounds_verified')==number,'Candidate native replay coverage is incomplete')
        require(checks[0].get('clean_protocol_comparison_eligible')is not False and not checks[0].get('protocol_deviations'),'Candidate validation records a dirty protocol')
        selected=read(case/'native-model-selection.json',{})
        base=policy['native_model'].removesuffix('-medium')
        require(selected.get('native_provider')is True and selected.get('gateway_used')is False and selected.get('selected',{}).get('model')==base and selected.get('selected',{}).get('thought_level')=='medium','Candidate native model-selection proof differs')
        executed.append({'id':row['id'],'status':result['status'],'planning_rounds':number,'source_sha256':{name:sha(case/name)for name in FILES if(case/name).exists()},'audited_sha256':sha(case/'audited.json'),'validation_sha256':sha(case/'validation.json'),'native_model_selection_sha256':sha(case/'native-model-selection.json')})
    # All skips must already exist. This revalidates, rather than manufacturing
    # new skips after the deadline and backdating campaign completion.
    before={item['id']:(item['skip_sha256'],item['skip_audit_sha256'])for item in skips}
    gates=apply_difficulty_policy(out,manifest)
    require(gates['skipped']==set(before),'Candidate policy-skipped set differs')
    for row in rows:
        if row['id']in before:
            case=Path(row['case_dir']);require(before[row['id']]==(sha(case/'skipped.json'),sha(case/'skip-audit.json')),'Candidate skip proof changed during validation')
    require(len(executed)+len(skips)==12 and complete.get('targets')==12 and complete.get('fresh')==len(executed)and complete.get('skipped',0)==len(skips),'Candidate completion counts differ')
    require(complete.get('skip_policy_audited')is True,'Candidate skip-policy completion gate is missing')
    return {'candidate_completed_at':ended,'planned':12,'scored':len(executed),'skipped':len(skips),'manifest_sha256':sha(out/'manifest.json'),'progress_sha256':sha(out/'progress.json'),'complete_sha256':sha(out/'complete.json'),'executed':executed,'policy_skips':skips}


class ProcessControl:
    """PID-specific signals only; never process groups or broad name matching."""
    def snapshot(self):
        output=subprocess.check_output(['ps','axww','-o','pid=,ppid=,stat=,command='],text=True);result={}
        for line in output.splitlines():
            parts=line.split(None,3)
            if len(parts)!=4:continue
            pid,ppid=int(parts[0]),int(parts[1]);command=parts[3]
            start=''
            try:start=Path('/proc')/str(pid)/'stat';start=start.read_text().rsplit(')',1)[1].split()[19]
            except(OSError,IndexError):start=''
            identity=str(pid)+'|'+str(start)if start else str(pid)+'|'+hashlib.sha256(command.encode()).hexdigest()
            result[pid]={'pid':pid,'ppid':ppid,'state':parts[2],'command':command,'identity':identity}
        return result
    def send(self,record,number):
        current=self.snapshot().get(record['pid'])
        if not current or current['identity']!=record['identity']:return False
        try:os.kill(record['pid'],number);return True
        except ProcessLookupError:return False
    def sleep(self,seconds):time.sleep(seconds)


def matches(record,mode,out,ident=None):
    try:tokens=shlex.split(record['command'])
    except ValueError:return False
    try:
        index=tokens.index('-m')
        if tokens[index+1:index+3]!=['maze_bench.campaign',mode]:return False
        target=tokens[tokens.index('--out')+1]
        if Path(target).resolve()!=out:return False
        return ident is None or tokens[tokens.index('--case')+1]==ident
    except(ValueError,IndexError):return False


def terminal_case(case,row):
    result=read(case/'result.json',{});progress=read(case/'progress.json',{});episode=read(case/'episode.json',{})
    return bool(read(case/'audited.json',{}).get('audit_passed')or result.get('status')in SCORED or progress.get('status')in SCORED or progress.get('state',{}).get('success')or episode.get('state',{}).get('success')or progress.get('planning_rounds',0)>=row['target_cap'])


def preserved_terminal(case,row,reason):
    progress=read(case/'progress.json',{})
    return {'id':row['id'],'reason':reason,'saved_rounds':progress.get('planning_rounds'),'physical_state':progress.get('state'),'native_session':read(case/'native-session.json',{}),'result_sha256_at_observation':sha(case/'result.json')if(case/'result.json').exists()else None,'terminal_audit_or_cleanup_may_finish':True}


def descendants(table,root):
    found=[];parents={root};seen={root}
    while parents:
        next_level={pid for pid,p in table.items()if p['ppid']in parents and pid not in seen};found.extend(table[pid]for pid in sorted(next_level));seen.update(next_level);parents=next_level
    return found


def checkpoint_snapshot(case,row,selection_id):
    archive=case/'interruptions'/('cancelled-by-replacement-'+selection_id[:16]+'-'+str(time.time_ns()));archive.mkdir(parents=True);archive.chmod(0o700)
    hashes={}
    for name in FILES:
        source=case/name
        if source.exists():hashes[name]=sha(source);shutil.copy2(source,archive/name)
    progress=read(case/'progress.json',{});episode=read(case/'episode.json',{});wire=read(case/'wire.json',[]);session=read(case/'native-session.json',{});profile=Path(row['config'].get('profile_root',case/'unused-profile'));session_files={};session_id=session.get('thread_id')
    if session_id:
        for pattern in('config/chats/*/'+session_id+'/store.db','config/chats/*/'+session_id+'/store.db-wal','config/chats/*/'+session_id+'/meta.json','data/projects/*/agent-transcripts/'+session_id+'/*.jsonl'):
            for path in profile.glob(pattern):session_files[str(path)]=sha(path)
    rounds=progress.get('planning_rounds',len(progress.get('events',[])))
    return {'saved_rounds':rounds,'planning_cap':row['target_cap'],'physical_state':episode.get('state',progress.get('state')),'planning_checkpoint_state':progress.get('state'),'checkpoint_states_match':progress.get('state')==episode.get('state'),'pending_wire_indices':[w['index']for w in wire if w.get('observation_round',0)>rounds],'native_session':session,'source_sha256':hashes,'session_file_sha256':session_files,'snapshot':str(archive)}


def stop_old_native(old_out,candidate_out,selection_id,control,now):
    manifest=read(old_out/'manifest.json');rows=manifest['rows']
    require(rows and all(r['model']=='gemini'and r['mode']=='harness'and r['config'].get('harness')=='cursor'and r['config'].get('native_model')=='gemini-4-argon-medium'for r in rows),'Old replacement target is not exclusively native Argon Cursor')
    require(all(Path(r['case_dir']).resolve().is_relative_to(old_out)for r in rows),'Old native case lies outside its cohort')
    existing=read(old_out/'replacement-stop-receipt.json')
    if existing:
        require(existing.get('selection_id')==selection_id and existing.get('all_unfinished_workers_stopped')is True,'Conflicting or incomplete old-cohort stop receipt');return existing
    marker={'status':'paused_by_replacement','selection_id':selection_id,'candidate_out':str(candidate_out),'at':now,'no_new_inference_authorized':True}
    dump(old_out/'PAUSED_BY_REPLACEMENT.json',marker)
    supervisors=[]
    for process in control.snapshot().values():
        if matches(process,'supervise',old_out):
            control.send(process,signal.SIGTERM);supervisors.append(process)
    control.sleep(.2)
    for process in supervisors:
        current=control.snapshot().get(process['pid'])
        if current and current['identity']==process['identity']and not current['state'].startswith('Z'):control.send(process,signal.SIGKILL)
    control.sleep(1)  # Give already-finishing goal/cap workers a chance to audit.
    cancelled=[];preserved=[]
    for row in rows:
        case=Path(row['case_dir'])
        if terminal_case(case,row):preserved.append(preserved_terminal(case,row,'completed or terminal audit/cleanup preserved'));continue
        previous=read(case/'cancelled-by-replacement.json')
        if previous:
            require(previous.get('selection_id')==selection_id,'Conflicting per-case replacement receipt');cancelled.append(previous);continue
        worker=read(case/'worker.json',{});table=control.snapshot();process=table.get(worker.get('pid'));frozen=[]
        if process and matches(process,'worker',old_out,row['id']):
            control.send(process,signal.SIGSTOP);control.sleep(.02)
            if terminal_case(case,row):
                control.send(process,signal.SIGCONT);preserved.append(preserved_terminal(case,row,'terminal boundary reached before freeze; audit left running'));continue
            frozen=[process]
            for _ in range(3):
                table=control.snapshot();known={p['pid']for p in frozen}
                added=[p for p in descendants(table,process['pid'])if p['pid']not in known]
                for child in added:control.send(child,signal.SIGSTOP)
                frozen.extend(added)
                if not added:break
            for target in frozen:
                current=control.snapshot().get(target['pid'])
                require(not current or current['identity']!=target['identity']or current['state'].startswith(('T','t','Z')),'An owned worker/child could not be frozen safely')
        snapshot=checkpoint_snapshot(case,row,selection_id)
        for target in reversed(frozen):control.send(target,signal.SIGKILL)
        control.sleep(.02)
        for name,digest in snapshot['source_sha256'].items():require(sha(case/name)==digest,'A frozen canonical checkpoint changed while stopping')
        for name,digest in snapshot['session_file_sha256'].items():require(sha(name)==digest,'A frozen native session file changed while stopping')
        record={'status':'cancelled_by_replacement','id':row['id'],'selection_id':selection_id,'candidate_out':str(candidate_out),'at':now,'counts_as_failure':False,'budget_reset':False,'canonical_files_unchanged':True,'processes':[{'pid':p['pid'],'identity':p['identity']}for p in frozen],**snapshot}
        dump(case/'cancelled-by-replacement.json',record);cancelled.append(record)
    allowed={r['id']for r in preserved}
    for process in control.snapshot().values():
        if matches(process,'worker',old_out)and not process['state'].startswith('Z'):
            require(any(matches(process,'worker',old_out,ident)for ident in allowed),'An unfinished old-cohort worker is still running')
    receipt={'status':'stopped','selection_id':selection_id,'candidate_out':str(candidate_out),'old_native_out':str(old_out),'at':now,'all_unfinished_workers_stopped':True,'supervisor_pids':[p['pid']for p in supervisors],'cancelled':cancelled,'terminal_cases_preserved':preserved,'other_models_and_api_processes_touched':False}
    dump(old_out/'replacement-stop-receipt.json',receipt);return receipt


def resolve_replacement(out,now=None,process_control=None):
    out=Path(out).resolve();now=time.time()if now is None else now;policy=read(out/'replacement-policy.json')
    if not policy:return {'status':'not_configured'}
    lock=(out/'.replacement.lock').open('w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:lock.close();return {'status':'selection_in_progress'}
    try:
        require(policy.get('user_authorized')is True,'Replacement is not user authorized')
        require(policy.get('candidate_model')=='gemini38'and policy.get('native_model')=='gemini-3.8-flash-medium','Unexpected replacement model')
        deadline=finite_epoch(policy.get('deadline_epoch'));old_out=Path(policy['old_native_out']).resolve();selection_file=Path(policy['selection_file']).resolve()
        require(old_out!=out and old_out.is_dir(),'Invalid old native cohort path')
        selected=read(selection_file)
        if selected:
            require(selected.get('status')=='selected'and selected.get('candidate_out')==str(out)and selected.get('candidate_model')==policy['candidate_model']and selected.get('native_model')==policy['native_model'],'Conflicting replacement selection')
            require(selected.get('stop_receipt_sha256')==sha(old_out/'replacement-stop-receipt.json'),'Recorded replacement stop receipt changed')
            return selected
        complete=read(out/'complete.json')
        if not complete or not complete.get('complete')or not complete.get('audited'):
            result={'status':'deadline_missed'if now>=deadline else'waiting','at':now,'deadline_epoch':deadline,'old_native_cohort_unchanged':True};dump(out/'replacement-resolution.json',result);return result
        if finite_epoch(complete.get('ended_at'))>deadline:
            result={'status':'deadline_missed','at':now,'deadline_epoch':deadline,'candidate_completed_at':complete['ended_at'],'old_native_cohort_unchanged':True};dump(out/'replacement-resolution.json',result);return result
        verified=candidate_validation(out,policy,complete);identity=verified['complete_sha256']
        dump(out/'replacement-in-progress.json',{'selection_id':identity,'old_native_out':str(old_out),'at':now,'candidate_validation':verified})
        stopped=stop_old_native(old_out,out,identity,process_control or ProcessControl(),now)
        result={'schema':'mirror-maze-native-replacement-selection/v1','status':'selected','selected_model':policy['candidate_model'],'candidate_model':policy['candidate_model'],'native_model':policy['native_model'],'candidate_out':str(out),'old_native_out':str(old_out),'deadline_epoch':deadline,'candidate_completed_at':verified['candidate_completed_at'],'selected_at':now,'user_authorized':True,'selection_id':identity,'candidate_validation':verified,'stop_receipt':str(old_out/'replacement-stop-receipt.json'),'stop_receipt_sha256':sha(old_out/'replacement-stop-receipt.json'),'selection_file':str(selection_file)}
        require(stopped['all_unfinished_workers_stopped'],'Old cohort stop was not verified')
        dump(old_out/'REPLACED_BY_CANDIDATE.json',result);dump(selection_file,result);dump(out/'replacement-resolution.json',result);return result
    except(Exception)as exc:
        result={'status':'validation_failed','at':now,'reason':str(exc),'automatic_selection_performed':False};dump(out/'replacement-resolution.json',result);return result
    finally:lock.close()
