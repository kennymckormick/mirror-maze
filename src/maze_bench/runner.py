import collections,datetime,getpass,gzip,hashlib,json,os,re,shutil,sys,time,traceback,uuid,threading
from pathlib import Path
from mirror_maze import MirrorMaze
from .common import dump,parse_plan as parse
from .native import Native
from .clients import API
from .bridge import Broker
from .rules import RULES
from .settings import environment_options, frame_name, planning_request
CACHE_LOCK=threading.RLock()
def run(cfg,key,case,cap=200,vision_probe=False,resume=False,recovered_reply=None):
 case=case.resolve();case.mkdir(parents=True,exist_ok=True);cfg=dict(cfg,direct_observations=True,observation_only=False,max_planning=cap)
 old=None;old_episode=None;old_result=None;native_id=None
 if resume:
  old=json.loads((case/'progress.json').read_text());old_episode=json.loads((case/'episode.json').read_text());old_result=json.loads((case/'result.json').read_text())
  if old['status']!='infrastructure_error' or old.get('protocol')not in('host-automatic-images-v2','round-prune-v3'):raise ValueError('Only interrupted formal V2 episodes may resume')
  backup=case/'interruptions'/str(time.time_ns());backup.mkdir(parents=True)
  for name in ['config.json','progress.json','episode.json','result.json','wire.json','native-invocation.json']:
   if(case/name).exists():shutil.copy2(case/name,backup/name)
  if cfg.get('harness'):
   with gzip.open(case/'native.jsonl.gz','rt')as f:
    for line in f:
     try:e=json.loads(line)
     except Exception:continue
     if cfg.get('harness')=='codex'and e.get('type')=='thread.started':native_id=e['thread_id']
     if cfg.get('harness')=='agy'and e.get('event')=='init':native_id=e['conversation_id']
     if cfg.get('harness')=='claude'and e.get('type')=='system'and e.get('subtype')=='init':native_id=e['session_id']
  if not native_id and cfg.get('harness'):raise RuntimeError('Native conversation ID missing; do not start a fresh episode')
  if cfg.get('harness')=='agy':cfg['_resume_conversation']=native_id
  if cfg.get('harness')=='claude':
   profile=Path(cfg['profile_root'])/'claude/projects'
   matches=list(profile.glob('*/'+native_id+'.jsonl'))
   if len(matches)!=1:raise RuntimeError('Saved Claude conversation is missing or ambiguous')
   cfg['_resume_transcript']=str(matches[0])
  cfg['_resume_from']=str(backup.relative_to(case))
  dump(case/'result.json',{'case_id':case.name,'status':'running','success':None,'resume_from':cfg['_resume_from']})
 dump(case/'config.json',cfg)
 broker=Broker(key,cfg,case);native=None;events=old['events']if resume else[];invalid=old['invalid_sequences']if resume else 0;status='running';error=None;started=time.time()-(old.get('elapsed_seconds',0)if resume else 0);env=None
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
  dump(case/'progress.json',{'protocol':'round-prune-v3','status':status,'planning_rounds':len(events),'invalid_sequences':invalid,'total_collisions':(env.state()['collisions']if env else 0)+invalid,'state':env.export_episode()['state']if env else{},'events':events,'elapsed_seconds':time.time()-started,'error':error})
 initial=None;pending=[]
 try:
  if not vision_probe:
   if resume:
    env.perform([a['action']for a in old_episode['actions']],stop_on_collision=False)
    if env.export_episode()['state']!=old_episode['state']:raise RuntimeError('Restored full state differs from interrupted episode')
    pending=[{'file':s['frame'],'path':str(cache/s['frame']),'label':f'After action {i+1}/{len(events[-1]["steps"])}: '+str(s['action'])+'. Outcome: '+json.dumps(s['result']),'state':s['state']}for i,s in enumerate(events[-1]['steps'])]if events else[frame('Initial first-person observation. 1280x720 PNG; no map or HUD.')]
    initial=old_result.get('initial_frame')or(events[0]['input_frames'][0]if events else pending[0]['file'])
   else:pending=[frame('Initial first-person observation. 1280x720 PNG; no map or HUD.')];initial=pending[0]['file']
  save();native=None if recovered_reply else(Native if cfg.get('harness')else API)(cfg,case,broker)
  if resume and cfg.get('harness')=='codex':native.thread_id=native_id
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
    raw=recovered_reply['response'];seconds=recovered_reply['seconds']
   else:
    if native is None:
     native=(Native if cfg.get('harness')else API)(cfg,case,broker)
     if resume and cfg.get('harness')=='codex':native.thread_id=native_id
    start_call=len(broker.calls);raw=native.ask(text,timeout=cfg.get('harness_timeout',3600));seconds=native.turns[-1]['seconds']
   delivered=verify_delivery(broker,pending,n)
   delivery=broker.transport.last_delivery
   if not delivered:raise RuntimeError('required_current_images_not_delivered; no action executed or collision charged')
   p,reason=parse(raw);before=env.export_episode()['state'];e={'round':n,'response':raw,'plan':str(p.get('plan',''))if p else'','actions':p.get('actions')if p else None,'invalid':p is None,'invalid_reason':reason,'before':before,'input_frames':[f['file']for f in pending],'steps':[],'seconds':seconds,'observation_delivery_verified':True,'input_frames_sent':[i['file']for i in delivery['images']],'image_trim_events':delivery['trim_events'],'input_request_index':delivery['request_index']}
   if recovered_reply:e['recovered_committed_public_reply']=recovered_reply['evidence'];recovered_reply=None
   pending=[]
   if p is None:
    invalid+=1;f=frame('Invalid action sequence: one collision penalty, no movement. Current first-person image.');pending=[f];e['steps'].append({'action':None,'result':{'collision':True,'reason':reason},'frame':f['file'],'state':f['state']})
   else:
    for i,a in enumerate(p['actions']):
     result=env.perform(a,stop_on_collision=False)['results'][0];f=frame(f'After action {i+1}/{len(p["actions"])}: {a}. Outcome: '+json.dumps(result));pending.append(f);e['steps'].append({'action':a,'result':result,'frame':f['file'],'state':f['state']})
     if env.state()['success']:break
   e['after']=env.export_episode()['state'];events.append(e);save();print(json.dumps({'case':case.name,'round':n,'actions':len(e['steps']),'collisions':env.state()['collisions']+invalid,'success':env.state()['success']},ensure_ascii=False),flush=True)
   if env.state()['success']:status='success';break
  else:status='planning_cap'
 except Exception as exc:status='infrastructure_error';error=type(exc).__name__+': '+str(exc);traceback.print_exc()
 finally:
  save()
  if native:
   if native.thread_id:dump(case/'native-session.json',{'thread_id':native.thread_id})
   native.close()
  broker.close()
  if env:env.close()
 result={'case_id':case.name,'protocol':'round-prune-v3','model':cfg['model_label'],'source_alias':cfg['source_alias'],'harness':cfg.get('harness'),'topology':cfg['topology'],'seed':cfg['seed'],'planning_cap':cap,'status':status,'success':status=='success','planning_rounds':len(events),'collisions':invalid+sum(s['result'].get('collision',False)for e in events if not e['invalid']for s in e['steps']),'invalid_sequences':invalid,'actions':sum(len(e['steps'])for e in events if not e['invalid']),'seconds':time.time()-started,'api_calls':len(broker.calls),'vision_api_calls':sum(bool(c.get('images'))for c in broker.calls),'mode':'harness'if cfg.get('harness')else'api','initial_frame':initial,'error':error};dump(case/'result.json',result);return result

def verify_images(calls,frames,n):
 expected=[hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()for f in frames]
 return any(c.get('http_status')==200 and c.get('observation_round')==n and[i['sha256']for i in c.get('images',[])]==expected for c in calls)

def verify_delivery(broker,frames,n):
 d=broker.transport.last_delivery
 if not d or d['round']!=n:return False
 original=[hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()for f in frames]
 actual=[i['sha256']for i in d['images']]
 if not actual or actual[-1]!=original[-1]:return False
 if not d['trim_events']:return actual==original
 selection=broker.transport.selection
 return actual==[hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()for f in selection.kept]and all((i['width'],i['height'])==(1280,720)for i in d['images'])
