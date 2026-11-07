"""Offline text-only harness fixtures: no model accounts or external calls."""
import collections,gzip,json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest
from mirror_maze import MirrorMaze
from maze_bench.common import dump
from maze_bench.text_mode import run_text,audit_text,recover_text_reply,TextTransport,TextSourceError,prompt,source_fault

def route(episode,heading=None):
    cells=episode['maze'];H=4;start=(episode['initial']['cell'],episode['initial']['heading']if heading is None else heading);q=collections.deque([start]);parent={start:None};via={}
    while q:
        cell,h=q.popleft()
        if cell==episode['goal']:
            state=(cell,h);result=[]
            while parent[state]is not None:result.append(via[state]);state=parent[state]
            return list(reversed(result))
        for action in ['left','right','forward']:
            target=(cell,(h+(-1 if action=='left'else 1))%H)
            if action=='forward':
                normal=[(1,0),(0,1),(-1,0),(0,-1)][h];edge=next(e for e in cells[cell]['edges']if abs(e['normal'][0]-normal[0])<.01 and abs(e['normal'][1]-normal[1])<.01)
                if not edge['open']:continue
                target=(edge['neighbor'],h)
            if target not in parent:parent[target]=(cell,h);via[target]=action;q.append(target)
    raise AssertionError('No canonical goal route')

