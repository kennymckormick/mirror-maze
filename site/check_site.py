"""Validate transformed trajectories and the English static publication."""
import csv,hashlib,json,math,re
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parent
def read(path):return json.loads((ROOT/path).read_text())

def main():
    study=read('data/study.json');assert len(study['cases'])==44
    cases=rounds=steps=0
    for c in study['cases']:
        e=read(c['episode']);m=read(next(x['file']for x in study['maps']if x['id']==e['map']));h=m['headings'];cell=m['initial']['cell'];heading=m['initial']['heading'];seen={cell};actions=hits=0
        for s in e['steps']:
            a,nxt,new_heading,image,hit,round_no=s
            if a==0:assert nxt==cell and new_heading==(heading-1)%h and not hit
            elif a==1:assert nxt==cell and new_heading==(heading+1)%h and not hit
            elif a==2:
                angle=(math.pi/6 if h==6 else 0)+heading*2*math.pi/h;v=(math.cos(angle),math.sin(angle));edge=max(m['cells'][cell]['edges'],key=lambda x:x['normal'][0]*v[0]+x['normal'][1]*v[1]);assert new_heading==heading
                assert nxt==(edge['neighbor']if edge['open']else cell)and bool(hit)==(not edge['open'])
            else:assert a==-1 and nxt==cell and new_heading==heading and hit
            assert 0<=image<len(e['images'])and(ROOT/e['images'][image]).is_file()
            cell,heading=nxt,new_heading;seen.add(cell);actions+=a>=0;hits+=bool(hit);steps+=1
        assert actions==c['actions']and hits==c['collisions']and len(seen)==c['visited']
        assert len(e['rounds'])==c['planning']<=c['cap']
        assert (cell==m['goal'])==(c['status']=='success')
        if c['status']=='planning_cap':assert c['planning']==c['cap']
        cursor=0
        for r in e['rounds']:
            assert r['begin']==cursor and r['end']>cursor
            assert all(s[5]==r['round']for s in e['steps'][r['begin']:r['end']]);cursor=r['end']
        assert cursor==len(e['steps']);cases+=1;rounds+=c['planning']
    assert rounds==7907
    for path in ROOT.rglob('*'):
        if not path.is_file()or path.suffix not in('.html','.css','.js','.json','.csv','.md','.py','.svg'):continue
        text=path.read_text();assert not re.search(r'[\u3400-\u9fff]',text),'Non-English CJK text in '+str(path.relative_to(ROOT))
        assert not re.search(r'-----BEGIN (?:(?:RSA|EC|OPENSSH|DSA) )?PRIVATE KEY-----|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}',text)
    heavy=[]
    for folder in('assets/observations','videos'):
        for f in sorted((ROOT/folder).rglob('*')):
            if not f.is_file():continue
            if f.suffix=='.jpg':
                with Image.open(f)as im:assert im.size==(1280,720)
            heavy.append({'path':str(f.relative_to(ROOT)),'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
    (ROOT/'data/asset-manifest.json').write_text(json.dumps({'base_url':'http://vlmeval.byted.org/reports/view/rzAw-KSgopFj/','scope':'Compressed replay observations and English videos. These are presentation assets, not original lossless model inputs.','files':heavy},separators=(',',':'))+'\n')
    report={'cases':cases,'planning_rounds':rounds,'recorded_action_entries':steps,'motor_actions':sum(c['actions']for c in study['cases']),'all_transformed_rules_match':True,'english_only':True,'presentation_assets':len(heavy),'model_results_changed':False}
    (ROOT/'data/site-validation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
