import collections,datetime,getpass,gzip,hashlib,json,os,re,shutil,sys,time,traceback,uuid,threading
from pathlib import Path
from mirror_maze import MirrorMaze
from .common import dump,parse_plan as parse
from .native import Native
from .clients import API
from .bridge import Broker
from .rules import RULES
from .settings import environment_options, frame_name, planning_request
from .cursor_native import CursorObservation,CursorNative
from .cursor_profile_evidence import profile_completions
from .host_interventions import check_resume_history,intervention_metadata
CACHE_LOCK=threading.RLock()
def run(cfg,key,case,cap=200,vision_probe=False,resume=False,recovered_reply=None):
 case=case.resolve();case.mkdir(parents=True,exist_ok=True);cfg=dict(cfg,direct_observations=True,observation_only=False,max_planning=cap)
 cursor=cfg.get('harness')=='cursor';agy_native=cfg.get('harness')=='agy'and cfg.get('native_provider')is True;direct_native=cursor or agy_native
 if agy_native:
  from .agy_native import AgyObservation,AgyNative
 protocol='cursor-native-images-v1'if cursor else'agy-acp-images-v1'if agy_native else'round-prune-v3'
 native_evidence='native_cursor_cli_image_attachments'if cursor else'native_agy_acp_image_attachments'
 client_class=CursorNative if cursor else AgyNative if agy_native else Native if cfg.get('harness')else API
 native_wire_replay=recovered_reply if recovered_reply and recovered_reply.get('kind')=='native_upstream_replay'else None
 if native_wire_replay:cfg['_native_wire_replay']=native_wire_replay;recovered_reply=None
 old=None;old_episode=None;old_result=None;native_id=None;interventions=[]
 if resume:
  old=json.loads((case/'progress.json').read_text());old_episode=json.loads((case/'episode.json').read_text());old_result=json.loads((case/'result.json').read_text())
  if old['status']!='infrastructure_error' or old.get('protocol')not in('host-automatic-images-v2','round-prune-v3','cursor-native-images-v1','agy-acp-images-v1'):raise ValueError('Only interrupted formal episodes may resume')
  interventions=check_resume_history(case,old,old_episode)
  backup=case/'interruptions'/str(time.time_ns());backup.mkdir(parents=True)
  for name in ['config.json','progress.json','episode.json','result.json','wire.json','native-invocation.json']:
   if(case/name).exists():(backup/name).write_bytes((case/name).read_bytes())
  if cfg.get('harness'):
   with gzip.open(case/'native.jsonl.gz','rt')as f:
    for line in f:
     try:e=json.loads(line)
     except Exception:continue
     if cfg.get('harness')=='codex'and e.get('type')=='thread.started':native_id=e['thread_id']
     if cfg.get('harness')=='agy'and not agy_native and e.get('event')=='init':native_id=e['conversation_id']
     if agy_native and e.get('type')=='native_agy_rpc_request'and e.get('method')=='session/prompt':native_id=e.get('params',{}).get('sessionId',native_id)
     if cfg.get('harness')=='claude'and e.get('type')=='system'and e.get('subtype')=='init':native_id=e['session_id']
     if cursor and e.get('type')=='system'and e.get('subtype')=='init':native_id=e['session_id']
  if agy_native and(case/'native-session.json').exists():
   recorded=json.loads((case/'native-session.json').read_text()).get('thread_id')
   if native_id and recorded and native_id!=recorded:raise RuntimeError('Saved AGY session differs from committed native request history')
   native_id=recorded or native_id
  if not native_id and cfg.get('harness')and not(direct_native and not old['events']):raise RuntimeError('Native conversation ID missing; do not start a fresh episode')
  if cfg.get('harness')=='agy'and not agy_native:cfg['_resume_conversation']=native_id
  if agy_native:cfg['_resume_agy_native']=native_id
  if cursor:cfg['_resume_cursor']=native_id
  if cfg.get('harness')=='claude':
   profile=Path(cfg['profile_root'])/'claude/projects'
   matches=list(profile.glob('*/'+native_id+'.jsonl'))
   if len(matches)!=1:raise RuntimeError('Saved Claude conversation is missing or ambiguous')
   cfg['_resume_transcript']=str(matches[0])
  cfg['_resume_from']=str(backup.relative_to(case))
  dump(case/'result.json',{'case_id':case.name,'status':'running','success':None,'resume_from':cfg['_resume_from']})
 dump(case/'config.json',cfg)
 broker=CursorObservation(cfg,case)if cursor else AgyObservation(cfg,case)if agy_native else Broker(key,cfg,case);native=None;events=old['events']if resume else[];invalid=old['invalid_sequences']if resume else 0;status='running';error=None;started=time.time()-(old.get('elapsed_seconds',0)if resume else 0);env=None
 cache=Path(cfg['cache_dir']);cache.mkdir(exist_ok=True)
 if not vision_probe:env=MirrorMaze(**environment_options(cfg))
 def frame(label):
  st=env.review_state();name=frame_name(cfg,st);path=cache/name
  with CACHE_LOCK:
   if not path.exists():
    png=env.render(width=1280,height=720);temp=path.with_name(name+'.'+uuid.uuid4().hex+'.tmp');temp.write_bytes(png)
    try:os.link(temp,path)
    except FileExistsError:pass
    finally:temp.unlink(missing_ok=True)
  return{'file':name,'path':str(path),'label':label,'state':st}
 def save():
  if env:dump(case/'episode.json',env.export_episode())
  dump(case/'progress.json',{'protocol':protocol,'status':status,'planning_rounds':len(events),'invalid_sequences':invalid,'total_collisions':(env.state()['collisions']if env else 0)+invalid,'state':env.export_episode()['state']if env else{},'events':events,'elapsed_seconds':time.time()-started,'error':error})
  if env:
   st=env.review_state();dump(case/'status.json',{'status':status,'planning_rounds':len(events),'actions':st['actionCount'],'collisions':st['collisions']+invalid,'visited':st['visited'],'success':st['success'],'updated_at':time.time(),'error':error})
 initial=None;pending=[];execution_checkpoint=None
 try:
  if not vision_probe:
   if resume:
    env.perform([a['action']for a in old_episode['actions']],stop_on_collision=False)
    if env.export_episode()['state']!=old_episode['state']:raise RuntimeError('Restored full state differs from interrupted episode')
    pending=[{'file':s['frame'],'path':str(cache/s['frame']),'label':f'After action {i+1}/{len(events[-1]["steps"])}: '+str(s['action'])+'. Outcome: '+json.dumps(s['result']),'state':s['state']}for i,s in enumerate(events[-1]['steps'])]if events else[frame('Initial first-person observation. 1280x720 PNG; no map or HUD.')]
    initial=old_result.get('initial_frame')or(events[0]['input_frames'][0]if events else pending[0]['file'])
   else:pending=[frame('Initial first-person observation. 1280x720 PNG; no map or HUD.')];initial=pending[0]['file']
  save();native=None if recovered_reply else client_class(cfg,case,broker)
  if resume and cfg.get('harness')=='codex'and native is not None:native.thread_id=native_id
  if vision_probe:
   from PIL import Image,ImageDraw,ImageFont
   rng=__import__('random').SystemRandom();cards=[]
   for i in range(3):
    code=str(rng.randrange(1000,10000));im=Image.new('RGB',(1280,720),'#f5f4ee');draw=ImageDraw.Draw(im);font=ImageFont.truetype(cfg['probe_font'],155)if cfg.get('probe_font')else ImageFont.load_default(size=155);draw.text((250,220),code,fill='#142838',font=font);path=case/(uuid.uuid4().hex+'.png');im.save(path);cards.append({'code':code,'path':str(path),'label':'Vision transport verification image.'})
   answers=[]
   for n,selected in enumerate([[cards[0]],[cards[1],cards[2]]],1):
    feed={'round':n,'feedback':'Vision transport preflight.','frames':[{'path':c['path'],'label':c['label']}for c in selected]};dump(case/'feed.json',feed);broker.set_feed(feed);begin=len(broker.calls);raw=native.ask('Transcribe the four large digits in EACH attached image, in image order. Reply only JSON {"codes":["1234"]} with the actual digits, not the example. Do not use tools to read images; a native finish wrapper for returning the JSON is allowed. The images are attached automatically by the host.',timeout=240)
    p=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip()));correct=p.get('codes')==[c['code']for c in selected];delivered=verify_images(broker.calls[begin:],selected,n)
    answers.append({'round':n,'correct':correct,'delivered':delivered,'response':raw});print('VISION_CHECK '+json.dumps({'model':cfg['model_key'],'round':n,'correct':correct,'delivered':delivered},ensure_ascii=False),flush=True)
   status='vision_passed'if all(a['correct']and a['delivered']for a in answers)else'vision_failed';dump(case/'vision-result.json',{'status':status,'answers':answers,'source_alias':cfg['source_alias'],'harness':cfg.get('harness')});return{'status':status,'model':cfg['model_key']}
  for n in range(len(events)+1,cap+1):
   public=env.state();feedback={'planning_round':n,'remaining':cap-n+1,'success':public['success'],'total_actions':public['actionCount'],'collisions':public['collisions']+invalid,'previous_sequence':[]if not events else[{'action':s['action'],'result':s['result']}for s in events[-1]['steps']],'previous_invalid':False if not events else events[-1]['invalid']}
   feed={'round':n,'feedback':json.dumps(feedback),'frames':[{'path':f['path'],'file':f['file'],'label':f['label']}for f in pending],'prune_previous_timeout':bool(resume and n==len(old['events'])+1 and cfg.get('prune_previous_timeout'))};dump(case/'feed.json',feed);broker.set_feed(feed)
   text=planning_request(cfg,n,cap,len(pending),feedback)
   if recovered_reply:
    assert recovered_reply['round']==n
    wire=recovered_reply['wire'];selection=broker.transport.selection
    selection.kept=[selection.original[i['index']]for i in wire['images']]
    assert [f['file']for f in selection.kept]==[i['file']for i in wire['images']]
    selection.drops=wire.get('trim_events',[])
    broker.transport.last_delivery={'round':n,'request_index':wire['index'],'images':wire['images'],'trim_events':selection.drops,'original_frames':len(pending)}
    if direct_native:broker.transport.last_delivery.update(evidence_type=native_evidence,native_output_proof=wire.get('native_output_proof'))
    evidence=recovered_reply['evidence'].get('closed_json_stream_recovery')or wire.get('closed_json_stream_recovery')
    if evidence:broker.transport.last_delivery['closed_json_stream_recovery']=evidence
    raw=recovered_reply['response'];seconds=recovered_reply['seconds']
   else:
    if native is None:
     native=client_class(cfg,case,broker)
     if resume and cfg.get('harness')=='codex':native.thread_id=native_id
    start_call=len(broker.calls);raw=native.ask(text,timeout=cfg.get('harness_timeout',3600));seconds=native.turns[-1]['seconds']
   delivered=verify_delivery(broker,pending,n)
   delivery=broker.transport.last_delivery
   if not delivered:raise RuntimeError('required_current_images_not_delivered; no action executed or collision charged')
   if agy_native:
    from .agy_protocol import committed_denied_tool_ids,ensure_fresh_denied_tools
    charged=committed_denied_tool_ids({'events':events},broker.calls)
    ensure_fresh_denied_tools(delivery.get('native_output_proof',{}),charged)
   p,reason=parse(raw);before=env.export_episode()['state'];e={'round':n,'response':raw,'plan':str(p.get('plan',''))if p else'','actions':p.get('actions')if p else None,'invalid':p is None,'invalid_reason':reason,'before':before,'input_frames':[f['file']for f in pending],'steps':[],'seconds':seconds,'observation_delivery_verified':True,'input_frames_sent':[i['file']for i in delivery['images']],'image_trim_events':delivery['trim_events'],'input_request_index':delivery['request_index']}
   if recovered_reply:e['recovered_committed_public_reply']=recovered_reply['evidence'];recovered_reply=None
   if native_wire_replay and native_wire_replay['round']==n:e['replayed_completed_native_response']=native_wire_replay
   if delivery.get('closed_json_stream_recovery'):e['closed_json_stream_recovery']=delivery['closed_json_stream_recovery']
   if direct_native:e.update(observation_evidence_type=native_evidence,native_output_proof=delivery.get('native_output_proof'))
   execution_checkpoint={'episode':env.export_episode(),'events':len(events),'invalid':invalid,'response_sha256':hashlib.sha256(raw.encode()).hexdigest(),'round':n}
   pending=[]
   if p is None:
    invalid+=1;f=frame('Invalid action sequence: one collision penalty, no movement. Current first-person image.');pending=[f];e['steps'].append({'action':None,'result':{'collision':True,'reason':reason},'frame':f['file'],'state':f['state']})
   else:
    for i,a in enumerate(p['actions']):
     result=env.perform(a,stop_on_collision=False)['results'][0];f=frame(f'After action {i+1}/{len(p["actions"])}: {a}. Outcome: '+json.dumps(result));pending.append(f);e['steps'].append({'action':a,'result':result,'frame':f['file'],'state':f['state']})
     if env.state()['success']:break
   e['after']=env.export_episode()['state'];events.append(e);save();execution_checkpoint=None;print(json.dumps({'case':case.name,'round':n,'actions':len(e['steps']),'collisions':env.state()['collisions']+invalid,'success':env.state()['success']},ensure_ascii=False),flush=True)
   if env.state()['success']:status='success';break
  else:status='planning_cap'
 except Exception as exc:
  status='infrastructure_error';error=type(exc).__name__+': '+str(exc);traceback.print_exc()
  if execution_checkpoint and len(events)==execution_checkpoint['events']:
   # No subsequent model observation was delivered: abort this uncommitted
   # virtual execution transaction, retaining the exact native answer to replay.
   discarded=env.export_episode();archive=case/'interruptions'/('uncommitted-execution-'+str(time.time_ns()))
   dump(archive/'discarded-episode.json',discarded);dump(archive/'committed-before.json',execution_checkpoint['episode'])
   dump(archive/'transaction.json',{'round':execution_checkpoint['round'],'response_sha256':execution_checkpoint['response_sha256'],'reason':error,'next_model_observation_delivered':False,'no_resampling':True,'rollback_scope':'uncommitted virtual actions only','discarded_actions':len(discarded['actions'])-len(execution_checkpoint['episode']['actions'])})
   env.close();env=MirrorMaze(**environment_options(cfg));env.perform([a['action']for a in execution_checkpoint['episode']['actions']],stop_on_collision=False)
   assert env.export_episode()['state']==execution_checkpoint['episode']['state'],'Virtual transaction rollback failed; manual reconciliation required'
   invalid=execution_checkpoint['invalid']
 finally:
  save()
  if native:
   if native.thread_id:dump(case/'native-session.json',{'thread_id':native.thread_id,**({'harness':'agy','native_provider':True}if agy_native else{})})
   native.close()
  broker.close()
  if env:env.close()
 result={'case_id':case.name,'protocol':protocol,'model':cfg['model_label'],'source_alias':cfg['source_alias'],'harness':cfg.get('harness'),'topology':cfg['topology'],'seed':cfg['seed'],'planning_cap':cap,'status':status,'success':status=='success','planning_rounds':len(events),'collisions':invalid+sum(s['result'].get('collision',False)for e in events if not e['invalid']for s in e['steps']),'invalid_sequences':invalid,'actions':sum(len(e['steps'])for e in events if not e['invalid']),'seconds':time.time()-started,'api_calls':0 if direct_native else len(broker.calls),'vision_api_calls':0 if direct_native else sum(bool(c.get('images'))for c in broker.calls),'mode':'harness'if cfg.get('harness')else'api','initial_frame':initial,'error':error}
 if direct_native:result.update(native_client_attempts=len(broker.calls),native_vision_turns=sum(bool(c.get('native_client_finished'))for c in broker.calls),observation_evidence_type=native_evidence)
 if agy_native:result['native_provider']=True
 metadata=intervention_metadata(interventions)
 if metadata:
  result['model_sequence_actions']=result['actions'];result['model_sequence_collisions']=result['collisions']
  result['actions']+=metadata['host_intervention_actions'];result['collisions']+=metadata['host_intervention_collisions'];result.update(metadata)
 if cursor:result['profile_backed_completion_rounds']=sorted(profile_completions(case,cfg))
 if(case/'source-provenance.json').exists():result['source_provenance']=json.loads((case/'source-provenance.json').read_text())
 dump(case/'result.json',result);return result

def verify_images(calls,frames,n):
 expected=[hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()for f in frames]
 return any((c.get('http_status')==200 or(c.get('kind')in('native_cursor_image_attachment','native_agy_acp_image_attachment')and c.get('native_client_finished')))and c.get('observation_round')==n and[i['sha256']for i in c.get('images',[])]==expected for c in calls)

def verify_delivery(broker,frames,n):
 d=broker.transport.last_delivery
 if not d or d['round']!=n:return False
 original=[hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()for f in frames]
 actual=[i['sha256']for i in d['images']]
 if not actual or actual[-1]!=original[-1]:return False
 if not d['trim_events']:return actual==original
 selection=broker.transport.selection
 return actual==[hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()for f in selection.kept]and all((i['width'],i['height'])==(1280,720)for i in d['images'])
