"""Model-free geometry, action, and 720p rendering smoke check."""
import argparse
from pathlib import Path
from mirror_maze import MirrorMaze

parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, default=Path('smoke.png'))
args = parser.parse_args()
with MirrorMaze(topology='hex', difficulty='medium', seed='MIRROR-042') as env:
    assert env.info()['geometry']['cell_count'] == 331
    before = env.state()
    preview = env.preview_sequence(['left', 'forward'])
    assert env.state() == before
    feedback = env.perform(['left', 'forward'], stop_on_collision=False)
    env.render(args.out, width=1280, height=720)
    assert env.state()['actionCount'] == 2
    print({'cells': 331, 'actions': 2, 'allExecutable': feedback['allExecutable'], 'image': str(args.out)})
