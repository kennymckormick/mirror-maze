from maze_bench.campaign import launchable_rows
import json
import pytest
from maze_bench.campaign import snapshot
from maze_bench.common import dump,sha


def rows_for(model, level, count=4):
    return [{'id': f'{model}-{level}-{i}', 'model': model, 'difficulty': level}
            for i in range(count)]


def test_each_model_advances_independently_with_four_case_limit():
    gpt = rows_for('gpt', 'easy') + rows_for('gpt', 'hard')
    kimi = rows_for('kimi', 'easy') + rows_for('kimi', 'medium')
    deepseek = rows_for('deepseek', 'medium') + rows_for('deepseek', 'hard')
    done = {r['id'] for r in gpt[:4]}
    active = {r['id'] for r in kimi[:4]}
    stages, ready = launchable_rows(gpt + kimi + deepseek, done, active, set())
    assert stages == {'gpt': 'hard', 'kimi': 'easy', 'deepseek': 'medium'}
    assert [r['id'] for r in ready] == [r['id'] for r in gpt[4:] + deepseek[:4]]


def test_a_blocked_easy_case_blocks_only_its_model_and_live_cleanup_uses_slot():
    a = rows_for('a', 'easy', 1) + rows_for('a', 'medium')
    b = rows_for('b', 'easy') + rows_for('b', 'medium')
    done = {r['id'] for r in b[:4]}
    stages, ready = launchable_rows(a + b, done, {b[0]['id']}, {a[0]['id']})
    assert stages == {'a': 'easy', 'b': 'medium'}
    assert len(ready) == 3
    assert all(r['model'] == 'b' and r['difficulty'] == 'medium' for r in ready)


def test_cursor_six_slots_fill_in_difficulty_order_and_refill_immediately():
    rows=rows_for('gemini','easy')+rows_for('gemini','medium')+rows_for('gemini','hard')
    stages,ready=launchable_rows(rows,set(),set(),set(),limit=6,rolling=True)
    assert len(ready)==6 and[d['difficulty']for d in ready]==['easy']*4+['medium']*2
    finished={ready[0]['id']};active={r['id']for r in ready[1:]}
    _,refill=launchable_rows(rows,finished,active,set(),limit=6,rolling=True)
    assert [r['id']for r in refill]==[rows[6]['id']]


@pytest.mark.parametrize('scenario,expected',[('old_alert','planning_cap'),('new_alert','needs_attention'),('tampered_result','needs_attention')])
def test_terminal_audit_preserves_completed_outcome_without_hiding_new_or_unbound_alert(tmp_path,scenario,expected):
    case=tmp_path/'case';case.mkdir()
    result={'status':'planning_cap','planning_rounds':200,'actions':400,'collisions':3}
    dump(case/'result.json',result);dump(case/'validation.json',{'all_checked_invariants_passed':True})
    dump(case/'audited.json',{'complete':True,'audit_passed':True,'ended_at':200,'result_sha256':sha(case/'result.json'),'validation_sha256':sha(case/'validation.json')})
    dump(case/'needs-attention.json',{'at':300 if scenario=='new_alert'else 100,'error':'Earlier interruption'})
    if scenario=='tampered_result':dump(case/'result.json',dict(result,actions=401))
    row={'id':'fixture','model':'fixture','mode':'harness','difficulty':'easy','topology':'square','side_length':7,'cells':49,'seed':'MIRROR-042','target_cap':200,'kind':'fresh','reuse_type':None,'case_dir':str(case)}
    dump(tmp_path/'manifest.json',{'schema':'fixture','rows':[row]})
    value=snapshot(tmp_path)
    assert value['rows'][0]['status']==expected
    assert value['complete_targets']==(1 if expected=='planning_cap'else 0)
    assert json.loads((case/'needs-attention.json').read_text())['error']=='Earlier interruption'
