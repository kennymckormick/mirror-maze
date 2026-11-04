import hashlib
import pytest
from maze_bench.settings import frame_name, geometry, planning_request
from maze_bench.rules import RULES

def test_easy_cache_and_initial_prompt_remain_compatible():
    cfg={'topology':'hex','seed':'MIRROR-042'};st={'cell':0,'heading':0}
    assert frame_name(cfg,st)==hashlib.sha256(b'hex|MIRROR-042|0|0|1280x720|10|0.2.0').hexdigest()+'.png'
    text=planning_request(cfg,1,200,1,{})
    assert text.startswith(RULES+'\n')and 'easy side7'in text
    assert frame_name(dict(cfg,side_length=11),st)!=frame_name(cfg,st)
    assert geometry({'difficulty':'medium'})==('medium',11)
    with pytest.raises(ValueError):geometry({'difficulty':'medium','side_length':7})
