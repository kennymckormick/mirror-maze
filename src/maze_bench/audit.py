"""Replay committed decisions and verify full or explicitly pruned image delivery."""
import hashlib,json,struct
from pathlib import Path
from mirror_maze import MirrorMaze
from .common import dump,parse_plan
from .settings import environment_options, frame_name

def validate(paths,cache,out):
    checks=[];cache=Path(cache)
    for case in paths:
        case=Path(case);cfg=json.loads((case/'config.json').read_text());progress=json.loads((case/'progress.json').read_text());result=json.loads((case/'result.json').read_text());wire=json.loads((case/'wire.json').read_text());hashes={};invalid=0;expected=None
        assert result['status']in('success','planning_cap','infrastructure_error')
        cap=cfg.get('max_planning',200)
        with MirrorMaze(**environment_options(cfg))as env:
            for n,e in enumerate(progress['events'],1):
                assert e['round']==n and e['before']==env.review_state(),(case.name,n,'before')
                if expected is not None:assert e['input_frames']==expected,(case.name,n,'original per-action input order')
                sent=e.get('input_frames_sent',e['input_frames']);trim=e.get('image_trim_events',[])
                if trim:
                    assert sent==[e['input_frames'][-1]],(case.name,n,'latest current frame not pinned')
                    removed=[x for d in trim for x in d['removed']]
                    assert sorted(x['index']for x in removed)==list(range(len(e['input_frames'])-1))
                    assert all(d['round']==n-1 for d in trim)
                else:assert sent==e['input_frames']
                required=[]
                for name in sent:
                    if name not in hashes:
                        raw=(cache/name).read_bytes();assert raw[:8]==b'\x89PNG\r\n\x1a\n'and struct.unpack('>II',raw[16:24])==(1280,720);hashes[name]=hashlib.sha256(raw).hexdigest()
                    required.append(hashes[name])
                calls=[c for c in wire if c.get('http_status')==200 and c.get('observation_round')==n]
                assert any([i['sha256']for i in c.get('images',[])]==required for c in calls),(case.name,n,'sent frame hashes')
                plan,error=parse_plan(e['response']);assert e['invalid']==(plan is None)
                if plan is None:
                    invalid+=1;assert len(e['steps'])==1 and e['steps'][0]['state']==env.review_state()
                else:
                    assert e['actions']==plan['actions'];executed=0
                    for action in plan['actions']:
                        actual=env.perform(action,stop_on_collision=False)['results'][0];s=e['steps'][executed]
                        assert s['action']==action and s['state']==env.review_state()
                        assert s['frame']==frame_name(cfg,s['state'])
                        for k in('accepted','moved','collision','reason','actionCount','moves','collisions','success'):assert s['result'].get(k)==actual.get(k),(case.name,n,k)
                        executed+=1
                        if env.state()['success']:break
                    assert executed==len(e['steps'])
                assert e['after']==env.review_state();expected=[s['frame']for s in e['steps']]
            state=env.review_state();assert progress['state']==state
            assert result['actions']==state['actionCount']and result['collisions']==state['collisions']+invalid
            assert result['planning_rounds']==len(progress['events'])<=cap
            if result['status']=='success':assert state['success']
            if result['status']=='planning_cap':assert len(progress['events'])==cap and not state['success']
        checks.append({'case':case.name,'status':result['status'],'replayed_planning_rounds':len(progress['events']),'ordered_image_rounds_verified':len(progress['events']),'pruned_rounds':sum(bool(e.get('image_trim_events'))for e in progress['events'])})
    report={'all20_complete':len(checks)==20,'all_checked_invariants_passed':True,'cases':checks};dump(out,report);return report
