import gzip,json,time
from .common import dump

class API:
    """Plain public-message API dialogue; no native harness, tools or hidden state."""
    def __init__(self,cfg,case,broker):
        self.cfg=cfg;self.case=case;self.broker=broker;self.turns=[];self.thread_id=None
        self.history=json.loads((case/'dialogue.json').read_text())if(case/'dialogue.json').exists()else[]
    def ask(self,text,timeout=None):
        start=time.monotonic();history=self.history+[{'role':'user','content':text}]
        kind=self.cfg.get('api_protocol','chat');path={'messages':'/v1/messages','responses':'/v1/responses','chat':'/v1/chat/completions'}[kind]
        payload={'model':self.cfg['source_alias'],'stream':True,**self.cfg.get('request_params',{})}
        if kind=='responses':payload['input']=[{'type':'message',**m}for m in history];payload['store']=False
        else:payload['messages']=history
        if kind=='chat':payload['stream_options']={'include_usage':True}
        if kind=='messages':payload.setdefault('max_tokens',32000)
        _,data,meta=self.broker.transport.request(payload,path,True)
        # Private provider stream permits parser audits without resampling answers.
        private=self.case/'private';private.mkdir(exist_ok=True)
        with gzip.open(private/f'response-{len(self.broker.calls):06d}.sse.gz','wb')as f:f.write(data)
        raw=meta['text']
        if not raw:raise RuntimeError('API returned no public text')
        self.history=history+[{'role':'assistant','content':raw}];dump(self.case/'dialogue.json',self.history)
        self.turns.append({'seconds':time.monotonic()-start});return raw
    def close(self):pass

