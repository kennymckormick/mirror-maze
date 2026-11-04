"""Fetch the ignored public presentation assets after cloning the private repo."""
import argparse,concurrent.futures,hashlib,json,urllib.parse,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def main():
    p=argparse.ArgumentParser();p.add_argument('--base-url');p.add_argument('--include-videos',action='store_true');p.add_argument('--workers',type=int,default=6);a=p.parse_args()
    manifest=json.loads((ROOT/'data/asset-manifest.json').read_text());base=(a.base_url or manifest['base_url']).rstrip('/')+'/'
    def get(record):
        rel=Path(record['path']);assert not rel.is_absolute()and'..'not in rel.parts and rel.parts[0]in('assets','videos')
        target=ROOT/rel
        if target.exists()and hashlib.sha256(target.read_bytes()).hexdigest()==record['sha256']:return
        reader=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with reader.open(base+urllib.parse.quote(str(rel),safe='/'),timeout=120)as r:raw=r.read()
        assert len(raw)==record['bytes']and hashlib.sha256(raw).hexdigest()==record['sha256'],'Asset hash mismatch: '+str(rel)
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    chosen=[r for r in manifest['files']if a.include_videos or not r['path'].startswith('videos/')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers)as pool:list(pool.map(get,chosen))
    print(json.dumps({'verified_assets':len(chosen),'videos':a.include_videos}))

if __name__=='__main__':main()
