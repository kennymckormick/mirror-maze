import {Episode,TOPOLOGIES,VERSION,angleFor} from './maze.js';
import {MirrorRenderer} from './renderer.js';
import {drawMinimap} from './minimap.js';
const $=id=>document.getElementById(id);
let topology='square',episode=new Episode(),renderer=null,busy=false,mapOpen=false,showRoute=false,toastTimer,animationToken=0,renderHistory=[],benchmarking=false;
const formatMs=value=>value===0?'below timer precision':value.toFixed(2)+' ms';
const settings=()=>({mirror:$('mirror').checked,bounces:Number($('bounces').value),quality:$('quality').value});
const options=()=>({topology,size:$('size').value,seed:$('seed').value.trim()||'MIRROR-001'});
function toast(message){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.add('show');toastTimer=setTimeout(()=>$('toast').classList.remove('show'),2300);}
function failure(error){$('error').hidden=false;$('error').textContent='Renderer initialization failed: '+error.message;$('render-status').textContent='Renderer unavailable';console.error(error);}
function syncButtons(){for(const id of ['left','forward','right'])$(id).disabled=busy||benchmarking||!!episode.finishedAt||!renderer;}
function render(pose={}){
  if(!renderer)return;
  const s=episode.state(),position=episode.maze.cells[s.cell].center;
  const metrics=renderer.render({cell:s.cell,position,angle:s.angle,...settings(),...pose});
  $('render-status').textContent=`WebGL 2 · ${metrics.width} × ${metrics.height} · on-demand rendering`;
  $('metrics').textContent=`CPU submission ${formatMs(metrics.cpuMs)} / frame${metrics.gpuMs!==null?'; recent GPU render '+metrics.gpuMs.toFixed(2)+' ms / frame':'; GPU timing unavailable in this browser'}. CPU submission timing excludes GPU execution time.`;
}
function update(){
  const s=episode.state(),maze=episode.maze,degree=Math.round(((s.angle*180/Math.PI)%360+360)%360);
  $('action-count').textContent=s.actionCount;$('move-count').textContent=s.moves;$('collision-count').textContent=s.collisions;
  $('heading-label').textContent=degree+'°';$('heading-arrow').style.transform=`rotate(${degree+90}deg)`;
  $('scene-label').textContent=TOPOLOGIES[topology].name+' grid · side '+maze.sideLength+' · '+maze.cells.length+' cells';
  $('map-meta').textContent=`${maze.outerShape==='square'?'Square outline':'Hexagon outline'} · side ${maze.sideLength} · ${maze.cells.length} cells`;
  $('view-tag').textContent=settings().mirror?`${settings().bounces} mirror bounces`:'Matte walls · control mode';
  $('left-degrees').textContent=$('right-degrees').textContent=(360/maze.config.headings)+'°';
  $('geometry-note').textContent=topology==='triangle'?'60° turns; forward toward a vertex is blocked. Heading is preserved across cells.':'Forward crosses one shared edge to the adjacent cell center.';
  $('win').hidden=!s.success;
  if(s.success)$('win-stats').textContent=`${s.actionCount} actions · moved ${s.moves} cells · collisions ${s.collisions}${episode.assists.size?' · assistance used':''}`;
  syncButtons();if(mapOpen)drawMap();updateClock();
}
function updateClock(){const seconds=Math.floor(((episode.finishedAt??Date.now())-episode.startedAt)/1000);$('elapsed').textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');}
function recordRender(){renderHistory.push({atAction:episode.actions.length,...settings()});if(!settings().mirror)episode.assists.add('mirror_disabled');}
function reset(next=options()){
  animationToken++;busy=false;clearTimeout(toastTimer);$('toast').classList.remove('show');$('hint').textContent='Show shortest route';$('benchmark-result').textContent='';if(window.mirrorMaze)window.mirrorMaze.lastBenchmark=null;episode=new Episode(next);topology=episode.maze.topology;showRoute=false;
  $('size').value=episode.maze.size;$('seed').value=episode.maze.seed;
  for(const b of document.querySelectorAll('[data-topology]')){const yes=b.dataset.topology===topology;b.classList.toggle('selected',yes);b.setAttribute('aria-pressed',String(yes));}
  if(mapOpen)episode.assists.add('map');renderHistory=[];recordRender();
  if(renderer){renderer.setMaze(episode.maze);renderer.resize(settings().quality);render();}
  update();return observe();
}
async function act(action,{animate=true,capture=false}={}){
  if(!renderer)return {accepted:false,reason:'renderer_unavailable'};
  if(busy||benchmarking)return {accepted:false,reason:'busy'};
  const before=episode.state(),from=episode.maze.cells[before.cell].center,result=episode.step(action);
  if(!result.accepted)return result;
  const after=episode.state(),to=episode.maze.cells[after.cell].center;
  if(result.reason==='wall')toast('A mirror wall blocks the way.');
  if(result.reason==='vertex')toast('You face a triangle vertex. Turn left or right first.');
  if(animate&&(result.moved||action!=='forward')){
    busy=true;syncButtons();const token=animationToken,start=performance.now(),duration=result.moved?240:190;
    let delta=after.angle-before.angle;while(delta>Math.PI)delta-=Math.PI*2;while(delta< -Math.PI)delta+=Math.PI*2;
    await new Promise(resolve=>{const frame=now=>{if(token!==animationToken){resolve();return;}const t=Math.min((now-start)/duration,1),smooth=t*t*(3-2*t),cell=t<.5?before.cell:after.cell;const position=[from[0]+(to[0]-from[0])*smooth,from[1]+(to[1]-from[1])*smooth],angle=before.angle+delta*smooth;
      render({cell,position,angle,playerCell:cell,playerPosition:position,playerAngle:angle});if(t<1)requestAnimationFrame(frame);else resolve();};requestAnimationFrame(frame);});
    if(token!==animationToken)return {accepted:false,reason:'reset_during_animation'};
  }
  busy=false;render();update();return {...result,...(capture?{observation:observe()}:{})};
}
function observe({includeState=false}={}){
  const result={version:VERSION,ready:!!renderer&&!busy,terminal:!!episode.finishedAt,actions:['left','right','forward'],observation:renderer?.image()??null,render:settings(),turnDegrees:360/episode.maze.config.headings};
  if(includeState)result.debug={...episode.state(),goal:episode.maze.goal,options:options()};return result;
}
function saveFile(name,content,type){const blob=new Blob([content],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}
function exportEpisode(){return {...episode.export(),renderHistory,renderLimits:{maxSegments:160,maxReflections:12,cutoff:'dark_termination'},renderMetrics:renderer?{...renderer.metrics}:null};}
function download(){saveFile(`mirror-maze-${topology}-${episode.maze.seed.replace(/[^a-z0-9_-]/gi,'_')}.json`,JSON.stringify(exportEpisode(),null,2),'application/json');toast('Episode and action history exported.');}
function drawMap(){
  const maze=episode.maze,route=[];
  if(showRoute){const preview=episode.fork();for(const action of preview.shortestActions()){const result=preview.step(action);if(result.moved)route.push(preview.cell);}}
  const mapState={cell:episode.cell,angle:episode.state().angle,visited:episode.visited,route};
  drawMinimap($('map'),maze,mapState);
  if($('map-dialog').open)drawMinimap($('large-map'),maze,mapState);
}
function toggleMap(show=!mapOpen){
  mapOpen=show;$('map-panel').hidden=!show;$('scene-review').classList.toggle('map-hidden',!show);
  $('map-toggle').setAttribute('aria-pressed',String(show));$('map-toggle').textContent=show?'◫ Hide map':'◫ Show map';
  if(show){episode.assists.add('map');drawMap();}
}
for(const b of document.querySelectorAll('[data-topology]'))b.addEventListener('click',()=>{topology=b.dataset.topology;reset();});
$('generate').onclick=()=>reset();$('size').onchange=()=>reset();$('seed').onkeydown=e=>{if(e.key==='Enter')reset();};
$('shuffle').onclick=()=>{$('seed').value='M-'+crypto.getRandomValues(new Uint32Array(1))[0].toString(36).toUpperCase();reset();};
$('restart').onclick=$('win-restart').onclick=()=>reset({topology:episode.maze.topology,size:episode.maze.size,seed:episode.maze.seed});
for(const a of ['left','right','forward'])$(a).onclick=()=>act(a);
document.addEventListener('keydown',e=>{
  if(e.target.matches('input,select,textarea')||$('about-dialog').open||e.ctrlKey||e.metaKey||e.altKey)return;
  const a={ArrowLeft:'left',a:'left',A:'left',ArrowRight:'right',d:'right',D:'right',ArrowUp:'forward',w:'forward',W:'forward'}[e.key];if(a){e.preventDefault();if(!e.repeat)act(a);}
});
$('map-toggle').onclick=()=>toggleMap();$('map-expand').onclick=()=>{$('map-dialog').showModal();drawMap();};$('map-dialog-close').onclick=()=>$('map-dialog').close();$('hint').onclick=()=>{showRoute=!showRoute;if(showRoute)episode.assists.add('route');$('hint').textContent=showRoute?'Hide shortest route':'Show shortest route';drawMap();};
for(const id of ['mirror','quality'])$(id).onchange=()=>{recordRender();if(renderer){renderer.resize(settings().quality);render();}update();};
$('bounces').oninput=()=>{$('bounce-value').textContent=$('bounces').value+' bounces';render();update();};$('bounces').onchange=recordRender;
$('export').onclick=$('win-export').onclick=download;
$('snapshot').onclick=()=>{if(!renderer)return;const a=document.createElement('a');a.href=renderer.image();a.download=`mirror-maze-observation-${episode.actions.length}.png`;a.click();toast('First-person image saved.');};
$('about').onclick=()=>$('about-dialog').showModal();$('close-about').onclick=$('about-done').onclick=()=>$('about-dialog').close();
$('about-dialog').onclick=e=>{if(e.target===$('about-dialog')){const r=$('about-dialog').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('about-dialog').close();}};
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('viewport').requestFullscreen();}catch{toast('Fullscreen is unavailable; enlarge the window instead.');}};
new ResizeObserver(()=>{if(renderer){renderer.resize(settings().quality);render();}}).observe($('scene'));
$('benchmark').onclick=async()=>{
  if(!renderer||busy||benchmarking)return;benchmarking=true;syncButtons();$('benchmark').disabled=true;$('benchmark-result').textContent='Rendering 45 frames…';
  const cpu=[],gpu=[],deltas=[],token=animationToken,benchSettings=JSON.stringify(settings());let last;for(let i=0;i<45;i++){await new Promise(requestAnimationFrame);if(token!==animationToken||benchSettings!==JSON.stringify(settings())){benchmarking=false;$('benchmark').disabled=false;$('benchmark-result').textContent='Settings changed; run the benchmark again.';syncButtons();return;}const t=performance.now();if(last)deltas.push(t-last);last=t;render();cpu.push(renderer.metrics.cpuMs);if(renderer.metrics.gpuMs!==null)gpu.push(renderer.metrics.gpuMs);}
  const median=a=>a.sort((a,b)=>a-b)[Math.floor(a.length/2)];const result={frames:45,...settings(),resolution:[renderer.canvas.width,renderer.canvas.height],cpuSubmitMedianMs:median(cpu),gpuRecentMedianMs:gpu.length?median(gpu):null,rafMedianMs:median(deltas)};
  $('benchmark-result').textContent=`CPU ${formatMs(result.cpuSubmitMedianMs)}${gpu.length?' / GPU '+result.gpuRecentMedianMs.toFixed(2)+' ms':''} · ${Math.round(1000/result.rafMedianMs)} fps`;
  $('benchmark-result').title='Frame rate includes browser scheduling and display refresh limits. This stationary-view test does not measure worst-case maze performance.';window.mirrorMaze.lastBenchmark=result;benchmarking=false;$('benchmark').disabled=false;syncButtons();
};
window.mirrorMaze={version:VERSION,perform:(actions,opts={})=>{if(busy||benchmarking)throw new Error('The environment is executing actions');const result=episode.perform(actions,opts);if(!opts.dryRun){render();update();}return result;},reset:next=>reset({...options(),...next}),step:(action)=>act(action,{animate:false,capture:true}),observe,exportEpisode,getState:()=>({...episode.state(),goal:episode.maze.goal}),getMaze:()=>structuredClone(episode.maze),lastBenchmark:null};
toggleMap(false);
try{renderer=new MirrorRenderer($('scene'));reset();}catch(error){failure(error);syncButtons();}
setInterval(updateClock,1000);
