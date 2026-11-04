/** Review map. This canvas is separate from the agent's first-person image. */
export function drawMinimap(canvas,maze,{cell=maze.start,angle=0,visited=[],route=[]}={}){
  const ctx=canvas.getContext('2d'),points=maze.cells.flatMap(c=>c.vertices),W=canvas.width,H=canvas.height;
  const minX=Math.min(...points.map(p=>p[0])),maxX=Math.max(...points.map(p=>p[0])),minY=Math.min(...points.map(p=>p[1])),maxY=Math.max(...points.map(p=>p[1]));
  const scale=(Math.min(W,H)-60)/Math.max(maxX-minX,maxY-minY),seen=new Set(visited);
  const project=p=>[W/2+(p[0]-(minX+maxX)/2)*scale,H/2+(p[1]-(minY+maxY)/2)*scale];ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#101d24';ctx.fillRect(0,0,W,H);
  for(const c of maze.cells){ctx.beginPath();c.vertices.forEach((p,i)=>{const [x,y]=project(p);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.closePath();ctx.fillStyle=seen.has(c.id)?'#294249':'#18272f';ctx.fill();ctx.strokeStyle='#33434a';ctx.lineWidth=.6;ctx.stroke();}
  if(route.length){ctx.beginPath();ctx.moveTo(...project(maze.cells[cell].center));for(const id of route)ctx.lineTo(...project(maze.cells[id].center));ctx.strokeStyle='#b8ec9e';ctx.lineWidth=4;ctx.lineJoin='round';ctx.stroke();}
  ctx.strokeStyle='#d1ded8';ctx.lineWidth=2.2;ctx.lineCap='round';
  for(const c of maze.cells)for(const e of c.edges)if(!e.open&&(e.neighbor<0||e.neighbor>c.id)){ctx.beginPath();ctx.moveTo(...project(e.a));ctx.lineTo(...project(e.b));ctx.stroke();}
  function marker(id,color,r,fill){ctx.beginPath();ctx.arc(...project(maze.cells[id].center),r,0,Math.PI*2);ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=3;fill?ctx.fill():ctx.stroke();}
  marker(maze.start,'#efad62',8,false);marker(maze.goal,'#7ee1b8',10,true);
  const p=project(maze.cells[cell].center);ctx.save();ctx.translate(...p);ctx.rotate(angle);ctx.beginPath();ctx.moveTo(18,0);ctx.lineTo(-11,-11);ctx.lineTo(-5,0);ctx.lineTo(-11,11);ctx.closePath();ctx.fillStyle='#d3f5a8';ctx.strokeStyle='#0d1c20';ctx.lineWidth=3;ctx.fill();ctx.stroke();ctx.restore();
}
