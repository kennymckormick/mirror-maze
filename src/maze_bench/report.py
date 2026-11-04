"""Build English review pages for configured runs, without private streams."""
import argparse
import copy
import json
import shutil
from pathlib import Path
from .common import dump
from . import report_build


def select_cases(root, overrides=None):
    root = Path(root)
    batch = report_build.read(root / 'batch-config.json', {})
    names = batch.get('case_ids') or [p.parent.name for p in sorted(root.glob('*/config.json'))]
    if not names:
        raise ValueError('No case configurations found in ' + str(root))
    return {name: Path(overrides) / name if overrides and (Path(overrides) / name / 'config.json').exists() else root / name for name in names}


def build_study(harness, api, cache, out, repair=None, seed_override=None, legacy=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    modes, sources = {}, {}
    for mode, root, override in [('harness', harness, repair), ('api', api, None)]:
        if not root:
            continue
        paths = select_cases(root, override)
        if mode == 'api' and seed_override:
            paths.update({ident: Path(seed_override) / ident for ident in paths if ident.startswith('seed-') and (Path(seed_override) / ident).exists()})
        sources[mode] = paths
        data = report_build.build(paths, cache, out / mode, mode)
        # The per-mode dataset keeps local image paths; the combined page adds a prefix.
        public = copy.deepcopy(data)
        public['images'] = [mode + '/' + name for name in data['images']]
        modes[mode] = public
    if not modes:
        raise ValueError('Supply --api-dir and/or --harness-dir')
    bundle = {'schema': 'mirror-maze-study/v4', 'modes': modes,
              'study': {'case_count': sum(len(m['cases']) for m in modes.values()),
                        'privacy': 'No native reasoning logs, credentials, or private profiles are included.'}}
    dump(out / 'data.json', bundle)
    for name in ('index.html', 'style.css', 'app.js'):
        shutil.copy2(Path(__file__).with_name('web') / name, out / name)
    return bundle, sources


def main(argv=None):
    p = argparse.ArgumentParser(description='Build an English review site from recorded cases')
    p.add_argument('--harness-dir', type=Path)
    p.add_argument('--api-dir', type=Path)
    p.add_argument('--repair-dir', type=Path)
    p.add_argument('--seed-override', type=Path)
    p.add_argument('--legacy-root', type=Path, help='Accepted for compatibility; recorded configs determine geometry')
    p.add_argument('--cache-dir', type=Path, help='Override the image cache recorded in each case config')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(argv)
    build_study(a.harness_dir, a.api_dir, a.cache_dir, a.out, a.repair_dir, a.seed_override, a.legacy_root)


if __name__ == '__main__':
    main()
