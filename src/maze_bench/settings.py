"""Episode settings shared by execution, cache identity, prompts and replay."""
import hashlib

SIDES = {'easy': 7, 'medium': 11, 'hard': 15}

def geometry(cfg):
    difficulty = cfg.get('difficulty')
    side = cfg.get('side_length', SIDES.get(difficulty, 7))
    if isinstance(side, bool) or side not in SIDES.values():
        raise ValueError('side_length must be 7, 11 or 15')
    inferred = next(name for name, value in SIDES.items() if value == side)
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
    return ((rules + '\n' if n == 1 else '') +
            f"\nMAZE_PLANNING_REQUEST {n}/{cap}. Grid={cfg['topology']}, {difficulty} side{side}, turn={90 if cfg['topology']=='square' else 60} degrees. {count} current observation image(s) are attached automatically. Inspect them and return this round's JSON plan.\nExecution feedback: " + json.dumps(feedback))
