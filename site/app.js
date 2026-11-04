const BASE = new URL('.', import.meta.url);
const $ = id => document.getElementById(id);
const esc = text => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = n => Number(n).toLocaleString('en-US');
const fetchJSON = path => fetch(new URL(path, BASE)).then(r => {if (!r.ok) throw Error(`Could not load ${path}`); return r.json();});
const colors = ['#557f92','#bb7d53','#7f7299','#5a8c77'];
const names = ['left','right','forward'];
const armName = arm => arm === 'api' ? 'Direct API' : arm === 'astra-codex' ? 'Codex' : 'Native client';
const maps = new Map(), episodes = new Map();
let study, analysis, stress, hero, heroChoice = 0, currentEpisode, currentRound = 0, withinSequence = null;

async function loadMap(id) {
  if (!maps.has(id)) {
    const info = study.maps.find(m => m.id === id);
    maps.set(id, fetchJSON(info.file));
  }
  return maps.get(id);
}

async function loadEpisode(id) {
  if (!episodes.has(id)) {
    const info = study.cases.find(c => c.id === id);
    episodes.set(id, fetchJSON(info.episode).then(e => {
      const map = study.maps.find(m => m.id === e.map);
      let count = 0, hits = 0;
      const seen = new Set(), edges = new Map();
      e.prefix = [{actions:0,collisions:0,visited:1}];
      e.traversed = edges;
      e.prepare = m => {
        if (e.prepared) return;
        let previous = m.initial.cell; seen.add(previous);
        for (let i = 0; i < e.steps.length; i++) {
          const s = e.steps[i]; count += s[0] >= 0; hits += s[4]; seen.add(s[1]);
          if (previous !== s[1]) {
            const key = [Math.min(previous,s[1]),Math.max(previous,s[1])].join('-');
            if (!edges.has(key)) edges.set(key,{from:previous,to:s[1],at:i+1});
          }
          e.prefix.push({actions:count,collisions:hits,visited:seen.size}); previous = s[1];
        }
        e.prepared = true;
      };
      return e;
    }));
  }
  const e = await episodes.get(id), m = await loadMap(e.map); e.prepare(m); return e;
}

function mapSVG(m, pose, e = null, limit = 0) {
  const points = m.cells.flatMap(c => c.vertices), xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
  const scale = Math.min(810/(xmax-xmin),810/(ymax-ymin));
  const xy = p => [450+(p[0]-(xmin+xmax)/2)*scale,450+(p[1]-(ymin+ymax)/2)*scale];
  let walls = '', route = '';
  for (const cell of m.cells) for (const edge of cell.edges) if (!edge.open && (edge.neighbor < 0 || edge.neighbor > cell.id)) {
    const a = xy(edge.a), b = xy(edge.b); walls += `M${a[0]},${a[1]}L${b[0]},${b[1]}`;
  }
  if (e) for (const edge of e.traversed.values()) if (edge.at <= limit) {
    const a = xy(m.cells[edge.from].center), b = xy(m.cells[edge.to].center); route += `M${a[0]},${a[1]}L${b[0]},${b[1]}`;
  }
  const [sx,sy] = xy(m.cells[m.initial.cell].center), [gx,gy] = xy(m.cells[m.goal].center);
  const [x,y] = xy(m.cells[pose.cell].center), angle = (m.topology === 'hex' ? Math.PI/6 : 0) + pose.heading*2*Math.PI/m.headings;
  const triangle = [0,2.5,-2.5].map((d,i) => `${x+(i?11:16)*Math.cos(angle+d)},${y+(i?11:16)*Math.sin(angle+d)}`).join(' ');
  return `<svg viewBox="0 0 900 900" role="img" aria-label="Review map with traversed edges and current heading"><rect width="900" height="900" fill="#142b33"/><path d="${route}" fill="none" stroke="#8ab2c3" stroke-width="5" stroke-linecap="round"/><path d="${walls}" fill="none" stroke="#6e8a95" stroke-width="2.2"/><circle cx="${sx}" cy="${sy}" r="7" fill="#c69b62"/><circle cx="${gx}" cy="${gy}" r="9" fill="#a6ca97"/><polygon points="${triangle}" fill="#fff0bf" stroke="#142b33" stroke-width="2"/></svg>`;
}

function stateAt(e, round, actionCount = null) {
  const actual = Math.min(round,e.planning), r = actual ? e.rounds[actual-1] : null;
  let limit = r ? r.end : 0;
  if (r && actionCount !== null && round <= e.planning) limit = r.begin + Math.min(actionCount,r.end-r.begin);
  const s = limit ? e.steps[limit-1] : null, p = e.prefix[limit];
  const terminal = limit === e.steps.length && round >= e.planning;
  return {r,s,p,limit,decision:actual,terminal,status:terminal ? (e.status === 'success' ? 'Goal reached' : 'Decision cap') : 'Exploring'};
}

