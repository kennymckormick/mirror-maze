"""Prepare English presentation data from frozen, sanitized evaluation exports.

No inference calls. The source study and its scores are never modified.
"""
import argparse,concurrent.futures,csv,hashlib,json,math,shutil
from pathlib import Path
from PIL import Image

NAMES={'deepseek':'DeepSeek-V4.1-Flash','kimi':'Kimi K3.1 Pro','gpt':'GPT-6.1 Sol','gemini':'Gemini 4 Argon','seed':'Seed 2.1 Pro','astra':'GPT-6 Astra'}
ACTION={'left':0,'right':1,'forward':2,None:-1}

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n')

def main():
    p=argparse.ArgumentParser();p.add_argument('--evidence-root',type=Path,required=True);p.add_argument('--out',type=Path,default=Path(__file__).resolve().parent);a=p.parse_args()
    source=a.evidence_root.resolve();out=a.out.resolve();data_dir=out/'data';data_dir.mkdir(parents=True,exist_ok=True)
    original=json.loads((source/'artifacts/final-study/data.json').read_text());medium=json.loads((source/'artifacts/press-kit/astra/data.json').read_text())
    assert all(m['complete']and len(m['cases'])==20 for m in original['modes'].values())and medium['complete']
    analysis=json.loads((source/'artifacts/english-insights/analysis.json').read_text());metrics={c['case_id']:c for c in analysis['cases']}
    summary=[];maps={};pictures={};sample_poses={};source_index={}
    def image(path):
        path=path.resolve();name=path.name;target='assets/observations/'+name
        if target not in pictures:pictures[target]=path
        return target
    def add_map(key,m,topology,difficulty):
        if key in maps:return
        cells=[{k:c[k]for k in('id','center','vertices','edges')}for c in m['maze']]
        maps[key]={'id':key,'topology':topology,'difficulty':difficulty,'cells':cells,'initial':{'cell':m['initial']['cell'],'heading':m['initial']['heading']},'goal':m['goal'],'headings':4 if topology=='square'else 6,'oracleActions':m.get('actions',m.get('shortest_actions'))}
        write(data_dir/'maps'/(key.replace('/','-')+'.json'),maps[key])
    def add_case(c,arm,m,image_root,medium_case=False):
        topology=c['topology'];difficulty='medium'if medium_case else'easy';map_name=c.get('map',c['id']);map_id=difficulty+'/'+map_name;model='astra'if medium_case else c['model'];ident=arm+'/'+c['id'];add_map(map_id,m,topology,difficulty)
        images=[];image_ids={}
        def picture(path):
            target=image(path)
            if target not in image_ids:image_ids[target]=len(images);images.append(target)
            return image_ids[target]
        initial_path=image_root/c['initialImage']if medium_case else image_root/c['_images'][c['initialImage']]
        first=picture(initial_path);steps=[];rounds=[];seen={m['initial']['cell']};actions=hits=0;curve=[{'round':0,'visited':1,'actions':0,'collisions':0,'newCells':1}]
        pose_index=sample_poses.setdefault(map_id,{})
        pose_index.setdefault((m['initial']['cell'],m['initial']['heading']),images[first])
        for r in c['rounds']:
            previous=len(seen);begin=len(steps)
            for raw in c['steps'][r['begin']:r['end']]:
                if medium_case:
                    action,cell,heading,hit=raw['action'],raw['cell'],raw['heading'],raw['collision'];frame=image_root/raw['image']
                else:action,cell,heading,ii,hit,reason,round_no=raw;frame=image_root/c['_images'][ii]
                picture_id=picture(frame);steps.append([ACTION[action],cell,heading,picture_id,int(hit),r['round']]);seen.add(cell);actions+=action is not None;hits+=bool(hit);pose_index.setdefault((cell,heading),images[picture_id])
            rounds.append({'round':r['round'],'plan':r['plan'],'submitted':r['submitted'],'begin':begin,'end':len(steps),'invalid':r.get('invalid',False),'inputImages':r['inputImages'],'sentImages':r.get('keptImages',r.get('sentImages',r['inputImages']))})
            curve.append({'round':r['round'],'visited':len(seen),'actions':actions,'collisions':hits,'newCells':len(seen)-previous})
        assert actions==c['actions']and hits==c['collisions']and len(seen)==c['visited'],ident
        record={'id':ident,'model':model,'name':NAMES[model],'arm':arm,'difficulty':difficulty,'topology':topology,'seed':c['seed'],'map':map_id,'cells':len(m['maze']),'status':c['status'],'planning':c['planning'],'cap':500 if medium_case else 200,'actions':actions,'collisions':hits,'visited':len(seen),'sourceAlias':c.get('source',c.get('source_alias')),'harness':c.get('harness','codex')if arm!='api'else None,'initialImage':first,'images':images,'rounds':rounds,'steps':steps}
        file='data/episodes/'+ident.replace('/','-')+'.json';write(out/file,record)
        small={k:record[k]for k in('id','model','name','arm','difficulty','topology','seed','map','cells','status','planning','cap','actions','collisions','visited','sourceAlias','harness')};small.update(episode=file,curve=curve,initialImage=images[first]);summary.append(small)
        source_index[ident]={'public_state_only':True,'source_artifact':('astra-medium-codex'if medium_case else 'frozen-easy-study'),'score_unchanged':True}
    for arm,m in original['modes'].items():
        for c in m['cases']:
            c=dict(c,_images=m['images']);add_case(c,arm,m['maps'][c['map']],source/'artifacts/final-study')
    for c in medium['cases']:
        c=dict(c,model='astra',map=c['id'],harness='codex');add_case(c,'astra-codex',medium['maps'][c['id']],source/'artifacts/press-kit/astra',True)
    # One-cell visual puzzle, with the actual forward edge checked in geometry.
    key='easy/square-MIRROR-043';m=maps[key];poses=sample_poses[key];candidates=[]
    for cell in [m['initial']['cell']]+[c['id']for c in m['cells']]:
        pair={}
        for heading in range(m['headings']):
            if(cell,heading)not in poses:continue
            angle=heading*math.pi/2;v=(math.cos(angle),math.sin(angle));edge=max(m['cells'][cell]['edges'],key=lambda e:e['normal'][0]*v[0]+e['normal'][1]*v[1]);pair.setdefault(edge['open'],{'cell':cell,'heading':heading,'open':edge['open'],'image':poses[cell,heading]})
        if True in pair and False in pair:candidates=[pair[False],pair[True]];break
    assert len(candidates)==2
    write(data_dir/'hero.json',{'map':key,'views':candidates,'source':'Recorded first-person states; physical openness determined from canonical edges. No new model inference.'})
    write(data_dir/'study.json',{'schema':'mirror-maze-english/v1','author':'Kenny','models':NAMES,'cases':summary,'maps':[{'id':k,'file':'data/maps/'+k.replace('/','-')+'.json','cells':len(v['cells']),'topology':v['topology'],'difficulty':v['difficulty']}for k,v in maps.items()],'groups':{'harness':{'cases':20,'success':7,'cap':200,'replayedRounds':3418},'api':{'cases':20,'success':6,'cap':200,'replayedRounds':3511},'astra-codex':{'cases':4,'success':3,'cap':500,'replayedRounds':978}},'auditedPlanningRounds':7907,'frozenModelRuns':44,'initialImageQuality':'Models received1280x720 PNG. This website uses smaller JPEG previews.','sourceIndex':source_index})
    # Export only aggregate/public trajectory analysis, never native transcripts.
    for name in('analysis.json','random-policy-protocol.json','random-policy-results.json'):
        shutil.copy2(source/'artifacts/english-insights'/name,data_dir/name)
    stress=json.loads((data_dir/'random-policy-results.json').read_text())
    write(data_dir/'stress-summary.json',{k:stress[k]for k in('aggregates','map_set_events','protocol_sha256')})
    human=json.loads((source/'artifacts/human-reference.json').read_text());write(data_dir/'ideal-observer.json',human)
    with(data_dir/'cases.csv').open('w',newline='')as f:
        fields=['id','name','arm','difficulty','topology','seed','cells','status','planning','cap','actions','collisions','visited','sourceAlias','harness'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:c[k]for k in fields}for c in summary)
    def convert(item):
        target,original=item;path=out/target;path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            with Image.open(original)as im:im.convert('RGB').save(path,quality=50,optimize=True)
        return path.stat().st_size
    with concurrent.futures.ThreadPoolExecutor(max_workers=4)as pool:sizes=list(pool.map(convert,pictures.items()))
    write(data_dir/'evidence-manifest.json',{'sources':'Frozen easy40 plus medium4 public exports; independent original-PNG input audits retained.','publicPreviewImages':len(pictures),'previewBytes':sum(sizes),'previewEncoding':'JPEG quality50; original input720p PNG unchanged','modelRuns':44,'replayedPlanningRounds':7907})
    print(json.dumps({'cases':len(summary),'maps':len(maps),'images':len(pictures),'previewMB':round(sum(sizes)/2**20,1),'out':str(out)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
