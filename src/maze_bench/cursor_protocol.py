"""Read authoritative public Cursor CLI turn output, retaining retry evidence."""
import hashlib


def public_text(event):
    return ''.join(block.get('text','')for block in event.get('message',{}).get('content',[])if block.get('type')=='text')


def completed_cursor_reply(events):
    result_index=next((i for i in range(len(events)-1,-1,-1)if events[i].get('type')=='result'),None)
    if result_index is None:raise RuntimeError('Cursor turn has no terminal result')
    result=events[result_index]
    if result.get('is_error')or result.get('subtype')!='success':raise RuntimeError('Cursor client error: '+str(result.get('result')or result.get('error')or result.get('subtype'))[:600])
    snapshots=[(i,e)for i,e in enumerate(events[:result_index])if e.get('type')=='assistant'and'timestamp_ms'not in e]
    if snapshots:
        selected,event=snapshots[-1];text=public_text(event);source='final_buffered_assistant'
    else:selected=result_index;text=result.get('result','');source='terminal_result'
    raw=result.get('result','')
    proof={'source':source,'selected_event_index':selected,'native_result_event_index':result_index,
           'native_result_aggregation_differs':raw!=text,'raw_result_sha256':hashlib.sha256(raw.encode()).hexdigest(),
           'selected_public_text_sha256':hashlib.sha256(text.encode()).hexdigest(),
           'retry_events':sum(e.get('type')=='retry'for e in events[:result_index]),'native_result_subtype':'success'}
    return text,proof,result
