import argparse,concurrent.futures,getpass,hashlib,json,os,shutil,sys,time
from pathlib import Path
from .common import dump
from .runner import run
from .settings import geometry

def main():
    if len(sys.argv)>1 and sys.argv[1]=='play':
        from .play import main as play_main
        return play_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='report':
        from .report import main as report_main
        return report_main(sys.argv[2:])
    p=argparse.ArgumentParser(description='Mirror Maze: real API or optional native CLI evaluation')
    p.add_argument('command',choices=['run','preflight']);p.add_argument('--config',type=Path,default=Path(__file__).with_name('defaults.json'))
    p.add_argument('--base-url',required=True);p.add_argument('--api-key-env',default='MIRROR_MAZE_API_KEY')
    p.add_argument('--mode',choices=['api','harness'],default='api');p.add_argument('--harness',choices=['claude','codex','agy'])
    p.add_argument('--harness-executable');p.add_argument('--model',action='append');p.add_argument('--out',type=Path,default=Path('runs')/time.strftime('%Y%m%d-%H%M%S'))
    p.add_argument('--cache-dir',type=Path);p.add_argument('--workers',type=int,default=5);p.add_argument('--resume',action='store_true')
    p.add_argument('--max-planning',type=int);p.add_argument('--probe-font')
    a=p.parse_args();a.mode='harness'if a.harness else a.mode;spec=json.loads(a.config.read_text());key=os.environ.get(a.api_key_env)or getpass.getpass('API key (hidden): ')
    if not key:raise SystemExit('API key is required')
    geometry(spec.get('defaults',{}))
    pinned={'reflections':10,'resolution':[1280,720],'image_history':'last_batch'}
    for field,value in pinned.items():
        if spec.get('defaults',{}).get(field,value)!=value:raise SystemExit('This benchmark pins '+field+'='+str(value)+'; use the Python environment API for other settings')
    cap=a.max_planning if a.max_planning is not None else spec.get('defaults',{}).get('max_planning',200)
    if isinstance(cap,bool) or not isinstance(cap,int) or not 1<=cap<=500:raise SystemExit('Planning cap must be between 1 and 500')
    if a.base_url.split('://',1)[-1].split('/',1)[0].find('@')>=0 or '?'in a.base_url:raise SystemExit('Supply credentials through the key environment variable, not the base URL')
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);cache=(a.cache_dir or out/'image-cache').resolve();cache.mkdir(parents=True,exist_ok=True)
    models=[m for m in spec['models']if not a.model or m['model_key']in a.model or m['source_alias']in a.model or m['model_label']in a.model]
    if not models:raise SystemExit('No matching models in config')
    run_id=hashlib.sha256(str(out).encode()).hexdigest()[:16];jobs=[]
    grid=[('square','VISION-PREFLIGHT')]if a.command=='preflight'else[(t,s)for t in spec.get('topologies',['square','hex'])for s in spec.get('seeds',['MIRROR-042','MIRROR-043'])]
    for topology,seed in grid:
        for model in models:
            name=model['model_key']if a.command=='preflight'else model['model_key']+'-'+topology+'-'+seed
            cfg={**spec.get('defaults',{}),**model,'case_id':a.mode+'-'+name,'run_id':run_id,'api_base':a.base_url,'topology':topology,'seed':seed,'cache_dir':str(cache),'direct_observations':True}
            cfg['harness']=(a.harness or model.get('harness'))if a.mode=='harness'else None
            if a.harness_executable:cfg['harness_executable']=a.harness_executable
            cfg['profile_root']=str(Path.home()/'.local/share/mirror-maze-benchmark/profiles'/run_id/name)
            if cfg.get('harness'):cfg['model_label']=model.get('native_model',model['model_label'])
            if a.probe_font:cfg['probe_font']=a.probe_font
            if a.max_planning:cfg['max_planning']=a.max_planning
            jobs.append((cfg,out/name))
    dump(out/'batch-config.json',{'spec':spec,'api_base':a.base_url,'mode':a.mode,'workers':a.workers,'started_at':time.time(),'case_ids':[path.name for _,path in jobs]})
    def job(item):
        cfg,case=item;existing=case/'result.json'
        if (case/'progress.json').exists() and not existing.exists():raise RuntimeError('A checkpoint exists without a final/interruption record; inspect and recover the saved native/public reply before resuming: '+case.name)
        if existing.exists():
            old=json.loads(existing.read_text())
            if old.get('status')in('success','planning_cap'):return old
            if not a.resume:raise RuntimeError('Interrupted case exists; use --resume or a new output directory: '+case.name)
        return run(cfg,key,case,cap=cfg.get('max_planning',200),vision_probe=a.command=='preflight',resume=existing.exists())
    failed=False
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers)as pool:
        futures={pool.submit(job,j):j for j in jobs}
        for future in concurrent.futures.as_completed(futures):
            try:result=future.result()
            except Exception as exc:result={'case_id':futures[future][1].name,'status':'executor_error','error':str(exc).replace(key,'[redacted]')}
            if result.get('status')in('executor_error','infrastructure_error','vision_failed'):failed=True
            print('CASE_RESULT '+json.dumps(result,ensure_ascii=False),flush=True)

    if failed:raise SystemExit(1)

if __name__=='__main__':main()