@pytest.fixture
def setup(tmp_path,monkeypatch):
    with MirrorMaze(topology='square',difficulty='easy',side_length=7,seed='MIRROR-042')as env:episode=env.export_episode()
    def forbid_render(*_,**__):raise AssertionError('Text mode must never render')
    monkeypatch.setattr(MirrorMaze,'render',forbid_render)
    executable=tmp_path/'fake-claude';executable.write_text('''#!/usr/bin/env python3
import json,os,sys,urllib.request
from pathlib import Path
profile=Path(os.environ['CLAUDE_CONFIG_DIR'])/'projects/fixture';profile.mkdir(parents=True,exist_ok=True);(profile/'fixture-session.jsonl').write_text('{}\\n')
print(json.dumps({'type':'system','subtype':'init','session_id':'fixture-session'}),flush=True)
history=[]
for line in sys.stdin:
 e=json.loads(line);text=e['message']['content'][0]['text'];history.append({'role':'user','content':text})
 req=urllib.request.Request(os.environ['ANTHROPIC_BASE_URL']+'/v1/messages',data=json.dumps({'model':'fixture','messages':history,'stream':False}).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req)as r:response=json.load(r)
 answer=''.join(b['text']for b in response['content']);history.append({'role':'assistant','content':answer})
 print(json.dumps({'type':'result','is_error':False,'result':answer}),flush=True)
''');executable.chmod(0o700)
    requests=[];answers=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(body);answer=answers[min(len(requests)-1,len(answers)-1)];raw=json.dumps({'type':'message','role':'assistant','model':'fixture','content':[{'type':'text','text':answer}],'stop_reason':'end_turn','usage':{'input_tokens':1,'output_tokens':1}}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    cfg={'model_label':'fixture','source_alias':'fixture','harness':'claude','harness_executable':str(executable),'profile_root':str(tmp_path/'profile'),'case_id':'fixture','run_id':'fixture','api_base':f'http://127.0.0.1:{server.server_port}','difficulty_schedule':'balanced-v1','topology':'square','difficulty':'easy','side_length':7,'seed':'MIRROR-042','planning_timeout':30,'request_timeout':30,'observation_modality':'text','max_planning':2,'isolate_upstream_session':True}
    yield cfg,episode,answers,requests,tmp_path/'case'
    server.shutdown();server.server_close();thread.join(2)

def test_only_ordered_action_outcomes_reach_second_planning_call_and_replay_audits(setup):
    cfg,episode,answers,requests,case=setup;answers.extend([json.dumps({'actions':['left']}),json.dumps({'actions':route(episode,(episode['initial']['heading']-1)%4)})]);r=run_text(cfg,'fixture-credential',case,cap=2)
    assert r['status']=='success'and r['planning_rounds']==2 and r['images_sent']==r['render_calls']==0
    later=requests[1]['messages'][-1]['content'];assert json.loads(later)=={'results':[{'action':'left','outcome':'ok'}],'sequence_rejected':False}
    assert not any(word in later for word in ['heading','visited','cell','goal','coordinates','image'])
    assert audit_text(case,cfg)['all_checked_invariants_passed']
    assert not list(case.rglob('*.png'))

def test_quota_notice_is_infrastructure_without_planning_action_or_collision_charge(setup):
    cfg,episode,answers,requests,case=setup;answers.append('⚠ Individual quota reached. Please upgrade your subscription. Resets in59m27s.')
    r=run_text(cfg,'fixture-credential',case,cap=2);assert r['status']=='infrastructure_error';assert r['planning_rounds']==r['actions']==r['collisions']==0
    assert(case/'retry-after.json').exists();assert recover_text_reply(case,cfg)is None
    assert len(requests)==1

def test_genuine_invalid_model_plan_keeps_the_declared_penalty(setup):
    cfg,episode,answers,requests,case=setup;answers.append('This is not a JSON action plan.');cfg['max_planning']=1
    r=run_text(cfg,'fixture-credential',case,cap=1);assert r['status']=='planning_cap'and r['planning_rounds']==r['collisions']==1 and r['actions']==0
    assert audit_text(case,cfg)['feedback_whitelist_verified']

def test_no_image_payload_is_allowed_even_if_accidentally_supplied(tmp_path):
    transport=TextTransport('fixture-credential','http://127.0.0.1:1','fixture',tmp_path,{'harness':'claude'});transport.set_feed({'round':1,'text':'feedback'})
    with pytest.raises(TextSourceError,match='image_block'):transport.request({'messages':[{'role':'user','content':[{'type':'image','source':{'data':'not-sent'}}]}]},'/v1/messages')
    assert transport.calls==[]

def test_saved_committed_text_reply_is_replayed_once_without_new_inference(setup):
    cfg,episode,answers,requests,case=setup;answers.extend([json.dumps({'actions':['left']}),json.dumps({'actions':route(episode,(episode['initial']['heading']-1)%4)})]);assert run_text(cfg,'fixture-credential',case,cap=2)['success'];full=json.loads((case/'progress.json').read_text());first=full['events'][0]
    with MirrorMaze(topology='square',difficulty='easy',side_length=7,seed='MIRROR-042')as env:
        env.step('left');dump(case/'episode.json',env.export_episode())
    partial=dict(full,status='infrastructure_error',events=[first],planning_rounds=1,state=first['after']);dump(case/'progress.json',partial);dump(case/'result.json',{'status':'infrastructure_error'})
    recovered=recover_text_reply(case,cfg);assert recovered['round']==2
    before=len(requests);r=run_text(cfg,'fixture-credential',case,cap=2,resume=True,recovered_reply=recovered);assert r['success']and len(requests)==before
    assert json.loads((case/'progress.json').read_text())['events'][-1]['recovered_committed_reply']
    assert audit_text(case,cfg)['all_checked_invariants_passed']

def test_initial_rules_only_and_no_false_quota_for_json_model_intention():
    cfg={'topology':'hex','difficulty_schedule':'balanced-v1','difficulty':'easy','side_length':5,'max_planning':200};text=prompt(cfg,1,{})
    assert 'hexagonal'in text and '60 degrees'in text and '200 planning'in text
    assert prompt(cfg,2,{'results':[],'sequence_rejected':True})=='{"results":[],"sequence_rejected":true}'
    assert source_fault('{"plan":"Individual quota reached is just a quoted phrase","actions":["left"]}')is None

def test_empty_signature_history_compatibility_preserves_content_and_private_proof(tmp_path):
    import hashlib
    from maze_bench.text_mode import compatible_assistant_history,private_request
    thought='fixture reasoning\nretain every character: λ';payload={'messages':[
        {'role':'assistant','content':[{'type':'thinking','thinking':thought,'signature':''},{'type':'text','text':'public plan'}]},
        {'role':'assistant','content':[{'type':'thinking','thinking':'signed','signature':'valid'}]},
        {'role':'user','content':'{"results":[],"sequence_rejected":false}'}]}
    original=json.loads(json.dumps(payload));converted,proof=compatible_assistant_history(payload)
    assert payload==original and converted['messages'][0]['content']==[{'type':'text','text':thought},{'type':'text','text':'public plan'}]
    assert converted['messages'][1:]==payload['messages'][1:]
    assert proof==[{'chars':len(thought),'sha256':hashlib.sha256(thought.encode()).hexdigest()}]
    cfg={'harness':'claude','text_history_compatibility':'assistant_thinking_as_text_v1'}
    transport=TextTransport('fixture-secret','http://127.0.0.1:1','DeepSeek-V4.1-Flash-1017-Ark',tmp_path,cfg);transport.set_feed({'round':2,'text':'feedback'})
    body=transport._body(payload,'/v1/messages',True);record={};transport.record_request_body(record,body)
    assert private_request(record,tmp_path)['messages']==converted['messages']
    assert record['preserved_assistant_thinking']==proof
    cfg['text_history_compatibility']='assistant_text_string_v2';body=transport._body(payload,'/v1/messages',True);record={};transport.record_request_body(record,body)
    assert private_request(record,tmp_path)['messages'][0]['content']==thought+'public plan'
    assert private_request(record,tmp_path)['messages'][1:]==payload['messages'][1:]
    other=TextTransport('fixture-secret','http://127.0.0.1:1','wrong-source',tmp_path,cfg);other.set_feed({'round':2,'text':'feedback'})
    with pytest.raises(AssertionError):other._body(payload,'/v1/messages',True)

def test_native_max_token_continuation_requires_exact_saved_parent_output(tmp_path):
    import hashlib
    from maze_bench.text_mode import matching_call
    private=tmp_path/'private';private.mkdir();feedback='{"results":[],"sequence_rejected":false}'
    first={'messages':[{'role':'user','content':feedback}]};second={'messages':[{'role':'user','content':feedback},{'role':'assistant','content':[{'type':'text','text':'partial public output'}]},{'role':'user','content':'Native continuation instruction'}]};calls=[]
    for i,payload,answer,stop in [(1,first,'partial public output','max_tokens'),(2,second,'{"actions":["left"]}','end_turn')]:
        body=json.dumps(payload).encode();request=private/('request'+str(i)+'.gz');response=private/('response'+str(i)+'.gz')
        with gzip.open(request,'wb')as f:f.write(body)
        with gzip.open(response,'wb')as f:f.write(json.dumps({'type':'message','role':'assistant','content':[{'type':'text','text':answer}],'stop_reason':stop}).encode())
        calls.append({'index':i,'observation_round':98,'main_inference':i==1,'http_status':200,'observation_modality':'text','no_image_payload':True,'images':[],'private_request_file':str(request.relative_to(tmp_path)),'request_sha256':hashlib.sha256(body).hexdigest(),'private_response_file':str(response.relative_to(tmp_path))})
    dump(tmp_path/'wire.json',calls);assert matching_call(tmp_path,{'harness':'claude'},98,feedback,'{"actions":["left"]}')['index']==2
    second['messages'][1]['content'][0]['text']='different partial output';body=json.dumps(second).encode()
    with gzip.open(private/'request2.gz','wb')as f:f.write(body)
    calls[1]['request_sha256']=hashlib.sha256(body).hexdigest();dump(tmp_path/'wire.json',calls)
    with pytest.raises(TextSourceError,match='no_matching_committed_request'):matching_call(tmp_path,{'harness':'claude'},98,feedback,'{"actions":["left"]}')

def test_official_checkpoint_uses_last_accepted_public_reply_and_leaves_failed_tail(tmp_path):
    from maze_bench.text_mode import claude_checkpoint_at_accepted_reply
    transcript=tmp_path/'native-session.jsonl';rows=[{'type':'assistant','sessionId':'same','uuid':'accepted','message':{'content':[{'type':'text','text':'{"actions":["left"]}'}]}},{'type':'user','sessionId':'same','uuid':'unanswered','message':{'content':'same feedback'}},{'type':'assistant','sessionId':'same','uuid':'empty','message':{'content':[]}},{'type':'assistant','sessionId':'foreign','uuid':'foreign','message':{'content':[{'type':'text','text':'{"actions":["left"]}'}]}}];transcript.write_text('\n'.join(json.dumps(r)for r in rows));before=transcript.read_bytes();checkpoint=claude_checkpoint_at_accepted_reply(transcript,'same',{'round':5,'response':'{"actions":["left"]}'})
    assert checkpoint['assistant_uuid']=='accepted'and checkpoint['committed_rounds']==5 and transcript.read_bytes()==before
    with pytest.raises(TextSourceError,match='missing_native_checkpoint'):claude_checkpoint_at_accepted_reply(transcript,'same',{'round':6,'response':'unsaved'})

def test_multiple_native_token_continuations_require_every_exact_ancestor(tmp_path):
    import copy,hashlib
    from maze_bench.text_mode import matching_call
    private=tmp_path/'private';private.mkdir();feedback='{"results":[],"sequence_rejected":false}';payload={'messages':[{'role':'user','content':feedback}]};calls=[]
    for i,answer,stop in [(1,'first partial','max_tokens'),(2,'second partial','max_tokens'),(3,'{"actions":["right"]}','end_turn')]:
        body=json.dumps(payload).encode();request=private/('request'+str(i)+'.gz');response=private/('response'+str(i)+'.gz')
        with gzip.open(request,'wb')as f:f.write(body)
        with gzip.open(response,'wb')as f:f.write(json.dumps({'type':'message','role':'assistant','content':[{'type':'text','text':answer}],'stop_reason':stop}).encode())
        calls.append({'index':i,'observation_round':92,'main_inference':i==1,'http_status':200,'observation_modality':'text','no_image_payload':True,'images':[],'private_request_file':str(request.relative_to(tmp_path)),'request_sha256':hashlib.sha256(body).hexdigest(),'private_response_file':str(response.relative_to(tmp_path))})
        payload['messages'] += [{'role':'assistant','content':[{'type':'text','text':answer}]},{'role':'user','content':'Native continuation'}]
    dump(tmp_path/'wire.json',calls);assert matching_call(tmp_path,{'harness':'claude'},92,feedback,'{"actions":["right"]}')['index']==3
    with gzip.open(private/'request2.gz','rb')as f:broken=json.loads(f.read())
    broken['messages'][1]['content'][0]['text']='not the first provider reply';body=json.dumps(broken).encode()
    with gzip.open(private/'request2.gz','wb')as f:f.write(body)
    calls[1]['request_sha256']=hashlib.sha256(body).hexdigest();dump(tmp_path/'wire.json',calls)
    with pytest.raises(TextSourceError,match='no_matching_committed_request'):matching_call(tmp_path,{'harness':'claude'},92,feedback,'{"actions":["right"]}')

def test_native_compaction_requires_profile_anchor_native_boundary_and_exact_summary(tmp_path):
    import hashlib
    from maze_bench.text_mode import COMPACT_HEADER,OUTPUT_LIMIT_CONTINUE,matching_call
    private=tmp_path/'private';private.mkdir();feedback='{"results":[],"sequence_rejected":false}';summary='\nRemember only the fictional feedback.\n';wrapper=COMPACT_HEADER+summary+'SDK continuation footer';answer='{"actions":["forward"]}';calls=[]
    payloads=[{'messages':[{'role':'user','content':feedback}]},{'messages':[{'role':'user','content':[{'type':'text','text':wrapper+'\n'},{'type':'text','text':OUTPUT_LIMIT_CONTINUE}]}]}]
    for i,payload,response_text in [(1,payloads[0],'<analysis>fixture only</analysis><summary>'+summary+'</summary>'),(2,payloads[1],answer)]:
        body=json.dumps(payload).encode();request=private/('request'+str(i)+'.gz');response=private/('response'+str(i)+'.gz')
        with gzip.open(request,'wb')as f:f.write(body)
        with gzip.open(response,'wb')as f:f.write(json.dumps({'type':'message','role':'assistant','content':[{'type':'text','text':response_text}],'stop_reason':'end_turn'}).encode())
        calls.append({'index':i,'observation_round':7,'main_inference':i==1,'http_status':200,'observation_modality':'text','no_image_payload':True,'images':[],'private_request_file':str(request.relative_to(tmp_path)),'request_sha256':hashlib.sha256(body).hexdigest(),'private_response_file':str(response.relative_to(tmp_path))})
    dump(tmp_path/'wire.json',calls);profile=tmp_path/'profile/claude/projects/project/session.jsonl';profile.parent.mkdir(parents=True)
    entries=[{'type':'system','subtype':'compact_boundary','uuid':'boundary','sessionId':'session'},{'type':'user','isCompactSummary':True,'uuid':'summary','parentUuid':'old-parent','sessionId':'session','version':'2.1.303','message':{'content':wrapper}},{'type':'user','uuid':'tail','sessionId':'session','message':{'content':OUTPUT_LIMIT_CONTINUE}}]
    profile.write_text('\n'.join(json.dumps(e)for e in entries));events=[{'type':'host_text_planning_request','round':7,'prompt_sha256':hashlib.sha256(feedback.encode()).hexdigest(),'at':100},{'type':'system','subtype':'init','session_id':'session'},{'type':'system','subtype':'compact_boundary','uuid':'boundary','session_id':'session','compact_metadata':{'trigger':'auto','pre_tokens':100,'post_tokens':10,'preserved_segment':{'anchor_uuid':'summary','tail_uuid':'tail'}}},{'type':'result','is_error':False,'result':answer}]
    with gzip.open(tmp_path/'native.jsonl.gz','wt')as f:f.write('\n'.join(json.dumps(e)for e in events)+'\n')
    cfg={'harness':'claude','profile_root':str(tmp_path/'profile')};call=matching_call(tmp_path,cfg,7,feedback,answer);assert call['index']==2 and call['native_compaction_proof']['summary_request_index']==1
    native_bytes=(tmp_path/'native.jsonl.gz').read_bytes();(tmp_path/'native.jsonl.gz').write_bytes(native_bytes[:-8])
    assert matching_call(tmp_path,cfg,7,feedback,answer)['index']==2
    (tmp_path/'native.jsonl.gz').write_bytes(native_bytes)
    entries[1]['uuid']='foreign-anchor';profile.write_text('\n'.join(json.dumps(e)for e in entries))
    with pytest.raises(TextSourceError,match='no_matching_committed_request'):matching_call(tmp_path,cfg,7,feedback,answer)
    entries[1]['uuid']='summary';profile.write_text('\n'.join(json.dumps(e)for e in entries));full_events=list(events);events=[e for e in full_events if e.get('type')!='result']
    with gzip.open(tmp_path/'native.jsonl.gz','wt')as f:f.write('\n'.join(json.dumps(e)for e in events)+'\n')
    with pytest.raises(TextSourceError,match='no_matching_committed_request'):matching_call(tmp_path,cfg,7,feedback,answer)
    events=[e for e in full_events if e.get('subtype')!='compact_boundary']
    with gzip.open(tmp_path/'native.jsonl.gz','wt')as f:f.write('\n'.join(json.dumps(e)for e in events)+'\n')
    with pytest.raises(TextSourceError,match='no_matching_committed_request'):matching_call(tmp_path,cfg,7,feedback,answer)

def test_uncharged_empty_provider_reply_requires_matching_genuine_native_commit(setup):
    cfg,episode,answers,requests,case=setup;cfg['max_planning']=1;answers.append('');assert run_text(cfg,'fixture-credential',case,cap=1)['status']=='infrastructure_error'
    calls=json.loads((case/'wire.json').read_text());calls[0].pop('provider_fault',None);dump(case/'wire.json',calls)
    assert recover_text_reply(case,cfg)is None and(case/'verified-uncharged-source-faults/wire-1.json').exists()
    with gzip.open(case/'native.jsonl.gz','rt')as f:events=[json.loads(line)for line in f]
    with gzip.open(case/'native.jsonl.gz','wt')as f:
        for e in events:
            if e.get('type')!='result':f.write(json.dumps(e)+'\n')
    with pytest.raises(TextSourceError,match='requires_native_reconciliation'):recover_text_reply(case,cfg)


def test_cli_text_run_and_completed_resume_do_not_render_or_resample(setup, monkeypatch):
    import sys
    from maze_bench.cli import main

    cfg, episode, answers, requests, case = setup
    answers.extend([json.dumps({'actions':['left']}),
                    json.dumps({'actions':route(episode,(episode['initial']['heading']-1)%4)})])
    config = case.parent/'cli-text-config.json'
    config.write_text(json.dumps({'topologies':['square'],'seeds':['MIRROR-042'],
                                  'models':[dict(cfg,model_key='fixture')]}))
    output = case.parent/'cli-run'
    monkeypatch.setattr(Path,'home',lambda: case.parent/'isolated-home')
    monkeypatch.setenv('MIRROR_MAZE_API_KEY','fixture-credential')
    command = ['mirror-maze-bench','run','--observation','text','--mode','harness',
               '--config',str(config),'--base-url',cfg['api_base'],'--difficulty','easy',
               '--out',str(output),'--max-planning','2','--workers','1']
    monkeypatch.setattr(sys,'argv',command)
    main()
    result = output/'fixture-square-MIRROR-042'/'result.json'
    terminal = json.loads(result.read_text())
    assert terminal['success'] and terminal['planning_rounds']==2 and terminal['images_sent']==0
    assert (result.parent/'audited.json').exists()
    assert len(requests)==2
    monkeypatch.setattr(sys,'argv',command+['--resume'])
    main()
    assert len(requests)==2 and json.loads(result.read_text())==terminal
