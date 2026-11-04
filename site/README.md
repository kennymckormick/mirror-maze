# Mirror Maze project blog

English project page by Kenny, with original prose, interactive observations, all 44 model-run replays, eight English per-case films, and an additional offline protocol stress test. The narrative layout is inspired by [PixelBench](https://haoquanzhang.github.io/pixelbench/); its text, images and code are not copied.

The live publication is hosted on [Report Hub](http://vlmeval.byted.org/reports/view/rzAw-KSgopFj/). The evaluation implementation in this repository remains private for testing.

## Preview a clone

The large observation JPEGs and MP4 films are intentionally not tracked in Git. Fetch them from the published site using the checked-in SHA256 manifest:

```bash
python site/fetch_assets.py --include-videos
python -m http.server 8894 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:8894/`. The environment under `play/` works without model credentials. Replays use compressed web previews; model inputs were 1280x720 lossless PNGs. The playable environment may be used independently through `mirror-maze-bench play` without fetching the website's evidence assets.

## Rebuild from frozen evidence

`prepare_evidence.py --evidence-root /path/to/frozen-project` reads the sanitized 40-case study and 4-case Astra extension, leaving every recorded run unchanged. It normalizes display names, creates per-episode files, and compresses web previews. `plot_figures.py` exports PNG/SVG figures with Matplotlib. `build_twitter.py` creates only drafts; it never posts.

```bash
python site/prepare_evidence.py --evidence-root /path/to/frozen-project
python site/plot_figures.py
python site/build_twitter.py
python site/check_site.py
```

`check_site.py` independently validates transformed actions against the exported map edges and headings, checks terminal states and counters, scans authored text for CJK content, and writes the large-asset manifest. Original inference modules and native model IDs are preserved in the evaluation package. The HTML and media are an English presentation release, distinct from those frozen inference assets.

## Scope

- Easy: five models, four mazes each, native/API configurations, 200 decisions, 40 runs.
- Medium: GPT-6 Astra + Codex, four larger mazes, 500 decisions, 4 runs.
- All 7,907 recorded decisions were independently replay-audited.
- Offline blind policy: 500 fixed action streams on 8 fixed maps, three motor-action limits, 20 canonical trajectories / 60 budget-checkpoint checks. This is not a model score or a token/compute-matched baseline.
- Ideal-controller DFS references assume complete scan observations and are not strict guarantees under image pruning or measured human results.

Public data contain brief submitted intentions and physical states, not native transcripts, private reasoning, credentials, or profiles. Twitter materials live in `twitter/` and remain unposted drafts.
