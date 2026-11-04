"""Five-model, planning-round synchronized videos from public evaluation data."""
import argparse,functools,html,json,math,shutil,subprocess,textwrap
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

COLORS={'deepseek':'#7eb4ed','kimi':'#c4a0f3','gpt':'#c4eda0','gemini':'#efb879','seed':'#78d8cd'}
SHORT={'deepseek':'DeepSeek V4.1 Flash*','kimi':'Kimi K3.1 Pro','gpt':'GPT-6.1 Sol','gemini':'Gemini 4 Argon','seed':'Seed 2.1 Pro'}
BG='#0d151a';PANEL='#162229';WHITE='#e5ede9';MUTED='#94aab1'

def font_path(explicit=None):
    if explicit:return explicit
    for p in ['/System/Library/Fonts/Supplemental/Arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf']:
        if Path(p).exists():return p
    raise RuntimeError('Install a TrueType font (e.g. fonts-dejavu-core) or specify --font')

class Film:
    def __init__(self,data,assets,font=None,title='Harness'):
        self.data=data;self.assets=Path(assets);self.fontfile=font_path(font);self.title=title
        self.fonts={n:ImageFont.truetype(self.fontfile,n)for n in [17,18,20,22,24,28,38,45]}
        self.W,self.H=1920,1080;self.col=376;self.models=data['modelOrder'];self.prepared={};self.cap=int(data.get('protocol',{}).get('maxPlanning',200))
        if len(self.models)!=5:raise ValueError('The synchronized video layout requires five models; use the interactive report for custom model counts')
    @functools.lru_cache(maxsize=512)
    def picture(self,index):
        with Image.open(self.assets/self.data['images'][index])as im:return im.convert('RGB').resize((360,203),Image.Resampling.LANCZOS)
    def text(self,d,xy,value,size=22,color=WHITE):d.text(xy,str(value),font=self.fonts[size],fill=color)
    def prepare(self,key):
        if key in self.prepared:return self.prepared[key]
        m=self.data['maps'][key];points=[p for c in m['maze']for p in c['vertices']];xmin=min(p[0]for p in points);xmax=max(p[0]for p in points);ymin=min(p[1]for p in points);ymax=max(p[1]for p in points)
        scale=min(320/(xmax-xmin),320/(ymax-ymin))
        def project(p):return(180+(p[0]-(xmin+xmax)/2)*scale,180+(p[1]-(ymin+ymax)/2)*scale)
        image=Image.new('RGB',(360,360),'#122129');d=ImageDraw.Draw(image)
        for c in m['maze']:
            for e in c['edges']:
                if not e['open']and(e['neighbor']<0 or e['neighbor']>c['id']):d.line([project(e['a']),project(e['b'])],fill='#728e98',width=2)
        for cell,color in[(m['initial']['cell'],'#efb879'),(m['goal'],'#80e0be')]:
            x,y=project(m['maze'][cell]['center']);d.ellipse((x-5,y-5,x+5,y+5),fill=color)
        cases=[]
        for model in self.models:
            c=next(c for c in self.data['cases']if c['model']==model and c['map']==key)
            coords=[project(m['maze'][m['initial']['cell']]['center'])]+[project(m['maze'][s[1]]['center'])for s in c['steps']]
            actions=[0];hits=[0]
            for s in c['steps']:actions.append(actions[-1]+(s[0]is not None));hits.append(hits[-1]+bool(s[4]))
            cases.append((c,coords,actions,hits))
        result=(m,image,project,cases);self.prepared[key]=result;return result
    def wrapped(self,d,xy,text,width=350,lines=4,size=18,color=MUTED):
        out=[];line=''
        for char in str(text):
            if char=='\n'or d.textlength(line+char,font=self.fonts[size])>width:
                out.append(line);line=''if char=='\n'else char
            else:line+=char
        if line:out.append(line)
        for i,value in enumerate(out[:lines]):self.text(d,(xy[0],xy[1]+i*(size+7)),value+('…'if i==lines-1 and len(out)>lines else''),size,color)
    def frame(self,key,round_no,phase=1,highlight=False):
        m,base,project,cases=self.prepare(key);im=Image.new('RGB',(self.W,self.H),BG);d=ImageDraw.Draw(im)
        topology='Square'if key.startswith('square')else'Hexagon';seed=key.split('-',1)[1]
        self.text(d,(35,25),'Mirror Maze: Five models. Who can escape?',38)
        self.text(d,(35,85),f'{self.title}  /  {topology} · {seed}  /  10 reflections · side {m.get("sideLength",m.get("geometry",{}).get("sideLength",7))} · cap {self.cap}',24)
        self.text(d,(35,123),'Synchronized by planning decision; long sequences are accelerated, not wall-clock inference time'+('  ·  selected decision excerpts'if highlight else''),20,MUTED)
        for j,(c,coords,actions,hits)in enumerate(cases):
            x=20+j*self.col;color=COLORS.get(c['model'],'#b7d5df');d.rounded_rectangle((x,170,x+366,1000),radius=9,fill=PANEL)
            self.text(d,(x+8,181),SHORT.get(c['model'],c['label']),22,color)
            r=next((r for r in c['rounds']if r['round']==round_no),None)
            if round_no<=0:index=0;plan=0
            elif r:index=r['begin']+min(r['end']-r['begin'],int(math.ceil((r['end']-r['begin'])*phase)));plan=round_no
            else:index=len(c['steps']);plan=min(round_no,c['planning'])
            index=min(index,len(c['steps']));step=c['steps'][index-1]if index else None;image_id=step[3]if step else c['initialImage']
            if image_id is not None:im.paste(self.picture(image_id),(x+3,225))
            map_image=base.copy();md=ImageDraw.Draw(map_image)
            if index:md.line(coords[:index+1],fill=color,width=3)
            cell=step[1]if step else m['initial']['cell'];heading=step[2]if step else m['initial']['heading'];angle=(math.pi/6 if c['topology']=='hex'else 0)+heading*(math.pi/3 if c['topology']=='hex'else math.pi/2);px,py=project(m['maze'][cell]['center'])
            triangle=[(px+math.cos(angle)*11,py+math.sin(angle)*11),(px+math.cos(angle+2.5)*9,py+math.sin(angle+2.5)*9),(px+math.cos(angle-2.5)*9,py+math.sin(angle-2.5)*9)];md.polygon(triangle,fill=color,outline='#07131a');im.paste(map_image,(x+3,445))
            ended=round_no>c['planning']or(round_no==c['planning']and phase>=1)
            status='Solved'if ended and c['success']else'Integration interruption'if ended and c['status']=='infrastructure_error'else str(c.get('planningCap',self.cap))+'-decision cap'if ended and c['status']=='planning_cap'else'Exploring'
            self.text(d,(x+12,811),status,24,'#c4eda0'if status=='Solved'else'#f0a69c'if status=='Integration interruption'else color)
            self.text(d,(x+12,851),f'Plan {plan} | Act {actions[index]} | Hit {hits[index]}',20)
            intention=r.get('plan','')if r else'Finished; final state retained.'if ended else'Initial first-person view'
            self.wrapped(d,(x+12,892),intention,340,3,18)
            if r and r.get('keptImages',0)<r.get('inputImages',0):self.text(d,(x+12,974),f'Images {r["inputImages"]} → {r["keptImages"]}; latest retained',17,'#efb879')
        d=ImageDraw.Draw(im);d.rectangle((35,1020,1885,1026),fill='#2c3d45');d.rectangle((35,1020,35+1850*min(round_no,self.cap)/self.cap,1026),fill='#c4eda0')
        self.text(d,(35,1040),'Maps are for human review only; models receive first-person images and action feedback.',20,MUTED);self.text(d,(1635,1040),f'Decision {round_no}/{self.cap}',20)
        return im

