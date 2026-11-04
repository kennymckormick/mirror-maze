"""Package an explicit source allowlist, never runs, private streams or secrets."""
import argparse,getpass,hashlib,json,os,zipfile
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();root=Path(__file__).resolve().parents[1]
key=os.environ.get('MIRROR_MAZE_API_KEY')or getpass.getpass('Provided gateway key for secret scan only: ')
files=[root/n for n in ['pyproject.toml','README.md','CHANGELOG.md','Dockerfile','.dockerignore','.gitignore','.env.example','environment-manifest.json']]
for name in ['src','examples','docs','tests','experiments']:
    files.extend(f for f in(root/name).rglob('*')if f.is_file()and'__pycache__'not in f.parts and not any(part.endswith('.egg-info') for part in f.parts) and not f.name.endswith(('.pyc','.tmp')))
files.extend(root/'tools'/name for name in ['repair_legacy.py','resume_interrupted.py','recover_native_checkpoint.py','package_project.py']);manifest=[]
for f in sorted(set(files)):
    raw=f.read_bytes()
    if key and key.encode()in raw:raise RuntimeError('Credential found in '+str(f.relative_to(root)))
    manifest.append({'path':str(f.relative_to(root)),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
a.out.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(a.out,'w',zipfile.ZIP_DEFLATED,compresslevel=6)as z:
    for record in manifest:z.write(root/record['path'],'mirror-maze-benchmark/'+record['path'])
    z.writestr('mirror-maze-benchmark/MANIFEST.json',json.dumps({'version':'0.4.0','files':manifest,'credentials_included':False,'raw_native_or_api_streams_included':False},ensure_ascii=False,indent=2))
print(json.dumps({'archive':str(a.out.resolve()),'files':len(manifest),'bytes':a.out.stat().st_size,'sha256':hashlib.sha256(a.out.read_bytes()).hexdigest(),'secret_scan_passed':True}),flush=True)
