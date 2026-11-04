"""Resume saved API cases without resampling completed maze outcomes."""
import argparse,concurrent.futures,getpass,json,os,time
from pathlib import Path
from maze_bench.common import dump
from maze_bench.runner import run

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--workers',type=int,default=4);p.add_argument('--timeout',type=int,default=3600);p.add_argument('--only',action='append');a=p.parse_args()
    key=os.environ.get('MIRROR_MAZE_API_KEY')or getpass.getpass('API key (hidden): ')
    groups={}
    for case in sorted(a.out.iterdir()):
        if not(case/'result.json').exists():continue
        if a.only and case.name not in a.only:continue
        result=json.loads((case/'result.json').read_text())
        if result['status']in('success','planning_cap'):continue
        if result['status']!='infrastructure_error':raise RuntimeError('Case must be stopped at a saved boundary: '+case.name)
        cfg=json.loads((case/'config.json').read_text())
        if cfg.get('harness'):raise RuntimeError('Use the native repair workflow for '+case.name)
        cfg.update(request_timeout=a.timeout,harness_timeout=a.timeout,planning_timeout=a.timeout)
        cfg.setdefault('max_output_budget',65536 if cfg.get('model_key')=='gemini'else 131072)
        groups.setdefault(cfg['model_key'],[]).append((cfg,case))
    jobs=[]
    while any(groups.values()):
        for model in groups:
            if groups.get(model):jobs.append(groups[model].pop(0))
    dump(a.out/('timeout-recovery-'+str(time.time_ns())+'.json'),{'started_at':time.time(),'timeout':a.timeout,'workers':a.workers,'cases':[case.name for _,case in jobs],'policy':'Preserve source, inference parameters, public history, maze state, accepted planning budget and completed outcomes'})
    def job(cfg,case):
        current=json.loads((case/'result.json').read_text())
        if current['status']in('success','planning_cap'):return current
        if current['status']=='running':return{'case_id':case.name,'status':'already_running_elsewhere'}
        return run(cfg,key,case,cap=cfg.get('max_planning',200),resume=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers)as pool:
        futures={pool.submit(job,cfg,case):case for cfg,case in jobs}
        for future in concurrent.futures.as_completed(futures):
            try:result=future.result()
            except Exception as exc:result={'case_id':futures[future].name,'status':'executor_error','error':str(exc).replace(key,'[redacted]')}
            print('RECOVERY_RESULT '+json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
