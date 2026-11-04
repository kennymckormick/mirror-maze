import hashlib,json,re,uuid
from pathlib import Path

def dump(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(path)

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def parse_plan(text):
    text=(text or '').strip()
    match=re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```',text)
    if match:text=match.group(1)
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('duplicate JSON key')
            out[k]=v
        return out
    try:
        p=json.loads(text,object_pairs_hook=pairs)
        if isinstance(p,list):p={'actions':p,'plan':''}
        if not isinstance(p,dict)or not isinstance(p.get('actions'),list)or not p['actions']or any(not isinstance(a,str)or a not in ('left','right','forward')for a in p['actions']):return None,'illegal_action_sequence'
        return p,None
    except (ValueError,TypeError):return None,'invalid_json'

def endpoint(base,path):
    base=base.rstrip('/')
    if base.endswith('/v1'):base=base[:-3]
    return base+path

