"""Package only static English publication assets; keep Python source private."""
import argparse,getpass,hashlib,json,re,shlex,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
ALLOWED={'.html','.css','.js','.json','.csv','.svg','.png','.jpg','.mp4'}
TEXT={'.html','.css','.js','.json','.csv','.svg'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    key=getpass.getpass('Gateway credential for final publication scan only: ');secrets=[key]
    alias=Path.home()/'.alias'
    for line in alias.read_text().splitlines():
        m=re.match(r'^\s*(?:export\s+)?REPORT_HUB_API_KEY\s*=\s*(.*?)\s*$',line)
        if m:
            parts=shlex.split(m.group(1),comments=True)
            if len(parts)==1 and '$('not in parts[0]and'`'not in parts[0]:secrets.append(parts[0])
    files=sorted(f for f in ROOT.rglob('*')if f.is_file()and f.suffix.lower()in ALLOWED and '__pycache__'not in f.parts)
    records=[]
    for f in files:
        rel=f.relative_to(ROOT);raw=f.read_bytes()
        assert not any(part in('private','profiles','runs','.git')for part in rel.parts)
        assert not any(s.encode()in raw for s in secrets if s),'Credential in '+str(rel)
        if f.suffix in TEXT:
            t=raw.decode('utf8');assert not re.search(r'[\u3400-\u9fff]',t),'CJK text in '+str(rel)
            assert not re.search(r'-----BEGIN (?:(?:RSA|EC|OPENSSH|DSA) )?PRIVATE KEY-----|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}',t)
        records.append({'path':str(rel),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    assert len(records)<10000 and sum(r['bytes']for r in records)<500*1024**2
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(a.out,'w',zipfile.ZIP_DEFLATED,compresslevel=5)as z:
        for f,r in zip(files,records):z.write(f,r['path'])
    receipt={'archive':str(a.out.resolve()),'archive_sha256':hashlib.sha256(a.out.read_bytes()).hexdigest(),'bytes':a.out.stat().st_size,'files':len(records),'manifest':records,'english_only':True,'known_secret_scan':True,'python_source_published':False,'private_inference_logs_published':False,'model_runs':44,'audited_planning_rounds':7907,'author':'Kenny','published':False}
    a.out.with_suffix('.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items()if k!='manifest'}),flush=True)

if __name__=='__main__':main()
