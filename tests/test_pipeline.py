"""A synthetic API fixture validates plumbing; never a real-model score."""
import json,threading
import pytest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from mirror_maze import MirrorMaze
from maze_bench.runner import run
from maze_bench.audit import validate
from maze_bench.common import dump
from maze_bench.report import build_study

@pytest.mark.parametrize('difficulty,cap',[('easy',1),('medium',500)])
def test_api_pipeline_reaches_actual_goal_and_replays(tmp_path,difficulty,cap):
    seed='LOCAL-PIPELINE-SMOKE'
    with MirrorMaze(topology='square',seed=seed,difficulty=difficulty)as env:
        route=env.debug_solution();initial_episode=env.export_episode()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert request['model']=='synthetic-fixture'
            content=json.dumps(request['messages'])
            assert f'At most{cap} planning decisions.'in content
            assert ('medium side11'if difficulty=='medium'else'easy side7')in content
            assert any(b.get('type')=='image_url'for m in request['messages']if isinstance(m['content'],list)for b in m['content'])
            result={'model':'synthetic-fixture','choices':[{'message':{'content':json.dumps({'plan':'Synthetic transport fixture only','actions':route})}}]}
            raw=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    case=tmp_path/'case';cache=tmp_path/'cache';cache.mkdir()
    cfg={'model_key':'fixture','model_label':'synthetic fixture','source_alias':'synthetic-fixture','harness':None,'api_protocol':'chat','request_params':{},'api_base':f'http://127.0.0.1:{server.server_port}','cache_dir':str(cache),'topology':'square','seed':seed,'difficulty':difficulty}
    try:result=run(cfg,'test-only-key',case,cap=cap)
    finally:server.shutdown();server.server_close();thread.join()
    assert result['success']and result['planning_rounds']==1 and result['collisions']==0
    report=validate([case],cache,tmp_path/'validation.json')
    assert report['all_checked_invariants_passed']
    bundle,_=build_study(None,tmp_path,cache,tmp_path/'review')
    data=bundle['modes']['api'];assert len(data['cases'])==1 and data['modelOrder']==['fixture']
    assert data['cases'][0]['success']and data['cases'][0]['observationValid']
    assert data['protocol']['maxPlanning']==cap
    m=data['maps'][data['cases'][0]['map']]
    assert m['geometry']['cellCount']==(121 if difficulty=='medium'else 49)
    assert m['sideLength']==(11 if difficulty=='medium'else 7)
    assert (tmp_path/'review/api/cases.csv').is_file()
    # Simulate host exit after the complete public answer was saved, before
    # actions/checkpoint committed. The real fixture server is already closed.
    old=json.loads((case/'progress.json').read_text());completed=old['events'][0]
    old.update(status='infrastructure_error',events=[],planning_rounds=0,total_collisions=0,invalid_sequences=0,state=initial_episode['state'])
    dump(case/'progress.json',old);dump(case/'episode.json',initial_episode)
    dump(case/'result.json',{'status':'infrastructure_error','initial_frame':completed['input_frames'][0]})
    wire=json.loads((case/'wire.json').read_text())
    recovered={'round':1,'response':completed['response'],'seconds':completed['seconds'],'wire':wire[-1],'evidence':{'synthetic_crash_recovery_fixture':True}}
    result=run(cfg,'test-only-key',case,cap=cap,resume=True,recovered_reply=recovered)
    assert result['success']and result['planning_rounds']==1 and len(json.loads((case/'wire.json').read_text()))==1
    assert validate([case],cache,tmp_path/'recovered-validation.json')['all_checked_invariants_passed']
