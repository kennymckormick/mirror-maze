/** State-blind one-decision protocol stress test. No model calls or images. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';

const SCRIPT=path.dirname(fileURLToPath(import.meta.url));
const ROOT=path.resolve(SCRIPT,'..');
const args=process.argv.slice(2),option=name=>{const i=args.indexOf(name);return i<0?null:args[i+1];};
if(args.includes('--help')){console.log('Usage: node --expose-gc experiments/random_policy.mjs [--out DIRECTORY] [--rules PATH_TO_MAZE_JS]\nFixed protocol:500 trials, seeds20261104..20261603, budgets1000/10000/100000. No model calls.');process.exit(0);}
const OUT=path.resolve(option('--out')||path.join(ROOT,'runs/random-policy'));fs.mkdirSync(OUT,{recursive:true});
const rulesFile=path.resolve(option('--rules')||path.join(ROOT,'src/mirror_maze/assets/maze.js'));
const {Episode}=await import(pathToFileURL(rulesFile).href);
const seedBase=20261104,trials=500,budgets=[1000,10000,100000];
const names=['left','right','forward'];
const maps=[];
for(const difficulty of ['easy','medium'])for(const topology of ['square','hex'])for(const seed of ['MIRROR-042','MIRROR-043'])maps.push({id:`${difficulty}/${topology}-${seed}`,difficulty,topology,seed,sideLength:difficulty==='easy'?7:11});
const protocol={kind:'state_blind_one_decision_protocol_stress_test',preregistered_at:new Date().toISOString(),trials,trial_seed_base:seedBase,trial_seed_last:seedBase+trials-1,action_budgets:budgets,action_space:names,action_distribution:'pseudorandom uniform left/right/forward via xorshift32 and rejection-to-range3',generator:'xorshift32: x^=x<<13;x^=x>>>17;x^=x<<5; reject4294967295; value%3',same_sequence_across_maps:true,sequence_pregenerated_before_simulation:true,policy_reads_state:false,policy_reads_images:false,policy_reads_goal:false,host_stops_at_actual_goal:true,planning_decisions_per_trial:1,no_token_or_compute_matching:true,model_results_modified:false,rules_sha256:crypto.createHash('sha256').update(fs.readFileSync(rulesFile)).digest('hex'),maps};
const protocolFile=path.join(OUT,'random-policy-protocol.json');
if(fs.existsSync(protocolFile)){
 const prior=JSON.parse(fs.readFileSync(protocolFile));
 for(const key of ['trials','trial_seed_base','trial_seed_last','action_budgets','rules_sha256','maps'])if(JSON.stringify(prior[key])!==JSON.stringify(protocol[key]))throw Error('Existing preregistration differs: '+key);
 protocol.preregistered_at=prior.preregistered_at;
}else fs.writeFileSync(protocolFile,JSON.stringify(protocol,null,2)+'\n');
const protocolSha=crypto.createHash('sha256').update(fs.readFileSync(protocolFile)).digest('hex');

function sequence(seed){
 let x=seed>>>0;if(!x)throw Error('Nonzero seed required');
 const a=new Uint8Array(budgets.at(-1));
 for(let i=0;i<a.length;i++){
  let value;
  do{x^=x<<13;x^=x>>>17;x^=x<<5;value=x>>>0;}while(value===4294967295);
  a[i]=value%3;
 }
 return a;
}
function compile(options){
 const ep=new Episode(options),H=ep.maze.config.headings,N=ep.maze.cells.length,states=N*H;
 const next=new Uint32Array(states*3),hit=new Uint8Array(states*3),move=new Uint8Array(states*3),goal=ep.maze.goal;
 for(let cell=0;cell<N;cell++)for(let h=0;h<H;h++)for(let a=0;a<3;a++){
  const index=(cell*H+h)*3+a;
  if(cell===goal){next[index]=cell*H+h;continue;}
  ep.cell=cell;ep.heading=h;ep.actions=[];ep.visited=new Set([cell]);ep.collisions=0;ep.moveCount=0;ep.finishedAt=null;
  const r=ep.step(names[a]);next[index]=r.cell*H+r.heading;hit[index]=r.collision?1:0;move[index]=r.moved?1:0;
 }
 const initial=new Episode(options);const route=initial.shortestActions();
 return {options,H,N,next,hit,move,goal,start:initial.cell*H+initial.heading,oracle_actions:route.length,oracle_moves:route.filter(a=>a==='forward').length};
}
const tables=maps.map(compile);
const results=maps.map(m=>({map:m.id,trial_results:[]}));
const sampleSequences={};
for(let trial=0;trial<trials;trial++){
 const seed=seedBase+trial;const a=sequence(seed);
 if(trial===0)sampleSequences[seed]=crypto.createHash('sha256').update(a).digest('hex');
 for(let mi=0;mi<tables.length;mi++){
  const t=tables[mi];let pose=t.start,collisions=0,moves=0,visitedCount=1,firstHit=null,bi=0;
  const visited=new Uint8Array(t.N);visited[Math.floor(pose/t.H)]=1;
  const checks=[];
  for(let i=0;i<a.length;i++){
   const index=pose*3+a[i];pose=t.next[index];collisions+=t.hit[index];moves+=t.move[index];
   const cell=Math.floor(pose/t.H);if(!visited[cell]){visited[cell]=1;visitedCount++;}
   const solved=cell===t.goal,actions=i+1;
   if(solved)firstHit=actions;
   if(actions===budgets[bi]||solved){
    checks.push({budget:budgets[bi],success:solved,executed_actions:actions,collisions,moves,visited:visitedCount,final_cell:cell,final_heading:pose%t.H});bi++;
    if(solved){while(bi<budgets.length){checks.push({...checks.at(-1),budget:budgets[bi]});bi++;}break;}
   }
  }
  if(checks.length!==budgets.length)throw Error('Missing budget record');
  results[mi].trial_results.push({trial_seed:seed,first_goal_action:firstHit,checkpoints:checks});
 }
}
function mean(xs){return xs.reduce((a,b)=>a+b,0)/xs.length;}
function median(xs){const a=xs.slice().sort((a,b)=>a-b);const m=Math.floor(a.length/2);return a.length%2?a[m]:(a[m-1]+a[m])/2;}
function wilson(k,n){const z=1.959963984540054,p=k/n,den=1+z*z/n,c=(p+z*z/(2*n))/den,h=z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den;return[Math.max(0,c-h),Math.min(1,c+h)];}
const aggregates=[];
for(let mi=0;mi<results.length;mi++)for(const budget of budgets){
 const rows=results[mi].trial_results.map(r=>r.checkpoints.find(c=>c.budget===budget));const success=rows.filter(r=>r.success);const t=tables[mi];
 aggregates.push({map:maps[mi].id,difficulty:maps[mi].difficulty,topology:maps[mi].topology,seed:maps[mi].seed,cells:t.N,budget,trials,solved:success.length,solve_fraction:success.length/trials,wilson95: wilson(success.length,trials),mean_executed_actions:mean(rows.map(r=>r.executed_actions)),mean_collisions:mean(rows.map(r=>r.collisions)),mean_moves:mean(rows.map(r=>r.moves)),mean_coverage:mean(rows.map(r=>r.visited/t.N)),mean_actions_if_solved:success.length?mean(success.map(r=>r.executed_actions)):null,median_actions_if_solved:success.length?median(success.map(r=>r.executed_actions)):null,mean_collisions_if_solved:success.length?mean(success.map(r=>r.collisions)):null,oracle_actions:t.oracle_actions,oracle_moves:t.oracle_moves});
}
const mapSetEvents=[];
for(const difficulty of ['easy','medium'])for(const budget of budgets){
 const indices=maps.map((m,i)=>m.difficulty===difficulty?i:-1).filter(i=>i>=0);
 const solvedCounts=Array.from({length:trials},(_,trial)=>indices.reduce((n,mi)=>n+Number(results[mi].trial_results[trial].checkpoints.find(c=>c.budget===budget).success),0));
 mapSetEvents.push({difficulty,budget,trials,maps_per_trial:indices.length,mean_cases_solved:mean(solvedCounts),trial_fraction_solving_all_maps:solvedCounts.filter(n=>n===indices.length).length/trials,solved_count_histogram:Object.fromEntries(Array.from({length:indices.length+1},(_,n)=>[n,solvedCounts.filter(v=>v===n).length]))});
}

// Replay the first two preregistered trials plus first solved/unsolved trials.
// Inference never calls these verification routines; they run only after the
// complete action sequences and all Monte Carlo statistics have been fixed.
const verification=[];
for(let mi=0;mi<maps.length;mi++){
 const available=results[mi].trial_results;const indices=new Set([0,1]);const solved=available.findIndex(r=>r.first_goal_action!==null),unsolved=available.findIndex(r=>r.first_goal_action===null);if(solved>=0)indices.add(solved);if(unsolved>=0)indices.add(unsolved);
 for(const trial of indices){
  const record=available[trial],a=sequence(record.trial_seed),ep=new Episode(maps[mi]);
  for(const expected of record.checkpoints){
   while(ep.state().actionCount<expected.executed_actions){ep.step(names[a[ep.state().actionCount]]);if(ep.state().success)break;}
   const s=ep.state();
   for(const[k,v]of Object.entries({success:expected.success,actionCount:expected.executed_actions,collisions:expected.collisions,moves:expected.moves,visited:expected.visited,cell:expected.final_cell,heading:expected.final_heading}))if(s[k]!==v)throw Error(`Replay mismatch ${maps[mi].id} trial${trial} budget${expected.budget} ${k}: ${s[k]} != ${v}`);
   verification.push({map:maps[mi].id,trial_seed:record.trial_seed,budget:expected.budget,actions:s.actionCount,success:s.success,collisions:s.collisions,canonical_replay:true});
  }
  if(globalThis.gc)globalThis.gc();
 }
}
const output={protocol,protocol_sha256:protocolSha,computed_at:new Date().toISOString(),sample_sequence_sha256:sampleSequences,aggregates,map_set_events:mapSetEvents,verification,trials:results,interpretation:['A single pre-generated action sequence is reused unchanged across all maps for each trial; the action policy has no state, image or goal access.','This is a protocol stress test implemented in code, not a model or human evaluation; it is not token-, latency- or compute-matched to model runs.','Solve fractions are conditional on these fixed maps and this stochastic generator. They are not estimates for a representative maze distribution.','Under the current unbounded sequence rule, one planning decision can buy many physical attempts. A high solve fraction at huge action cost does not show spatial understanding.','All model and harness results remain frozen; no planning budget or score has been changed.']};
fs.writeFileSync(path.join(OUT,'random-policy-results.json.tmp'),JSON.stringify(output)+'\n');fs.renameSync(path.join(OUT,'random-policy-results.json.tmp'),path.join(OUT,'random-policy-results.json'));
console.log(JSON.stringify({trials,maps:maps.length,budgets,canonical_replays:verification.length,aggregates:aggregates.map(a=>({map:a.map,budget:a.budget,solved:a.solved,solve_fraction:a.solve_fraction,mean_actions:a.mean_executed_actions,mean_collisions:a.mean_collisions})),map_set_events:mapSetEvents},null,2));
