"""Resume only interrupted V2 cases; original artifacts remain untouched."""
import argparse,concurrent.futures,getpass,hashlib,json,os,shutil,time
from pathlib import Path
from maze_bench.common import dump,sha
from maze_bench.runner import run

def main():
    p=argparse.ArgumentParser();p.add_argument('--legacy-root',type=Path,required=True);p.add_argument('--profile-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--base-url',required=True);p.add_argument('--claude-executable',required=True);a=p.parse_args()
    key=os.environ.get('MIRROR_MAZE_API_KEY')or getpass.getpass('API key (hidden): ');a.out.mkdir(parents=True,exist_ok=True)
    jobs=[]
    for original in sorted((a.legacy_root/'final-runs').iterdir()):
        result=json.loads((original/'result.json').read_text())
        if result['status']!='infrastructure_error':continue
        case=a.out/original.name
        if not case.exists():
            case.mkdir(parents=True)
            for filename in ['config.json','progress.json','episode.json','result.json','wire.json','native.jsonl.gz']:
                shutil.copy2(original/filename,case/filename)
            dump(case/'lineage.json',{'parent_report':'http://vlmeval.byted.org/reports/view/Ta1425szHmmw/','parent_case':original.name,'parent_planning':result['planning_rounds'],'parent_files':{f:sha(original/f)for f in ['progress.json','episode.json','result.json']},'policy':'round-prune-v3; latest view pinned; no fresh maze or budget reset'})
        cfg=json.loads((case/'config.json').read_text());wire=json.loads((case/'wire.json').read_text())
        cfg.update(api_base=a.base_url,cache_dir=str((a.legacy_root/'image-cache').resolve()),profile_root=str((a.profile_root/cfg['case_id']).resolve()),workspace=str((original/'workspace').resolve()),harness_executable=a.claude_executable,max_body_bytes=120_000_000,request_attempts=5,request_timeout=3600,harness_timeout=3600,planning_timeout=3600,prune_previous_timeout=not any(c.get('http_status')==413 for c in wire)and sum(c.get('http_status')==502 for c in wire)>=2)
        jobs.append((cfg,case))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:
        def job(item):
            cfg,case=item
            for attempt in range(3):
                old=json.loads((case/'result.json').read_text())
                if old['status']in('success','planning_cap'):return old
                result=run(cfg,key,case,cap=cfg.get('max_planning',200),resume=True)
                if result['status']!='infrastructure_error':return result
                time.sleep(5)
            return result
        for result in pool.map(job,jobs):print('REPAIR_RESULT '+json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
