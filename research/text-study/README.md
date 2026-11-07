# Mirror Maze text-only study

The [project page](http://vlmeval.byted.org/reports/view/rzAw-KSgopFj/text/) presents the 32 public Easy and Medium cases and their matched visual runs. The JSON files contain audited public text trajectories and paired terminal counters. The release excludes model reasoning and raw client or provider traffic.

Run a fresh text campaign using the root README and `examples/text-native.json`. The protocol has a 200-round Easy cap and a 400-round Medium cap. Each planning call may contain multiple actions. The host returns ordered execution outcomes and a sequence-rejected flag. The test client receives no images, map or coordinates. Each model's four Easy trials must resolve before Medium can launch. An audited Easy pass permits Medium. Four audited Easy caps produce four separately audited skips.

`export_text_release.py` regenerates the audited public text JSON from a completed canonical campaign and the site's map data. It also emits a separate private audit receipt. Run `python3 export_text_release.py --help` for the inputs and outputs.

## Regenerate the case clips

The media recipe requires Python 3.9+, Pillow, FFmpeg with libx264, and Lato Regular and Bold. Run these commands in the `media` directory.

```sh
python3 render_comparisons.py --input comparison-input.json --out release --workers 2
python3 verify_media.py --root release --input comparison-input.json --contact-dir review
```

The render input includes exact map geometry, paths, round-end counters and render settings for 64 public trajectories. The manifest records the published media digests. FFmpeg builds and fonts can change the encoded bytes across machines. The files use a common planning-round clock and reviewer maps. Wall hits count physical collisions. Invalid sequences are tracked separately.