async function pane(e, round, actionCount = null, single = false) {
  const m = await loadMap(e.map), st = stateAt(e,round,actionCount), pose = st.s ? {cell:st.s[1],heading:st.s[2]} : m.initial;
  const clientName = e.arm==='api'?'Direct API':({codex:'Codex',claude:'Claude Code',agy:'Antigravity CLI'}[e.harness]||armName(e.arm));
  const image = e.images[st.s ? st.s[3] : e.initialImage], title = `${e.name} · ${clientName}`;
  const imageHTML = `<div class="pane-image"><img src="${new URL(image,BASE)}" width="1280" height="720" alt="Recorded first-person observation"><span class="image-tag">Recorded observation</span></div>`;
  const mapHTML = `<div class="pane-map">${mapSVG(m,pose,e,st.limit)}${single?'<span class="map-label">Review only · traversed edges + current pose</span>':''}</div>`;
  const stats = `<div class="pane-stats"><span>Decisions <b>${st.decision}</b></span><span>Actions <b>${number(st.p.actions)}</b></span><span>Collisions <b>${number(st.p.collisions)}</b></span><span>Visited <b>${st.p.visited}/${e.cells}</b></span></div>`;
  const plan = st.r ? st.r.plan || 'No intention text submitted.' : 'Initial observation. No planning decision submitted yet.';
  const heading = `<div class="pane-title"><b>${esc(title)}</b><span class="outcome">${st.status}</span></div>`;
  if (single) return `<div class="single-replay"><div>${heading}${imageHTML}${stats}</div>${mapHTML}<p class="pane-plan">${esc(plan)}</p></div>`;
  return `<div class="replay-pane">${heading}${imageHTML}${mapHTML}${stats}<p class="pane-plan">${esc(plan)}</p></div>`;
}

async function showHero(reveal = false, guess = null) {
  const view = hero.views[heroChoice], m = await loadMap(hero.map);
  $('hero-observation').src = new URL(view.image,BASE);
  $('view-a').setAttribute('aria-pressed',String(heroChoice===0)); $('view-b').setAttribute('aria-pressed',String(heroChoice===1));
  $('hero-map-wrap').hidden = !reveal; $('reveal-geometry').setAttribute('aria-expanded',String(reveal));
  $('reveal-geometry').textContent = reveal ? 'Hide the geometry' : 'Reveal the geometry';
  $('hero-map').innerHTML = mapSVG(m,view);
  const answer = view.open ? 'This direction crosses a real open edge.' : 'A mirror wall blocks this direction. The visible continuation is a reflection.';
  $('puzzle-answer').textContent = reveal ? `${guess===null?'':guess===view.open?'Correct. ':'The geometry says otherwise. '}${answer}` : 'These are two recorded orientations at the same cell. The answer comes from the maze geometry.';
  $('puzzle-answer').classList.toggle('correct',reveal && (guess === null || guess === view.open));
}

function resultTable() {
  const models = ['deepseek','kimi','gpt','gemini','seed'];
  const clients = {deepseek:'Claude Code',kimi:'Claude Code',gpt:'Codex',gemini:'Antigravity CLI · Medium',seed:'Claude Code'};
  const cell = (model,arm) => {
    const cases = study.cases.filter(c => c.model===model && c.arm===arm), count = cases.filter(c=>c.status==='success').length;
    return `<div class="result-cell"><span class="result-number">${count}/4</span><div class="case-dots">${cases.map(c=>`<button class="case-dot ${c.status==='success'?'success':''}" data-episode="${c.id}" title="${esc(c.topology+' '+c.seed+': '+(c.status==='success'?'success':'decision cap')+', '+c.planning+' decisions')}" aria-label="Open ${esc(c.name+' '+armName(arm)+' '+c.topology+' '+c.seed)}"></button>`).join('')}</div></div>`;
  };
  $('model-results').querySelector('tbody').innerHTML = models.map(m=>`<tr><th scope="row">${esc(study.models[m])}${m==='deepseek'?'*':''}</th><td>${clients[m]}</td><td>${cell(m,'harness')}</td><td>${cell(m,'api')}</td></tr>`).join('');
  document.querySelectorAll('[data-episode]').forEach(button=>button.addEventListener('click',()=>{
    $('episode-picker').value = button.dataset.episode; openEpisode(button.dataset.episode); $('run-explorer').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  }));
}

