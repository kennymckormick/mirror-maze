"""Ideal observer/controller references; never a measured human or model score.

The decision bound assumes every new-cell scan is available to the controller.
It is not a guarantee under the benchmark image-pruning policy: an oversized
backtracking-plus-scan batch may retain only its last view. Extra sensing or
decisions can be required in that interface. Numerical references stay frozen.

The exploration policy sees only the current cell's adjacent openings after a
scan, retains their identity perfectly, and recognizes the goal upon entry.
It never uses the hidden goal or unvisited walls to choose a branch. A batch
may backtrack through known passages and enter one new cell, followed by H-1
right turns to inspect all headings. Canonical JS replay verifies samples.
"""
import collections,json,math,random,statistics
from pathlib import Path
from mirror_maze import MirrorMaze

def ports_for(episode):
    h=4 if episode['options']['topology']=='square'else 6;base=0 if h==4 else math.pi/6
    ports={}
    for c in episode['maze']:
        ports[c['id']]={round((math.atan2(e['normal'][1],e['normal'][0])-base)/(2*math.pi/h))%h:e['neighbor']for e in c['edges']if e['open']}
    return ports,h

def explore(episode,trial_seed=0,scan=True,trace=False):
    ports,h=ports_for(episode);rng=random.Random(trial_seed);cell=episode['initial']['cell'];heading=episode['initial']['heading'];goal=episode['goal'];seen={cell};stack=[cell];orders={};actions=[];plans=[];moves=turns=0
    def observe():
        choices=sorted(ports[cell]);rng.shuffle(choices);orders[cell]=choices
    def turn_to(target):
        nonlocal heading,turns
        d=(target-heading)%h
        if d<=h//2:actions.extend(['right']*d);turns+=d
        else:actions.extend(['left']*(h-d));turns+=h-d
        heading=target
    def scan_cell():
        nonlocal heading,turns
        if scan:actions.extend(['right']*(h-1));turns+=h-1;heading=(heading+h-1)%h
        observe()
    scan_cell();plans.append({'begin':0,'end':len(actions),'new_cell':cell})
    while cell!=goal:
        begin=len(actions)
        while True:
            unvisited=[d for d in orders[cell]if ports[cell][d]not in seen]
            if unvisited:
                d=unvisited[0];turn_to(d);cell=ports[cell][d];actions.append('forward');moves+=1;seen.add(cell);stack.append(cell)
                if cell!=goal:scan_cell()
                break
            assert len(stack)>1,'All reachable cells visited without goal'
            parent=stack[-2];d=next(d for d,to in ports[cell].items()if to==parent);turn_to(d);actions.append('forward');moves+=1;cell=parent;stack.pop()
        plans.append({'begin':begin,'end':len(actions),'new_cell':cell})
    result={'trial_seed':trial_seed,'forward_moves':moves,'turn_actions':turns,'primitive_actions':len(actions),'visited_cells':len(seen),'planning_rounds':len(plans),'collisions':0}
    if trace:result.update(actions=actions,plans=plans,final_cell=cell,final_heading=heading)
    return result

def quantile(values,p):
    ordered=sorted(values);return ordered[round((len(ordered)-1)*p)]

def calculate(trials=10000,seed_base=20261103):
    results=[]
    for topology in('square','hex'):
        for seed in('MIRROR-042','MIRROR-043'):
            with MirrorMaze(topology=topology,seed=seed,bounces=10)as env:
                episode=env.export_episode();oracle=env.debug_solution();verified=env.perform(oracle)['state'];assert verified['success']and not verified['collisions']
            ports,h=ports_for(episode);n=len(ports);edges=sum(map(len,ports.values()))//2;d=oracle.count('forward')
            assert edges==n-1 and len(ports[episode['goal']])==1,'Analytic DFS expectation assumes tree with leaf goal'
            with_scan=[explore(episode,seed_base+i,True)for i in range(trials)]
            without_scan=[explore(episode,seed_base+i,False)for i in range(trials)]
            assert all(a['forward_moves']==b['forward_moves']and a['visited_cells']==b['visited_cells']for a,b in zip(with_scan,without_scan))
            assert all(r['forward_moves']<=2*(n-1)-d and r['planning_rounds']<=n for r in with_scan)
            stats={name:{'mean':statistics.mean(r[name]for r in with_scan),'p10':quantile([r[name]for r in with_scan],.1),'p90':quantile([r[name]for r in with_scan],.9),'sample_min':min(r[name]for r in with_scan),'sample_max':max(r[name]for r in with_scan)}for name in('forward_moves','turn_actions','primitive_actions','planning_rounds')}
            selected={0,min(range(trials),key=lambda i:with_scan[i]['primitive_actions']),max(range(trials),key=lambda i:with_scan[i]['primitive_actions'])};replays=[]
            for i in sorted(selected):
                trace=explore(episode,seed_base+i,True,True)
                with MirrorMaze(topology=topology,seed=seed,bounces=10)as env:
                    env.perform(trace['actions']);actual=env.review_state();assert actual['success']and actual['collisions']==0 and actual['actionCount']==trace['primitive_actions']and actual['moves']==trace['forward_moves']
                    assert actual['cell']==trace['final_cell']and actual['heading']==trace['final_heading']
                replays.append({'trial_seed':trace['trial_seed'],'actions':trace['primitive_actions'],'moves':trace['forward_moves'],'planning_rounds':trace['planning_rounds'],'canonical_js_replay_passed':True})
            results.append({'case':topology+'-'+seed,'cells':n,'edges':edges,'goal_is_leaf':True,'oracle':{'primitive_actions':len(oracle),'forward_moves':d,'turn_actions':len(oracle)-d,'planning_rounds_if_map_and_goal_known':1},'unknown_maze_uniform_branch_dfs':{'analytic_expected_forward_moves':n-1,'analytic_min_forward_moves':d,'analytic_max_forward_moves':2*(n-1)-d,'analytic_expected_planning_rounds':(n+d+1)/2,'guaranteed_max_planning_rounds':n,'with_new_cell_scan':stats,'free_all_direction_observation':{'primitive_actions_mean':statistics.mean(r['primitive_actions']for r in without_scan),'primitive_actions_p10':quantile([r['primitive_actions']for r in without_scan],.1),'primitive_actions_p90':quantile([r['primitive_actions']for r in without_scan],.9),'not_same_visual_information_budget':True},'replays':replays,'example_trace':explore(episode,seed_base,True,True)}})
    return{'kind':'idealized_algorithmic_reference_not_human_measurement','information_assumption':'Every new-cell scan is available to a perfect controller. The decision bound is not a verified guarantee under transport image pruning; extra sensing/decisions may be required.','trials_per_case':trials,'seed_base':seed_base,'branch_policy':'uniform random permutation of locally observed open headings at each first visit; no goal-aware branch choice','sensing_policy':'H-1 right-turn actions on initial cell and after entering each non-goal unvisited cell; scans are charged','memory':'perfect cell/heading/observed-wall memory and exact action integration','goal_detection':'only upon actual entry; ignores opportunities to see the beacon from farther away','planning_policy':'initial scan in1decision, then any known-path backtracking +1newcell entry +scan in1decision; host stops at goal','analytic_explanation':'All4graphs are trees and goal is a leaf. A goal-path edge is traversed once; every off-path edge is traversed twice with probability1/2 under uniform branch orders. Expected forward moves=N-1. For visited count V and goal depth d, moves=2(V-1)-d, hence expected decisions=(N+d+1)/2 and at mostN.','cases':results}
