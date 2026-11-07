"""Replay committed decisions and verify full or explicitly pruned image delivery."""
import base64,gzip,hashlib,json,struct
from pathlib import Path
from mirror_maze import MirrorMaze
from .common import dump,parse_plan
from .settings import environment_options, frame_name,planning_request
from .transport import metadata_response,complete_json_document
from .cursor_protocol import completed_cursor_reply
from .cursor_profile_evidence import profile_completions,child_path
from .host_interventions import load_interventions,apply_intervention,intervention_metadata

def validate(paths,cache,out):
    checks=[];cache=Path(cache)
    for case in paths:
        case=Path(case);cfg=json.loads((case/'config.json').read_text());progress=json.loads((case/'progress.json').read_text());result=json.loads((case/'result.json').read_text());wire=json.loads((case/'wire.json').read_text());hashes={};invalid=0;expected=None
        assert result['status']in('success','planning_cap','infrastructure_error')
        cap=cfg.get('max_planning',200)
        agy_native=cfg.get('harness')=='agy'and cfg.get('native_provider')is True
        direct_native=cfg.get('harness')=='cursor'or agy_native
        native_kind='native_agy_acp_image_attachment'if agy_native else'native_cursor_image_attachment'
        cursor_native=[]
        native_denied_tool_count=0;charged_denial_ids=set()
        interventions=load_interventions(case,progress);by_round={entry['before_round']:entry for entry in interventions}
        profile_evidence=profile_completions(case,cfg,progress,wire)if cfg.get('harness')=='cursor'else{}
        if direct_native:
            with gzip.open(case/'native.jsonl.gz','rt')as f:cursor_native=[json.loads(line)for line in f if line.strip()]
        with MirrorMaze(**environment_options(cfg))as env:
            for n,e in enumerate(progress['events'],1):
                if n in by_round:apply_intervention(env,by_round[n])
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
                calls=[c for c in wire if(c.get('http_status')==200 or(direct_native and c.get('kind')==native_kind and c.get('native_client_finished')))and c.get('observation_round')==n]
                assert any([i['sha256']for i in c.get('images',[])]==required for c in calls),(case.name,n,'sent frame hashes')
                selected=next((c for c in calls if c['index']==e.get('input_request_index')),None)
                if cfg.get('harness')=='cursor':
                    assert selected and selected.get('native_client_finished')and selected.get('no_custom_model_api_bridge')
                    if n not in profile_evidence:
                        proof=selected['native_output_proof'];offset=proof['native_json_event_offset'];end=offset+proof['native_result_event_index']+1
                        text,_,native_result=completed_cursor_reply(cursor_native[offset:end]);assert text==e['response']
                        assert hashlib.sha256(json.dumps(native_result,sort_keys=True).encode()).hexdigest()==selected['native_result_sha256']
                    attachment_paths=[]
                    for attachment in selected['attachments']:
                        path=Path(attachment['path'])
                        if not path.exists():
                            suffix=attachment['path'].split('/workspace/',1)
                            assert len(suffix)==2
                            path=child_path(Path(cfg['profile_root'])/'workspace',suffix[1])
                        attachment_paths.append(path)
                    assert [hashlib.sha256(path.read_bytes()).hexdigest()for path in attachment_paths]==required
                    assert 'http_status'not in selected
                if agy_native:
                    from .agy_protocol import completed_agy_reply,ensure_fresh_denied_tools
                    from .agy_recovery import declaration_images_match,result_sha
                    assert selected and selected.get('kind')==native_kind and selected.get('native_client_finished')and selected.get('no_custom_model_api_bridge')
                    assert 'http_status'not in selected
                    proof=selected['native_output_proof'];offset=proof['native_json_event_offset'];end=offset+proof['native_result_event_index']+1
                    group=cursor_native[offset:end];declaration=group[0]
                    assert declaration.get('type')=='native_agy_rpc_request'and declaration.get('method')=='session/prompt'
                    assert declaration['id']==proof['native_request_id']and declaration['params']['sessionId']==proof['native_session_id']==selected['native_session_id']
                    declaration_images_match(declaration,selected)
                    feedback={'planning_round':n,'remaining':cap-n+1,'success':e['before']['success'],'total_actions':e['before']['actionCount'],'collisions':e['before']['collisions']+invalid,'previous_sequence':[]if n==1 else[{'action':step['action'],'result':step['result']}for step in progress['events'][n-2]['steps']],'previous_invalid':False if n==1 else progress['events'][n-2]['invalid']}
                    request_text=planning_request(cfg,n,cap,len(e['input_frames']),feedback)
                    assert declaration['prompt_sha256']==hashlib.sha256(request_text.encode()).hexdigest(),(case.name,n,'native AGY public prompt hash')
                    content=[{'type':'text','text':request_text}]+[{'type':'image','mimeType':'image/png','data':base64.b64encode((cache/name).read_bytes()).decode()}for name in sent]
                    request={'jsonrpc':'2.0','id':declaration['id'],'method':'session/prompt','params':{'sessionId':selected['native_session_id'],'prompt':content}}
                    encoded=json.dumps(request,ensure_ascii=False,separators=(',',':')).encode()
                    assert declaration['request_sha256']==hashlib.sha256(encoded).hexdigest(),(case.name,n,'serialized native AGY image request hash')
                    assert selected['native_request_bytes']==len(encoded)
                    text,rebuilt,native_result=completed_agy_reply(group,request_id=proof['native_request_id'])
                    charged_denial_ids.update(ensure_fresh_denied_tools(rebuilt,charged_denial_ids))
                    assert text==e['response']and result_sha(native_result)==selected['native_result_sha256']
                    for key in('native_request_id','native_session_id','native_chunk_event_indices','native_result_event_index','native_stop_reason','selected_public_text_sha256'):
                        assert rebuilt[key]==proof[key],(case.name,n,'native AGY completion proof',key)
                    assert rebuilt.get('native_verified_denied_tool_attempts',[])==proof.get('native_verified_denied_tool_attempts',[]),(case.name,n,'native AGY verified tool-denial proof')
                    assert not rebuilt.get('native_invalid_plan_reason')and not proof.get('native_invalid_plan_reason'),(case.name,n,'empty native AGY reply must not be scored')
                    native_denied_tool_count+=len(rebuilt.get('native_verified_denied_tool_attempts',[]))
                    assert proof['native_stop_reason']=='end_turn'
                if selected and selected.get('upstream_session_key'):
                    invalidated=json.loads((case/'invalidated-wire-indices.json').read_text()).get('indices',[])if(case/'invalidated-wire-indices.json').exists()else[]
                    assert selected['index']not in invalidated and[i['sha256']for i in selected['images']]==required
                    source=case/selected['private_response_file']
                    with gzip.open(source,'rb')as f:provider=metadata_response(f.read())
                    assert not provider['response_error'],(case.name,n,'explicit provider error')
                    assert provider['terminal']or e.get('closed_json_stream_recovery'),(case.name,n,'completion evidence')
                evidence=e.get('closed_json_stream_recovery')
                if evidence:
                    assert cfg.get('recover_closed_json_streams')and evidence['provider_terminal_received']is False
                    original=next(c for c in calls if c['index']==evidence['source_wire_index'])
                    source=case/original['private_response_file']
                    assert hashlib.sha256(source.read_bytes()).hexdigest()==evidence['private_response_sha256']
                    with gzip.open(source,'rb')as f:provider=metadata_response(f.read())
                    assert not provider['terminal']and not provider['response_error']
                    assert provider['text'].strip()==e['response'].strip()and complete_json_document(provider['text'])
                    assert hashlib.sha256(provider['text'].encode()).hexdigest()==evidence['public_text_sha256']
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
        metadata=intervention_metadata(interventions)
        native_tool_notes={}
        if agy_native:
            from .agy_recovery import native_agy_operation_notes
            operations=native_agy_operation_notes(cursor_native)
            assert result.get('denied_tool_decisions',0)==0,'Empty tool-denied replies cannot create scored decisions'
            native_tool_notes={'native_prompt_tool_policy_verified':True,'native_verified_denied_tool_attempts':native_denied_tool_count,'native_unscored_tool_attempt_windows':len(operations['prompt_windows_with_tool_attempts']),'native_historical_tool_replay_notifications':len(operations['historical_tool_replay_notifications'])}
        if metadata:
            assert result.get('clean_protocol_comparison_eligible')is False
            assert result.get('host_intervention_actions')==metadata['host_intervention_actions']
            assert result.get('host_intervention_collisions')==metadata['host_intervention_collisions']
        checks.append({'case':case.name,'status':result['status'],'replayed_planning_rounds':len(progress['events']),'ordered_image_rounds_verified':len(progress['events']),'pruned_rounds':sum(bool(e.get('image_trim_events'))for e in progress['events']),'closed_json_eof_recovered_rounds':sum(bool(e.get('closed_json_stream_recovery'))for e in progress['events']),'profile_backed_completion_rounds':sorted(profile_evidence),'native_agy_rounds_verified':len(progress['events'])if agy_native else 0,**metadata,**native_tool_notes})
    report={'all20_complete':len(checks)==20,'all_checked_invariants_passed':True,'cases':checks};dump(out,report);return report