def render(data_path,assets,out,mode=None,preview=False,highlight=False,font=None,case_key=None):
    all_data=json.loads(Path(data_path).read_text());data=all_data['modes'][mode]if'modes'in all_data else all_data
    if case_key is not None and case_key not in data['maps']:raise ValueError('Unknown maze case: '+case_key)
    film=Film(data,assets,font,('Direct API'if mode=='api'else'Harness')+(' · original batch'if data.get('originalBatch')else''));out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    fps=12;cmd=[shutil.which('ffmpeg')or'ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s','1920x1080','-r',str(fps),'-i','-','-an','-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(out)]
    process=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    keys=[case_key]if case_key is not None else list(data['maps']);keys=keys[:1]if preview else keys
    try:
        for key in keys:
            rounds=sorted({0,1,*[round(film.cap*x)for x in [.025,.05,.1,.2,.4,.6,.8,1]]})if highlight else list(range(film.cap+1))
            if preview:rounds=[0,1,2,3,4,5,10,20]
            for n in rounds:
                frames=24 if highlight else 6
                for k in range(frames):process.stdin.write(film.frame(key,n,(k+1)/frames,highlight).tobytes())
            for _ in range(fps*2):process.stdin.write(film.frame(key,film.cap,1,highlight).tobytes())
            print('VIDEO_CASE_DONE '+key,flush=True)
    finally:process.stdin.close()
    if process.wait()!=0:raise RuntimeError('ffmpeg failed')
    print('VIDEO_SAVED '+str(out.resolve()),flush=True)

def render_cases(data_path,assets,out,mode=None,preview=False,highlight=False,font=None):
    all_data=json.loads(Path(data_path).read_text());data=all_data['modes'][mode]if'modes'in all_data else all_data
    out=Path(out);out.mkdir(parents=True,exist_ok=True);records=[]
    for key in data['maps']:
        if Path(key).name!=key or'\\'in key:raise ValueError('Unsafe case filename')
        path=out/(key+'.mp4');render(data_path,assets,path,mode,preview,highlight,font,case_key=key)
        records.append({'mode':mode or data.get('mode','harness'),'case':key,'path':str(path),'models':len(data['modelOrder']),'duration_seconds':float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],text=True).strip())})
    (out/'manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    return records

def video_section(records):
    """One player with an explicit case selector; no combined film in the UI."""
    if not records:return''
    def label(r):
        return('Direct API'if r['mode']=='api'else'Harness')+' · '+('Square'if r['case'].startswith('square-')else'Hexagon')+' · '+r['case'].split('-',1)[1]
    options=''.join('<option value="'+html.escape(r['path'],quote=True)+'">'+html.escape(label(r))+'</option>'for r in records)
    links=' · '.join('<a href="'+html.escape(r['path'],quote=True)+'" download>'+html.escape(label(r))+'</a>'for r in records)
    first=html.escape(records[0]['path'],quote=True)
    return'<div id="video-links"><h3 id="case-videos">Five-model synchronized replay by case</h3><p class="fine">Each video contains one case, with five models synchronized by planning decision. Its length depends on the decision budget. Maps are for human review only.</p><label for="video-case-select">Select video</label><select id="video-case-select">'+options+'</select><video id="case-video" controls playsinline preload="metadata" style="width:100%;border-radius:8px;margin-top:14px" src="'+first+'"></video><p class="fine">Download individual videos: '+links+'</p></div><script>document.getElementById("video-case-select").onchange=function(){const video=document.getElementById("case-video");video.pause();video.src=this.value;video.load();};</script>'

def comparative_highlights(data_path,assets,out,font=None):
    bundle=json.loads(Path(data_path).read_text());films={mode:Film(data,assets,font,'Direct API'if mode=='api'else'Harness')for mode,data in bundle['modes'].items()}
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);fps=12
    process=subprocess.Popen([shutil.which('ffmpeg')or'ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s','1920x1080','-r',str(fps),'-i','-','-an','-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(out)],stdin=subprocess.PIPE)
    first=next(iter(films.values()));intro=first.frame('square-MIRROR-042',0);d=ImageDraw.Draw(intro);d.rounded_rectangle((220,430,1700,670),radius=18,fill='#0d151a',outline='#80e0be',width=2);first.text(d,(330,470),'A passage—or a mirror?',45);first.text(d,(330,550),'No map. Just three actions.',38)
    try:
        for _ in range(fps*5):process.stdin.write(intro.tobytes())
        for key in ['square-MIRROR-042','hex-MIRROR-042']:
            for mode,film in films.items():
                for n in [1,5,10,20,40,80,120,160,200]:
                    for k in range(24):process.stdin.write(film.frame(key,n,(k+1)/24,True).tobytes())
                for _ in range(fps):process.stdin.write(film.frame(key,200,1,True).tobytes())
                print('HIGHLIGHT_CHAPTER '+mode+' '+key,flush=True)
        end=Image.new('RGB',(1920,1080),BG);d=ImageDraw.Draw(end);first.text(d,(100,70),'Four fixed cases: complete traces and reproducible code',45)
        first.text(d,(100,160),'Model',28);first.text(d,(920,160),'Harness solved',28);first.text(d,(1320,160),'Direct API Solved',28)
        for i,m in enumerate(first.models):
            y=240+i*100;first.text(d,(100,y),SHORT.get(m,m),28,COLORS.get(m,WHITE))
            for mode,x in [('harness',970),('api',1360)]:
                data=bundle['modes'].get(mode,{});cases=[c for c in data.get('cases',[])if c['model']==m];first.text(d,(x,y),str(sum(c['success']for c in cases))+'/4',38)
        first.text(d,(100,810),'Four fixed cases per model/configuration; exploratory results.',28,MUTED)
        first.text(d,(100,870),'Decision-synchronized playback does not measure inference speed. See the companion page for replays, settings, and code.',24,MUTED)
        first.text(d,(100,960),'Try it yourself. Then plug in your model.',38,'#c4eda0')
        for _ in range(fps*10):process.stdin.write(end.tobytes())
    finally:process.stdin.close()
    if process.wait()!=0:raise RuntimeError('ffmpeg highlights failed')
    print('HIGHLIGHTS_SAVED '+str(out.resolve()),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--assets',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--mode');p.add_argument('--case');p.add_argument('--combined',action='store_true',help='Optional archival combined film; the default exports one video per case');p.add_argument('--preview',action='store_true');p.add_argument('--highlight',action='store_true');p.add_argument('--font');a=p.parse_args()
    if a.case or a.combined:render(a.data,a.assets,a.out,a.mode,a.preview,a.highlight,a.font,case_key=a.case)
    else:render_cases(a.data,a.assets,a.out,a.mode,a.preview,a.highlight,a.font)

if __name__=='__main__':main()