function plotSVG(series, options = {}) {
  const mobile=window.innerWidth<650, W=mobile?Math.max(300,window.innerWidth-70):960, H=mobile?350:390;
  const margin={left:mobile?45:62,right:mobile?16:25,top:mobile?64:34,bottom:56}, xmax=options.xmax||200, log=options.log||false;
  const x=v=>margin.left+(log?(Math.log10(v)-3)/2:v/xmax)*(W-margin.left-margin.right);
  const y=v=>H-margin.bottom-v/100*(H-margin.top-margin.bottom);
  let content='';
  for(const tick of [0,25,50,75,100]) content+=`<path d="M${margin.left},${y(tick)}H${W-margin.right}" stroke="#e9edef"/><text x="${margin.left-13}" y="${y(tick)+4}" text-anchor="end" font-size="11" fill="#94a0a7">${tick}%</text>`;
  const xticks=log?[1000,10000,100000]:xmax===500?[0,100,200,300,400,500]:[0,50,100,150,200];
  for(const tick of xticks) content+=`<text x="${x(tick)}" y="${H-31}" text-anchor="middle" font-size="11" fill="#94a0a7">${number(tick)}</text>`;
  if(options.band){const a=x(options.band[0]),b=x(options.band[1]);content+=`<rect x="${a}" y="${margin.top}" width="${b-a}" height="${H-margin.top-margin.bottom}" fill="#c8d1d6" opacity=".3"/>`;}
  series.forEach((s,i)=>{
    content+=`<polyline points="${s.points.map(p=>`${x(p.x)},${y(p.y)}`).join(' ')}" fill="none" stroke="${s.color||colors[i]}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
    if(log) for(const p of s.points) content+=`<circle cx="${x(p.x)}" cy="${y(p.y)}" r="4" fill="${s.color||colors[i]}"/>`;
    const columns=log?2:1, lx=mobile?margin.left+(i%columns)*(W-margin.left-margin.right)/columns:margin.left+i*(log?213:370), ly=mobile?17+Math.floor(i/columns)*18:17;
    content+=`<path d="M${lx},${ly}h17" stroke="${s.color||colors[i]}" stroke-width="2.5"/><text x="${lx+23}" y="${ly+4}" font-size="${mobile?10:11}" fill="#6c818e">${esc(s.label)}</text>`;
  });
  content+=`<text x="${(W+margin.left-margin.right)/2}" y="${H-4}" text-anchor="middle" font-size="10" fill="#91a0a9">${options.xlabel||'Planning decision'}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" aria-hidden="true">${content}</svg>`;
}

function coverageChart() {
  const mode=$('coverage-view').value;
  const ids=mode==='astra'?['astra-codex/hex-MIRROR-042','astra-codex/hex-MIRROR-043']:mode==='seed'?['api/seed-hex-MIRROR-042','api/seed-hex-MIRROR-043']:['harness/gpt-square-MIRROR-043','api/gpt-square-MIRROR-043'];
  const cases=ids.map(id=>study.cases.find(c=>c.id===id));
  const series=cases.map((c,i)=>({label:mode==='gpt'?(i?'Direct API':'Codex'):`${mode==='astra'?'Astra':'Seed'} · ${c.seed} · ${c.status==='success'?'success':'cap'}`,points:c.curve.map(p=>({x:p.round,y:100*p.visited/c.cells})),color:colors[i]}));
  $('coverage-chart').innerHTML=plotSVG(series,{xmax:mode==='astra'?500:200,band:mode==='astra'?[121,161]:mode==='seed'?[5,200]:[26,170]});
  $('coverage-readout').textContent=mode==='astra'?'Hex 042: 264/331 cells, goal at decision 246. Hex 043: 206/331 cells at the 500-decision cap.':mode==='seed'?'Hex 043 remained at 4/127 visited cells for decisions 5-200: 196 consecutive decisions without a new cell.':'Codex reached the goal at decision 49. Direct API held at 14/49 cells from decisions 26-170.';
  $('coverage-caption').textContent='Curves end when the recorded run terminates. The shaded interval marks a no-new-cell plateau in the highlighted capped run; it does not describe hidden reasoning.';
}

function stressChart() {
  const difficulty=$('stress-difficulty').value,budget=+$('stress-budget').value;
  const labels=['square-MIRROR-042','square-MIRROR-043','hex-MIRROR-042','hex-MIRROR-043'];
  const series=labels.map((key,i)=>({label:(key.startsWith('square')?'Square':'Hexagon')+' '+key.slice(-3),color:colors[i],points:stress.aggregates.filter(r=>r.map===difficulty+'/'+key).map(r=>({x:r.budget,y:r.solve_fraction*100}))}));
  $('stress-chart').innerHTML=plotSVG(series,{log:true,xlabel:'Motor-action limit in one pre-generated plan'});
  const rows=stress.aggregates.filter(r=>r.difficulty===difficulty && r.budget===budget), solved=rows.reduce((n,r)=>n+r.solved,0),actions=rows.reduce((n,r)=>n+r.mean_executed_actions,0)/4,hits=rows.reduce((n,r)=>n+r.mean_collisions,0)/4;
  $('stress-readout').textContent=`${number(solved)}/2,000 map-trials solved (${(solved/20).toFixed(2)}%). Mean executed actions: ${number(Math.round(actions))}; mean collisions: ${number(Math.round(hits))}. These are code-policy simulations.`;
}

let pairGeneration=0;
async function pairReplay() {
  const generation=++pairGeneration,round=+$('pair-round').value;
  $('pair-round-label').textContent=`${round} / 200`;
  const es=await Promise.all(['harness/gpt-square-MIRROR-043','api/gpt-square-MIRROR-043'].map(loadEpisode));
  const html=await Promise.all(es.map(e=>pane(e,round)));
  if(generation===pairGeneration)$('pair-replay').innerHTML=html.join('');
}

let episodeGeneration=0;
async function openEpisode(id) {
  const generation=++episodeGeneration;$('episode-stage').innerHTML='<p class="loading">Loading the recorded trajectory...</p>';
  const e=await loadEpisode(id);if(generation!==episodeGeneration)return;
  currentEpisode=e;currentRound=e.planning;withinSequence=null;
  $('episode-round').max=e.planning;$('episode-round').value=e.planning;
  await episodeReplay();
}
async function episodeReplay() {
  if(!currentEpisode)return;
  const e=currentEpisode,round=+$('episode-round').value,r=round?e.rounds[round-1]:null;
  const length=r?r.end-r.begin:0;
  if(currentRound!==round){currentRound=round;withinSequence=null;}
  $('episode-action').max=length;
  if(withinSequence===null)$('episode-action').value=length;
  else $('episode-action').value=withinSequence;
  const within=+$('episode-action').value;
  $('episode-round-label').textContent=`${round} / ${e.planning} recorded decisions`;
  $('episode-action-label').textContent=`${within} / ${length} executed in this sequence`;
  const html=await pane(e,round,within,true);
  if(e===currentEpisode && round===+$('episode-round').value)$('episode-stage').innerHTML=html;
}

function films() {
  const records=[];
  for(const arm of ['harness','api'])for(const topology of ['square','hex'])for(const seed of ['MIRROR-042','MIRROR-043'])records.push({path:`videos/${arm}/${topology}-${seed}.mp4`,label:`${arm==='api'?'Direct API':'Native clients'} · ${topology==='hex'?'Hexagon':'Square'} ${seed}`});
  $('film-picker').innerHTML=records.map((r,i)=>`<option value="${i}">${r.label}</option>`).join('');
  const change=()=>{const r=records[+$('film-picker').value];$('full-film').src=new URL(r.path,BASE);$('film-download').href=new URL(r.path,BASE);};
  $('film-picker').addEventListener('change',change);change();
}

async function initialize() {
  [study,stress,hero]=await Promise.all(['data/study.json','data/stress-summary.json','data/hero.json'].map(fetchJSON));
  resultTable();coverageChart();stressChart();films();await showHero();
  $('view-a').onclick=()=>{heroChoice=0;showHero();};$('view-b').onclick=()=>{heroChoice=1;showHero();};
  $('guess-open').onclick=()=>showHero(true,true);$('guess-mirror').onclick=()=>showHero(true,false);
  $('reveal-geometry').onclick=()=>showHero($('hero-map-wrap').hidden);
  $('coverage-view').onchange=coverageChart;$('stress-difficulty').onchange=stressChart;$('stress-budget').onchange=stressChart;
  $('pair-round').oninput=pairReplay;
  const groups=[['harness','Easy · native clients'],['api','Easy · direct API'],['astra-codex','Medium · GPT-6 Astra + Codex']];
  $('episode-picker').innerHTML=groups.map(([arm,label])=>`<optgroup label="${label}">${study.cases.filter(c=>c.arm===arm).map(c=>`<option value="${c.id}">${esc(c.name+' · '+(c.topology==='hex'?'Hexagon':'Square')+' · '+c.seed+' · '+(c.status==='success'?'success':'cap'))}</option>`).join('')}</optgroup>`).join('');
  $('episode-picker').onchange=()=>openEpisode($('episode-picker').value);
  $('episode-round').oninput=()=>{withinSequence=null;episodeReplay();};
  $('episode-action').oninput=()=>{withinSequence=+$('episode-action').value;episodeReplay();};
  const initial='astra-codex/hex-MIRROR-043';$('episode-picker').value=initial;
  await Promise.all([pairReplay(),openEpisode(initial)]);
  let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{coverageChart();stressChart();},120);});
}
initialize().catch(error=>{console.error(error);document.querySelectorAll('.chart,#episode-stage,#pair-replay').forEach(e=>{if(!e.textContent)e.innerHTML='<p class="loading">The interactive evidence could not load. The CSV and protocol downloads remain available below.</p>';});});
