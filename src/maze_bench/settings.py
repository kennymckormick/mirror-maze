"""Episode settings shared by execution, cache identity, prompts and replay."""
import hashlib

SIDES = {'easy': 7, 'medium': 11, 'hard': 15}
BALANCED_SIDES = {'square': SIDES, 'triangle': SIDES, 'hex': {'easy': 5, 'medium': 7, 'hard': 9}}
BALANCED_CAPS = {'easy': 200, 'medium': 400, 'hard': 800}

def upstream_session_key(cfg,role='main'):
    identity=str(cfg.get('run_id','manual'))+'|'+str(cfg.get('case_id',cfg.get('topology','')+'|'+cfg.get('seed','')))
    base=cfg.get('upstream_session_key')or('maze-'+hashlib.sha256(identity.encode()).hexdigest()[:40])
    return base+'-'+role

def geometry(cfg):
    difficulty = cfg.get('difficulty')
    schedule=cfg.get('difficulty_schedule','legacy-v3')
    if schedule not in('legacy-v3','balanced-v1'):raise ValueError('Unknown difficulty schedule')
    topology=cfg.get('topology','square')
    if topology not in BALANCED_SIDES:raise ValueError('Unknown topology')
    sides=BALANCED_SIDES[topology]if schedule=='balanced-v1'else SIDES
    if difficulty is not None and difficulty not in sides:raise ValueError('Unknown difficulty')
    side = cfg.get('side_length', sides.get(difficulty, sides['easy']))
    if isinstance(side, bool) or side not in sides.values():
        raise ValueError('side_length does not belong to the selected topology/difficulty schedule')
    inferred = next(name for name, value in sides.items() if value == side)
    if difficulty is not None and difficulty != inferred:
        raise ValueError('difficulty and side_length disagree')
    return inferred, side

def environment_options(cfg):
    difficulty, side = geometry(cfg)
    return dict(topology=cfg['topology'], seed=cfg['seed'], difficulty=difficulty,
                side_length=side, bounces=10)

def frame_name(cfg, state):
    _, side = geometry(cfg)
    # Preserve frozen easy-run identities. Larger maps need another namespace.
    identity = f'{cfg["topology"]}|{cfg["seed"]}|{state["cell"]}|{state["heading"]}|1280x720|10|0.2.0'
    if side != 7:
        identity += f'|side={side}'
    return hashlib.sha256(identity.encode()).hexdigest() + '.png'

def planning_request(cfg, n, cap, count, feedback):
    from .rules import RULES
    import json
    difficulty, side = geometry(cfg)
    rules = RULES if cap == 200 else RULES.replace('At most200 planning decisions.', f'At most{cap} planning decisions.')
    reminder_from=cfg.get('contract_reminder_from_round')
    repeat_contract=reminder_from is not None and n>=reminder_from
    return ((rules + '\n' if n == 1 or repeat_contract else '') +
            f"\nMAZE_PLANNING_REQUEST {n}/{cap}. Grid={cfg['topology']}, {difficulty} side{side}, turn={90 if cfg['topology']=='square' else 60} degrees. {count} current observation image(s) are attached automatically. Inspect them and return this round's JSON plan.\nExecution feedback: " + json.dumps(feedback))
