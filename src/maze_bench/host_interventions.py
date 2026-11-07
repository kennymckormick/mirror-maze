"""Explicitly verify historical host actions, without rewriting model decisions."""
import json
from pathlib import Path
from .common import sha
from .cursor_profile_evidence import child_path


def load_interventions(case,progress):
    case=Path(case);path=case/'host-interventions.json'
    if not path.exists():return []
    manifest=json.loads(path.read_text());assert manifest['schema']=='mirror-maze-host-interventions/v1'
    assert manifest['clean_protocol_comparison_eligible']is False
    entries=manifest['interventions'];seen=set()
    for entry in entries:
        number=entry['before_round'];assert 1<=number<=len(progress['events'])and number not in seen;seen.add(number)
        event=progress['events'][number-1];previous=progress['events'][number-2]['after']if number>1 else None
        assert previous is not None and entry['before']==previous and entry['after']==event['before']
        assert entry['actions']and entry['actions']==event['actions'][:len(entry['actions'])]
        assert entry['after']['actionCount']-entry['before']['actionCount']==len(entry['actions'])
        assert entry['origin']=='uncommitted_prefix_replayed_by_host_after_render_failure'
        assert entry['interrupted_snapshots']
        for source in entry['interrupted_snapshots']:
            file=child_path(case,source['progress_file']);assert sha(file)==source['progress_sha256']
            saved=json.loads(file.read_text())
            assert len(saved['events'])==number-1 and saved['state']==entry['after']and saved['events'][-1]['after']==entry['before']
    return entries


def check_resume_history(case,progress,episode):
    assert progress['state']==episode['state'],'Saved episode/progress states disagree; manual reconciliation required'
    if progress['events']:assert progress['events'][-1]['after']==progress['state'],'Uncommitted physical actions require explicit reconciliation before resume'
    entries=load_interventions(case,progress);by_round={e['before_round']:e for e in entries};actions=[]
    previous=episode['initial']
    for event in progress['events']:
        entry=by_round.get(event['round'])
        if entry:actions.extend(entry['actions'])
        elif event['round']>1:assert event['before']==previous,'Unannotated gap between committed planning rounds'
        actions.extend(s['action']for s in event['steps']if s['action']is not None);previous=event['after']
    assert actions==[a['action']for a in episode['actions']],'Physical action history differs without a validated host intervention'
    return entries


def apply_intervention(env,entry):
    assert env.review_state()==entry['before']
    env.perform(entry['actions'],stop_on_collision=False)
    assert env.review_state()==entry['after']


def intervention_metadata(entries):
    if not entries:return {}
    return {'protocol_deviations':['host_duplicate_execution_before_committed_reply_replay'],
            'clean_protocol_comparison_eligible':False,'host_intervention_actions':sum(len(e['actions'])for e in entries),
            'host_intervention_collisions':sum(e['after']['collisions']-e['before']['collisions']for e in entries),
            'host_intervention_rounds':[e['before_round']for e in entries]}
