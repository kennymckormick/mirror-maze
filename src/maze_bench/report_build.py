"""Build a sanitized review dataset for arbitrary configured models and maps."""
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path
from PIL import Image
from mirror_maze import MirrorMaze
from .common import dump
from .settings import environment_options, geometry

TERMINAL = {'success', 'planning_cap', 'infrastructure_error'}

def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default

def configuration(case):
    cfg = read(case / 'config.json')
    if cfg:
        return cfg
    spec = read(case.parent / 'batch-config.json', {}).get('spec', {})
    for model in spec.get('models', []):
        for topology in spec.get('topologies', ['square', 'hex']):
            for seed in spec.get('seeds', ['MIRROR-042', 'MIRROR-043']):
                if case.name == f'{model["model_key"]}-{topology}-{seed}':
                    return {**spec.get('defaults', {}), **model, 'topology': topology, 'seed': seed}
    raise ValueError('Cannot determine configuration for case: ' + str(case))

def build(paths, cache, out, mode):
    out = Path(out)
    (out / 'images').mkdir(parents=True, exist_ok=True)
    configs = {ident: configuration(Path(path)) for ident, path in paths.items()}
    maps, cases, images, ids, hashes, rows = {}, [], [], {}, {}, []
    model_order = list(dict.fromkeys(c['model_key'] for c in configs.values()))
    grid_order = []
    for ident, cfg in configs.items():
        _, side = geometry(cfg)
        key = f'{cfg["topology"]}-{cfg["seed"]}' + (f'-side{side}' if side != 7 else '')
        if key in maps:
            continue
        with MirrorMaze(**environment_options(cfg)) as env:
            snap = env.export_episode()
            solution = env.debug_solution()
            env.perform(solution, stop_on_collision=False)
            trace = env.export_episode()
        maps[key] = {'actions': len(solution), 'moves': solution.count('forward'),
                     'route': [snap['initial']['cell']] + [a['after']['cell'] for a in trace['actions'] if a['moved']],
                     'maze': snap['maze'], 'initial': snap['initial'], 'goal': snap['goal'],
                     'geometry': snap['geometry'], 'topology': cfg['topology'], 'seed': cfg['seed'], 'sideLength': side}
        grid_order.append(key)

    def image(name, cfg):
        if not name:
            return None
        source = Path(cache or cfg.get('cache_dir', 'image-cache')) / name
        if not source.is_file():
            return None
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        hashes[name] = digest
        if digest in ids:
            return ids[digest]
        with Image.open(source) as im:
            if im.size != (1280, 720):
                raise ValueError('Input image is not 1280x720: ' + source.name)
            target = out / 'images' / (digest + '.jpg')
            if not target.exists():
                im.convert('RGB').save(target, 'JPEG', quality=65, optimize=True)
        index = len(images)
        ids[digest] = index
        hashes[name] = digest
        images.append('images/' + target.name)
        return index

    for ident, path in paths.items():
        path = Path(path)
        cfg = configs[ident]
        progress, result, episode = (read(path / n, {}) for n in ('progress.json', 'result.json', 'episode.json'))
        wire = read(path / 'wire.json', [])
        events = progress.get('events', [])
        status = result.get('status', progress.get('status', 'pending'))
        if status not in TERMINAL | {'running', 'pending'}:
            status = 'pending'
        _, side = geometry(cfg)
        key = f'{cfg["topology"]}-{cfg["seed"]}' + (f'-side{side}' if side != 7 else '')
        st = episode.get('state', progress.get('state', {}))
        initial = result.get('initial_frame') or (events[0]['input_frames'][0] if events else None)
        if initial is None:
            frames = read(path / 'feed.json', {}).get('frames', [])
            initial = frames[0].get('file') if frames else None
        c = {'id': ident, 'model': cfg['model_key'], 'label': cfg.get('display_name', cfg['model_label']),
             'harness': cfg.get('harness') or 'api', 'source': cfg.get('source_alias'),
             'map': key, 'topology': cfg['topology'], 'seed': cfg['seed'], 'sideLength': side,
             'planningCap': cfg.get('max_planning', 200), 'status': status,
             'final': status in TERMINAL, 'success': status == 'success', 'planning': len(events),
             'collisions': progress.get('total_collisions', result.get('collisions', 0)),
             'invalid': progress.get('invalid_sequences', 0), 'actions': st.get('actionCount', 0),
             'moves': st.get('moves', 0), 'visited': st.get('visited', 0),
             'seconds': result.get('seconds', progress.get('elapsed_seconds', 0)),
             'error': 'Interrupted; inspect the local error record.' if result.get('error') or progress.get('error') else None,
             'initialImage': image(initial, cfg), 'rounds': [], 'steps': [],
             'apiProtocol': cfg.get('api_protocol'), 'requestParams': cfg.get('request_params', {}),
             'effort': cfg.get('effort', 'native/provider default'),
             'timeouts': {k: cfg.get(k) for k in ('request_timeout', 'planning_timeout', 'harness_timeout')},
             'apiCalls': len(wire), 'visionCalls': sum(bool(w.get('images')) for w in wire),
             'reportedModels': sorted({m for w in wire for m in w.get('reported_models', [])}),
             'httpErrors': dict(collections.Counter(str(w.get('http_status')) for w in wire if w.get('http_status') not in (None, 200))),
             'audit': {'rounds': [], 'resolutionViolations': [], 'allImagesMatched': True}}
        for w in wire:
            for im in w.get('images', []):
                if (im.get('width'), im.get('height')) != (1280, 720):
                    c['audit']['resolutionViolations'].append({'call': w['index'], 'size': [im.get('width'), im.get('height')]})
        for e in events:
            begin = len(c['steps'])
            for step in e.get('steps', []):
                state = step.get('state', e['after'])
                r = step.get('result', {})
                c['steps'].append([step.get('action'), state['cell'], state['heading'], image(step.get('frame'), cfg),
                                   bool(r.get('collision')), r.get('reason'), e['round']])
            sent = e.get('input_frames_sent', e.get('input_frames', []))
            expected = []
            for name in sent:
                image(name, cfg)
                if name in hashes:
                    expected.append(hashes[name])
            matched = next((w['index'] for w in wire if w.get('http_status') == 200 and w.get('observation_round') == e['round'] and expected and [im.get('sha256') for im in w.get('images', [])] == expected), None)
            ok = matched is not None
            c['audit']['rounds'].append({'round': e['round'], 'expectedImages': len(expected), 'matchedNativeRequest': matched, 'matched': ok})
            c['audit']['allImagesMatched'] &= ok
            c['rounds'].append({'round': e['round'], 'plan': str(e.get('plan', ''))[:1200], 'submitted': len(e.get('actions') or []),
                                'actions': e.get('actions'), 'begin': begin, 'end': len(c['steps']), 'invalid': e.get('invalid', False),
                                'invalidReason': e.get('invalid_reason'), 'seconds': e.get('seconds'),
                                'inputImages': len(e.get('input_frames', [])), 'keptImages': len(sent),
                                'trimEvents': e.get('image_trim_events', [])})
        c['observationValid'] = bool(events) and not c['audit']['resolutionViolations'] and c['audit']['allImagesMatched']
        c['forwardAttempts'] = sum(s.get('action') == 'forward' for e in events for s in e['steps'])
        c['longestSequence'] = max((len(e.get('actions') or []) for e in events), default=0)
        c['averageSequence'] = sum(len(e.get('actions') or []) for e in events) / len(events) if events else 0
        c['trimmedPlanningRounds'] = sum(bool(e.get('image_trim_events')) for e in events)
        cases.append(c)
        rows.append({k: c[k] for k in ('id', 'label', 'harness', 'source', 'topology', 'seed', 'sideLength', 'planningCap', 'status', 'planning', 'actions', 'moves', 'collisions', 'invalid', 'seconds', 'apiCalls', 'visionCalls', 'observationValid')})
    result = {'schema': 'mirror-maze-review/v4', 'mode': mode, 'generatedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'environmentVersion': '0.2.0', 'complete': all(c['final'] for c in cases), 'cases': cases, 'maps': maps,
              'images': images, 'modelOrder': model_order, 'mapOrder': grid_order,
              'sources': {m: sorted({c['source'] for c in cases if c['model'] == m and c['source']}) for m in model_order},
              'protocol': {'cases': len(cases), 'maxPlanning': max(c['planningCap'] for c in cases), 'resolution': [1280, 720],
                           'reflections': 10, 'sideLengths': sorted({c['sideLength'] for c in cases}), 'sequences': 'unbounded',
                           'imagePruning': 'last-batch images; oldest round first; latest current view pinned',
                           'collisionExecution': 'continue', 'invalid': 'reject entire sequence, one collision'},
              'privacy': 'Public plan intentions and review geometry only; no provider reasoning streams or credentials.'}
    dump(out / 'data.json', result)
    dump(out / 'image-hashes.json', hashes)
    with (out / 'cases.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return result
