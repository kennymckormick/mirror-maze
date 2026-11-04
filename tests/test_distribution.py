"""Verify portable English packaging and frozen semantic geometry equivalence."""
import hashlib
import json
from pathlib import Path
import threading
import urllib.request
import pytest
from mirror_maze import MirrorMaze
from maze_bench.play import create_server

FIXTURE=json.loads((Path(__file__).parent/'fixtures/geometry-v0.2.0.json').read_text())['records']

@pytest.mark.parametrize('record',FIXTURE,ids=lambda r:f'{r["topology"]}-{r["difficulty"]}-{r["seed"]}')
def test_localization_preserves_frozen_geometry_and_shortest_route(record):
    with MirrorMaze(topology=record['topology'],difficulty=record['difficulty'],seed=record['seed'])as env:
        ep=env.export_episode();value={k:ep[k]for k in ['maze','initial','goal','geometry']}
        assert len(ep['maze'])==record['cells']
        assert hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()==record['sha256']
        route=env.debug_solution()
        assert hashlib.sha256(json.dumps(route,separators=(',',':')).encode()).hexdigest()==record['solution_sha256']
        env.perform(route,stop_on_collision=False)
        assert env.state()['success']and env.state()['collisions']==0

def test_play_server_uses_packaged_english_assets_without_a_key(monkeypatch):
    monkeypatch.delenv('MIRROR_MAZE_API_KEY',raising=False)
    server=create_server(port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{server.server_port}/')as r:page=r.read().decode()
        assert 'Mirror Maze' in page and 'lang="en"'in page
        assert 'id="map-panel" hidden'in page
        with opener.open(f'http://127.0.0.1:{server.server_port}/maze.js')as r:rules=r.read().decode()
        assert 'export const VERSION' in rules
        assert not any('\u3400'<=c<='\u9fff'for c in page+rules)
    finally:server.shutdown();server.server_close();thread.join()


def test_cli_refuses_unrecorded_checkpoint_instead_of_resampling(tmp_path,monkeypatch,capsys):
    from maze_bench.cli import main
    import sys
    spec={"defaults":{},"topologies":["square"],"seeds":["S"],"models":[{"model_key":"custom","model_label":"unchanged-native-id","source_alias":"test-source","api_protocol":"chat"}]}
    config=tmp_path/'config.json';config.write_text(json.dumps(spec))
    out=tmp_path/'runs';case=out/'custom-square-S';case.mkdir(parents=True)
    checkpoint=case/'progress.json';checkpoint.write_text('{"status":"running","planning_rounds":12}')
    original=checkpoint.read_bytes();called=[]
    monkeypatch.setenv('MIRROR_MAZE_API_KEY','synthetic-local-test-key')
    monkeypatch.setattr('maze_bench.cli.run',lambda *a,**kw:called.append((a,kw)))
    monkeypatch.setattr(sys,'argv',['mirror-maze-bench','run','--base-url','http://127.0.0.1:1','--config',str(config),'--out',str(out),'--resume'])
    with pytest.raises(SystemExit)as exc:main()
    assert exc.value.code==1 and not called and checkpoint.read_bytes()==original
    assert 'checkpoint exists' in capsys.readouterr().out.lower()
