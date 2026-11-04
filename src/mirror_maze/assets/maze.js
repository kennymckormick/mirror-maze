export const VERSION = '0.2.0';
export const TAU = Math.PI * 2;
export const CELL_SPACING = 2.6;
export const TOPOLOGIES = {
  square: { name: 'Square', sides: 4, headings: 4, base: 0, dimensions: [7, 11, 15] },
  triangle: { name: 'Equilateral triangle', sides: 3, headings: 6, base: Math.PI / 6, dimensions: [7, 11, 15] },
  hex: { name: 'Regular hexagon', sides: 6, headings: 6, base: Math.PI / 6, dimensions: [7, 11, 15] },
};
export function randomFor(seed) {
  let h = 2166136261;
  for (const c of String(seed)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
  return () => { h += 0x6D2B79F5; let t = h; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
const key = p => p.map(v => Math.round(v * 1e5)).join(',');
export function makeMaze({ topology = 'square', size = 'small', difficulty, sideLength, seed = 'MIRROR-001' } = {}) {
  if (!TOPOLOGIES[topology]) throw new Error('Unknown topology');
  if (difficulty !== undefined) {
    size = { easy: 'small', medium: 'medium', hard: 'large' }[difficulty];
    if (!size) throw new Error('difficulty must be easy, medium, or hard');
  }
  const cfg = TOPOLOGIES[topology], sizeIndex = ['small', 'medium', 'large'].indexOf(size);
  if (sizeIndex < 0) throw new Error('Unknown maze size');
  const n = sideLength ?? cfg.dimensions[sizeIndex];
  if (!Number.isInteger(n) || n < 2 || n > 31) throw new Error('sideLength must be an integer between 2 and 31');
  const cells = [], rng = randomFor(seed), spacing = CELL_SPACING;
  function add(vertices) {
    const center = vertices.reduce((a, b) => [a[0] + b[0] / vertices.length, a[1] + b[1] / vertices.length], [0, 0]);
    cells.push({ id: cells.length, center, vertices, edges: [], landmark: 0 });
  }
  if (topology === 'square') {
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      const a = x * spacing, b = y * spacing, r = spacing / 2;
      add([[a-r,b-r],[a+r,b-r],[a+r,b+r],[a-r,b+r]]);
    }
  } else if (topology === 'hex') {
    const r = spacing / Math.sqrt(3);
    for (let q = -n+1; q < n; q++) for (let s = -n+1; s < n; s++) if (Math.abs(q+s) < n) {
      const x = r * 1.5 * q, y = spacing * (s + q/2);
      add(Array.from({length:6},(_,i)=>[x+r*Math.cos(i*TAU/6),y+r*Math.sin(i*TAU/6)]));
    }
  } else {
    // Clip the triangular lattice to an exact regular hexagon. Each of its six
    // sides contains n triangle edges; all cells remain complete triangles.
    const s = spacing * Math.sqrt(3), h = s * Math.sqrt(3) / 2;
    const v = ([i,j]) => [s*(i+j/2), h*j];
    const inside = ([i,j]) => Math.max(Math.abs(i),Math.abs(j),Math.abs(i+j)) <= n;
    for (let j=-n;j<n;j++) for(let i=-n;i<n;i++) {
      for (const tri of [ [[i,j],[i+1,j],[i,j+1]], [[i+1,j],[i+1,j+1],[i,j+1]] ]) {
        if (tri.every(inside)) add(tri.map(v));
      }
    }
  }
  const edgeMap = new Map();
  for (const c of cells) c.vertices.forEach((a,i)=>{
    const b=c.vertices[(i+1)%cfg.sides], dx=b[0]-a[0],dy=b[1]-a[1],len=Math.hypot(dx,dy);
    const edge={a,b,normal:[dy/len,-dx/len], neighbor:-1,open:false}; c.edges.push(edge);
    const ek=[key(a),key(b)].sort().join('|');
    if (edgeMap.has(ek)) { const [other,oe]=edgeMap.get(ek); edge.neighbor=other.id; oe.neighbor=c.id; } else edgeMap.set(ek,[c,edge]);
  });
  const start=0, visited=new Set([start]), stack=[start];
  while(stack.length) {
    const c=cells[stack.at(-1)], options=c.edges.filter(e=>e.neighbor>=0&&!visited.has(e.neighbor));
    if(!options.length){stack.pop();continue;}
    const e=options[Math.floor(rng()*options.length)]; e.open=true;
    cells[e.neighbor].edges.find(x=>x.neighbor===c.id).open=true;
    visited.add(e.neighbor);stack.push(e.neighbor);
  }
  const distances=Array(cells.length).fill(-1);distances[start]=0;const queue=[start];
  for(let i=0;i<queue.length;i++) for(const e of cells[queue[i]].edges) if(e.open&&distances[e.neighbor]<0){distances[e.neighbor]=distances[queue[i]]+1;queue.push(e.neighbor);}
  const goal=distances.indexOf(Math.max(...distances));
  for(const c of cells) if(c.id!==start&&c.id!==goal&&rng()<.24)c.landmark=1+Math.floor(rng()*3);
  const first=cells[start].edges.find(e=>e.open), angle=Math.atan2(first.normal[1],first.normal[0]);
  const initialHeading=((Math.round((angle-cfg.base)/(TAU/cfg.headings))%cfg.headings)+cfg.headings)%cfg.headings;
  return {version:VERSION,topology,size,difficulty:['easy','medium','hard'][sizeIndex],sideLength:n,outerShape:topology==='square'?'square':'hexagon',cellSpacing:spacing,seed:String(seed),cells,start,goal,initialHeading,shortestDistance:distances[goal],config:cfg};
}
export function angleFor(maze,heading){return maze.config.base+heading*TAU/maze.config.headings;}
export function forwardEdge(maze,cellId,heading){
  const a=angleFor(maze,heading),d=[Math.cos(a),Math.sin(a)];
  return maze.cells[cellId].edges.find(e=>e.normal[0]*d[0]+e.normal[1]*d[1]>.9999)??null;
}
export class Episode {
  constructor(options={}) { this.reset(options); }
  reset(options={}) {
    this.maze=makeMaze(options);this.cell=this.maze.start;this.heading=this.maze.initialHeading;this.actions=[];this.visited=new Set([this.cell]);this.startedAt=Date.now();this.finishedAt=null;this.assists=new Set();this.collisions=0;this.moveCount=0;return this.state();
  }
  state(){return {cell:this.cell,heading:this.heading,angle:angleFor(this.maze,this.heading),success:this.cell===this.maze.goal,actionCount:this.actions.length,moves:this.moveCount,collisions:this.collisions,visited:this.visited.size};}
  step(action){
    if(!['left','right','forward'].includes(action))throw new Error('Action must be left, right, or forward');
    if(this.finishedAt)return {accepted:false,action,moved:false,collision:false,collisionType:null,reason:'episode_finished',...this.state()};
    const before={cell:this.cell,heading:this.heading};let moved=false,reason='turned';
    if(action==='left')this.heading=(this.heading+this.maze.config.headings-1)%this.maze.config.headings;
    if(action==='right')this.heading=(this.heading+1)%this.maze.config.headings;
    if(action==='forward'){
      const edge=forwardEdge(this.maze,this.cell,this.heading);
      if(edge?.open){this.cell=edge.neighbor;moved=true;reason='moved';this.visited.add(this.cell);this.moveCount++;}else{reason=edge?'wall':'vertex';this.collisions++;}
    }
    if(this.cell===this.maze.goal)this.finishedAt=Date.now();
    const collision=reason==='wall'||reason==='vertex';
    this.actions.push({index:this.actions.length,action,before,after:{cell:this.cell,heading:this.heading},moved,collision,collisionType:collision?reason:null,reason,elapsedMs:Date.now()-this.startedAt});
    return {accepted:true,action,moved,collision,collisionType:collision?reason:null,reason,...this.state()};
  }
  fork(){
    const other=Object.create(Episode.prototype);Object.assign(other,this);
    other.actions=this.actions.slice();other.visited=new Set(this.visited);other.assists=new Set(this.assists);return other;
  }
  perform(actions,{dryRun=false,stopOnCollision=true}={}){
    if(typeof actions==='string')actions=[actions];
    if(!Array.isArray(actions)||actions.some(a=>!['left','right','forward'].includes(a)))throw new Error('actions must be a valid action or an array of valid actions');
    if(dryRun)return {...this.fork().perform(actions,{stopOnCollision}),dryRun:true};
    const results=[];let executed=0,blockedAt=null,stopReason=null;
    for(let i=0;i<actions.length;i++){
      const result=this.step(actions[i]);results.push(result);
      if(result.accepted)executed++;
      if(result.collision&&blockedAt===null)blockedAt=i;
      if(!result.accepted){stopReason=result.reason;break;}
      if(result.collision&&stopOnCollision){stopReason=result.reason;break;}
    }
    return {dryRun:false,requested:actions.length,executed,completed:executed===actions.length,allExecutable:executed===actions.length&&blockedAt===null,collision:blockedAt!==null,blockedAt,stopReason,results,state:this.state()};
  }
  shortestActions(){
    const maze=this.maze,H=maze.config.headings,total=maze.cells.length*H,start=this.cell*H+this.heading;
    const parent=new Int32Array(total).fill(-1),via=new Uint8Array(total),queue=[start],names=['forward','left','right'];parent[start]=start;
    for(let i=0;i<queue.length;i++){
      const id=queue[i],c=Math.floor(id/H),h=id%H;
      if(c===maze.goal){const path=[];for(let k=id;k!==start;k=parent[k])path.push(names[via[k]]);return path.reverse();}
      for(let action=0;action<3;action++){
        let nc=c,nh=h;if(action===0){const e=forwardEdge(maze,c,h);if(!e?.open)continue;nc=e.neighbor;}else nh=(h+(action===1?H-1:1))%H;
        const next=nc*H+nh;if(parent[next]===-1){parent[next]=id;via[next]=action;queue.push(next);}
      }
    }return [];
  }
  export(){return {schema:'mirror-maze-episode/v1',environmentVersion:VERSION,options:{topology:this.maze.topology,size:this.maze.size,sideLength:this.maze.sideLength,seed:this.maze.seed},geometry:{outerShape:this.maze.outerShape,sideLength:this.maze.sideLength,cellSpacing:this.maze.cellSpacing,cellCount:this.maze.cells.length},rules:{turnDegrees:360/this.maze.config.headings,triangleVertexForward:'blocked',walls:'specular',terminal:'enter_goal_cell'},initial:{cell:this.maze.start,heading:this.maze.initialHeading},goal:this.maze.goal,state:this.state(),assists:[...this.assists],startedAt:new Date(this.startedAt).toISOString(),finishedAt:this.finishedAt===null?null:new Date(this.finishedAt).toISOString(),elapsedMs:(this.finishedAt??Date.now())-this.startedAt,actions:this.actions,maze:this.maze.cells};}
}
