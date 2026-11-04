const vertex = `#version 300 es
in vec2 aPosition;
void main(){gl_Position=vec4(aPosition,0.,1.);}`;
const fragment = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
out vec4 outColor;
uniform vec2 uResolution;
uniform sampler2D uCells;
uniform int uSides, uCell, uPlayerCell, uGoal, uStart, uBounces, uMirror;
uniform vec2 uCamera, uPlayer;
uniform float uAngle, uPlayerAngle;
const float HEIGHT=2.9;
vec4 edgeData(int cell,int edge){return texelFetch(uCells,ivec2(edge*2,cell),0);}
vec4 meta(int cell){return texelFetch(uCells,ivec2(12,cell),0);}
float cross2(vec2 a,vec2 b){return a.x*b.y-a.y*b.x;}
float boxHit(vec3 ro,vec3 rd,vec3 center,vec3 halfSize,out vec3 norm){
  vec3 r=ro-center, inv=1./(rd+vec3(1e-9)),a=(-halfSize-r)*inv,b=(halfSize-r)*inv;
  vec3 lo=min(a,b),hi=max(a,b);float nearT=max(max(lo.x,lo.y),lo.z),farT=min(min(hi.x,hi.y),hi.z);
  if(nearT<.0001||nearT>farT)return 1e6;
  norm=vec3(0.);if(lo.x>=lo.y&&lo.x>=lo.z)norm.x=-sign(rd.x);else if(lo.y>=lo.z)norm.y=-sign(rd.y);else norm.z=-sign(rd.z);return nearT;
}
void considerBox(vec3 ro,vec3 rd,vec3 c,vec3 s,vec3 base,inout float t,inout vec3 col){
  vec3 n;float v=boxHit(ro,rd,c,s,n);
  if(v<t){t=v;float lighting=.58+.42*max(dot(n,normalize(vec3(.6,.9,.35))),0.);col=base*lighting;}
}
float avatar(vec3 ro,vec3 rd,out vec3 color){
  vec2 f=vec2(cos(uPlayerAngle),sin(uPlayerAngle)),right=vec2(-f.y,f.x);
  vec2 p=ro.xz-uPlayer,d=rd.xz;
  vec3 o=vec3(dot(p,f),ro.y,dot(p,right)),v=vec3(dot(d,f),rd.y,dot(d,right));
  float t=1e6;vec3 yellow=vec3(1.,.64,.13),blue=vec3(.12,.39,.68),n;
  considerBox(o,v,vec3(0.,.82,0.),vec3(.23,.35,.29),blue,t,color);
  considerBox(o,v,vec3(.242,.83,0.),vec3(.018,.22,.24),yellow,t,color);
  considerBox(o,v,vec3(0.,1.43,0.),vec3(.24,.23,.26),vec3(.84,.88,.9),t,color);
  considerBox(o,v,vec3(.249,1.44,0.),vec3(.014,.12,.21),vec3(.045,.1,.13),t,color);
  considerBox(o,v,vec3(.268,1.46,.105),vec3(.008,.035,.04),yellow,t,color);
  considerBox(o,v,vec3(.268,1.46,-.105),vec3(.008,.035,.04),yellow,t,color);
  considerBox(o,v,vec3(0.,.78,.41),vec3(.16,.3,.095),yellow,t,color);
  considerBox(o,v,vec3(0.,.78,-.41),vec3(.16,.3,.095),yellow,t,color);
  considerBox(o,v,vec3(0.,.25,.16),vec3(.17,.23,.10),vec3(.16,.22,.28),t,color);
  considerBox(o,v,vec3(0.,.25,-.16),vec3(.17,.23,.10),vec3(.16,.22,.28),t,color);
  // Flat arrow on top of the head, pointing along the player's true forward vector.
  if(abs(v.y)>.0001){float at=(1.68-o.y)/v.y;vec2 p=(o+v*at).xz;
    if(at>.0001&&at<t&&p.x>-.18&&p.x<.48&&abs(p.y)<(.48-p.x)*.43){t=at;color=yellow;}}
  return t;
}
vec3 floorColor(vec3 p,int cell){
  vec4 m=meta(cell);vec2 q=p.xz-m.xy;
  float border=100.;for(int i=0;i<6;i++){if(i>=uSides)break;vec4 e=edgeData(cell,i);border=min(border,e.z-dot(e.xy,p.xz));}
  float check=mod(floor(p.x*.7)+floor(p.z*.7),2.);
  vec3 color=mix(vec3(.24,.29,.31),vec3(.28,.33,.35),check);
  // Subtle tile seams keep the three tilings visible without revealing a map.
  color*=mix(.55,1.,smoothstep(.012,.045,border));
  float r=length(q);
  if(cell==uStart){if(r<.64&&r>.56)color=vec3(1.,.55,.15);if(abs(q.x)<.12&&abs(q.y)<.12)color=vec3(1.,.63,.22);}
  if(cell==uGoal){if(r<.94&&r>.78)color=vec3(.2,1.,.68);if(r<.6)color=mix(color,vec3(.18,.65,.46),.5);}
  if(m.z>.5&&r<.5){int id=int(m.z);bool shape=id==1?r>.34:(id==2?abs(q.x)+abs(q.y)<.47:max(abs(q.x),abs(q.y))<.31);if(shape)color=id==1?vec3(.37,.79,.96):id==2?vec3(.95,.37,.39):vec3(.8,.55,.98);}
  return color*(.82+.18*exp(-r*.2));
}
vec3 trace(vec3 ro,vec3 rd){
  int cell=uCell,bounces=0;vec3 throughput=vec3(1.);float distanceTravelled=0.;
  for(int iteration=0;iteration<160;iteration++){
    float best=1e6;int edge=-1;vec4 hitEdge=vec4(0.);
    for(int i=0;i<6;i++){if(i>=uSides)break;vec4 e=edgeData(cell,i);float den=dot(e.xy,rd.xz);if(den>.000001){float t=(e.z-dot(e.xy,ro.xz))/den;if(t>.00001&&t<best){best=t;edge=i;hitEdge=e;}}}
    float plane=1e6;bool floorHit=rd.y<0.;if(abs(rd.y)>.000001)plane=((floorHit?0.:HEIGHT)-ro.y)/rd.y;
    bool planeFirst=plane>.00001&&plane<best;if(planeFirst)best=plane;
    vec3 objColor;float objectT=1e6;
    if(bounces>0&&cell==uPlayerCell)objectT=avatar(ro,rd,objColor);
    if(cell==uGoal){vec3 n;vec2 g=meta(cell).xy;float t=boxHit(ro,rd,vec3(g.x,.92,g.y),vec3(.14,.83,.14),n);if(t<objectT){objectT=t;objColor=vec3(.22,.95,.64)*(.7+.3*abs(n.y));}}
    if(objectT<best)return throughput*objColor;
    if(best>1e5)return throughput*vec3(.07,.12,.15);
    vec3 p=ro+rd*best;distanceTravelled+=best;
    if(planeFirst){
      vec3 color;
      if(floorHit)color=floorColor(p,cell);else{
        vec2 q=p.xz-meta(cell).xy;float r=length(q);
        bool light=(abs(q.x)<.5&&abs(q.y)<.06)||(abs(q.y)<.5&&abs(q.x)<.06);
        color=light?vec3(.9,.98,1.):vec3(.18,.23,.26)*(1.-.12*smoothstep(.2,2.,r));
      }
      return throughput*color;
    }
    if(edge<0)return throughput*vec3(.07,.12,.15);
    if(hitEdge.w>=0.){cell=int(hitEdge.w+.5);ro=p+rd*.00015;continue;}
    vec4 ends=texelFetch(uCells,ivec2(edge*2+1,cell),0);float along=length(p.xz-ends.xy),len=length(ends.zw-ends.xy);
    bool vertical=min(along,len-along)<.045, horizontal=p.y<.075||p.y>HEIGHT-.075;
    if(vertical||horizontal){vec3 frame=vec3(.08,.14,.17);if((p.y>.10&&p.y<.13)||(p.y>HEIGHT-.13&&p.y<HEIGHT-.10))frame=vec3(.36,.75,.78);return throughput*frame;}
    if(uMirror==0){float panel=mod(floor(along*.7)+floor(p.y*.7),2.);return throughput*mix(vec3(.22,.34,.37),vec3(.26,.39,.41),panel);}
    if(bounces>=uBounces)return throughput*vec3(.065,.10,.13);
    rd=reflect(rd,vec3(hitEdge.x,0.,hitEdge.y));ro=p+rd*.00015;bounces++;
    throughput*=vec3(.95,.975,.985);
  }
  return throughput*vec3(.065,.10,.13);
}
void main(){
  vec2 uv=(gl_FragCoord.xy-.5*uResolution)/uResolution.y;
  vec2 f=vec2(cos(uAngle),sin(uAngle)),right=vec2(-f.y,f.x);
  vec3 forward=normalize(vec3(f.x,-.12,f.y));vec3 right3=vec3(right.x,0.,right.y);vec3 up=cross(right3,forward);
  vec3 rd=normalize(forward+uv.x*right3*1.3+uv.y*up*1.3);
  vec3 color=trace(vec3(uCamera.x,1.47,uCamera.y),rd);
  // Gentle display transfer; no stochastic sampling or temporal noise.
  color=pow(max(color,vec3(0.)),vec3(.82));
  outColor=vec4(color,1.);
}`;
export class MirrorRenderer {
  constructor(canvas){
    this.canvas=canvas;this.gl=canvas.getContext('webgl2',{antialias:false,alpha:false,preserveDrawingBuffer:true,powerPreference:'high-performance'});
    if(!this.gl)throw new Error('WebGL 2 is unavailable. Use Chrome, Edge, or Safari with hardware acceleration enabled.');
    const gl=this.gl;this.timer=gl.getExtension('EXT_disjoint_timer_query_webgl2');this.pending=[];this.metrics={cpuMs:0,gpuMs:null,frames:0,width:0,height:0};
    const compile=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));return s;};
    this.program=gl.createProgram();gl.attachShader(this.program,compile(gl.VERTEX_SHADER,vertex));gl.attachShader(this.program,compile(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(this.program));
    gl.useProgram(this.program);const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);const loc=gl.getAttribLocation(this.program,'aPosition');gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
    this.locations={};for(const n of ['uResolution','uCells','uSides','uCell','uPlayerCell','uGoal','uStart','uBounces','uMirror','uCamera','uPlayer','uAngle','uPlayerAngle'])this.locations[n]=gl.getUniformLocation(this.program,n);
    this.texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,this.texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  }
  setMaze(maze){
    this.maze=maze;const data=new Float32Array(13*maze.cells.length*4);
    for(const c of maze.cells){for(let i=0;i<c.edges.length;i++){const e=c.edges[i],at=(c.id*13+i*2)*4;data.set([e.normal[0],e.normal[1],e.normal[0]*e.a[0]+e.normal[1]*e.a[1],e.open?e.neighbor:-1],at);data.set([...e.a,...e.b],at+4);}data.set([...c.center,c.landmark,0],(c.id*13+12)*4);}
    const gl=this.gl;gl.bindTexture(gl.TEXTURE_2D,this.texture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA32F,13,maze.cells.length,0,gl.RGBA,gl.FLOAT,data);
  }
  resize(quality='balanced'){
    const rect=this.canvas.getBoundingClientRect(),target={eco:640,balanced:960,sharp:1440}[quality]??960;
    const width=Math.max(320,Math.round(Math.min(rect.width*(window.devicePixelRatio||1),target))),height=Math.max(200,Math.round(width/(rect.width/rect.height||1.6)));
    if(this.canvas.width!==width||this.canvas.height!==height){this.canvas.width=width;this.canvas.height=height;}
    this.gl.viewport(0,0,width,height);Object.assign(this.metrics,{width,height});
  }
  setResolution(width,height){
    if(!Number.isInteger(width)||!Number.isInteger(height)||width<64||height<64||width>4096||height>4096)throw new Error('Image dimensions must be integers between 64 and 4096');
    this.canvas.width=width;this.canvas.height=height;this.gl.viewport(0,0,width,height);Object.assign(this.metrics,{width,height});
  }
  render({cell,position,angle,playerCell=cell,playerPosition=position,playerAngle=angle,bounces=10,mirror=true}){
    const gl=this.gl,L=this.locations,start=performance.now();gl.useProgram(this.program);
    for(const q of this.pending){if(gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE)){if(!gl.getParameter(this.timer.GPU_DISJOINT_EXT))this.metrics.gpuMs=gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6;gl.deleteQuery(q);q.done=true;}}
    this.pending=this.pending.filter(q=>!q.done);
    let query=null;if(this.timer&&this.pending.length<3){query=gl.createQuery();gl.beginQuery(this.timer.TIME_ELAPSED_EXT,query);}
    gl.uniform2f(L.uResolution,this.canvas.width,this.canvas.height);gl.uniform1i(L.uCells,0);gl.uniform1i(L.uSides,this.maze.config.sides);gl.uniform1i(L.uCell,cell);gl.uniform1i(L.uPlayerCell,playerCell);gl.uniform1i(L.uGoal,this.maze.goal);gl.uniform1i(L.uStart,this.maze.start);gl.uniform1i(L.uBounces,bounces);gl.uniform1i(L.uMirror,mirror?1:0);gl.uniform2fv(L.uCamera,position);gl.uniform2fv(L.uPlayer,playerPosition);gl.uniform1f(L.uAngle,angle);gl.uniform1f(L.uPlayerAngle,playerAngle);gl.drawArrays(gl.TRIANGLES,0,3);
    if(query){gl.endQuery(this.timer.TIME_ELAPSED_EXT);this.pending.push(query);}
    this.metrics.cpuMs=performance.now()-start;this.metrics.frames++;return {...this.metrics};
  }
  image(){return this.canvas.toDataURL('image/png');}
}
