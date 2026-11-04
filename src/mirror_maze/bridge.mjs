import readline from 'node:readline';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const { Episode, VERSION } = await import(pathToFileURL(path.join(process.argv[2],'maze.js')).href);
let episode;
const publicState = state => Object.fromEntries(['success','actionCount','moves','collisions'].map(k=>[k,state[k]]));
const publicResult = r => ({...Object.fromEntries(['accepted','action','moved','collision','collisionType','reason'].map(k=>[k,r[k]])),...publicState(r)});
const info=()=>({version:VERSION,options:{topology:episode.maze.topology,difficulty:episode.maze.difficulty,side_length:episode.maze.sideLength,seed:episode.maze.seed},geometry:{outer_shape:episode.maze.outerShape,cell_count:episode.maze.cells.length,cell_spacing:episode.maze.cellSpacing},turn_degrees:360/episode.maze.config.headings,actions:['left','right','forward'],state:publicState(episode.state())});
const lines=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
for await(const line of lines){
  let request;
  try{
    request=JSON.parse(line);let result;
    if(request.method==='initialize'){episode=new Episode(request.params??{});result=info();}
    else{
      if(!episode)throw new Error('Call initialize first');
      if(request.method==='perform'){
        const p=request.params??{},raw=episode.perform(p.actions,{dryRun:p.dryRun??false,stopOnCollision:p.stopOnCollision??true});
        result={...raw,results:raw.results.map(publicResult),state:publicState(raw.state)};
      }else if(request.method==='state')result=publicState(episode.state());
      else if(request.method==='info')result=info();
      else if(request.method==='export')result=request.params?.stateOnly?episode.state():episode.export();
      else if(request.method==='solution')result=episode.shortestActions();
      else throw new Error('Unknown method: '+request.method);
    }
    process.stdout.write(JSON.stringify({id:request.id,ok:true,result})+'\n');
  }catch(error){process.stdout.write(JSON.stringify({id:request?.id??null,ok:false,error:{type:error.name,message:error.message}})+'\n');}
}
