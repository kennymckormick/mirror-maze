# Mirror Maze

**First-person navigation through a maze whose walls are mirrors.**

The environment is playable in a browser and exposes a Python API. The evaluation runner supports direct model APIs and optional native Codex, Claude Code, and Antigravity clients. No API key is needed to play or use the environment alone.

## Install and play

Requirements: Python 3.9+ and Node.js 18+. Rendering uses an isolated Playwright Chromium; videos additionally need FFmpeg and a TrueType font.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[render,test]'
python -m playwright install chromium
mirror-maze-bench play --port 8876
```

Open the printed local URL. Use the on-screen controls, arrow keys, or A/D/W. The review map starts hidden; you can reveal it for inspection. If port 8876 is busy, select another port. `--open` opens the page automatically. To check the environment before using any model:

```bash
python examples/environment_smoke.py --out smoke.png
```

## Python environment API

```python
from mirror_maze import MirrorMaze

with MirrorMaze(topology="hex", difficulty="easy", seed="MIRROR-042") as env:
    env.render("observation.png", width=1280, height=720)
    feedback = env.perform(["left", "forward"], stop_on_collision=False)
    print(feedback["allExecutable"], feedback["blockedAt"])
    print(feedback["results"])
```

`MirrorMaze(...)` initializes an independent episode. `initialize(...)` / `reset(...)` starts again; `state()` returns public counters and terminal status. A single action or action sequence can be passed to `perform()` / `step()`.

`preview_sequence()` checks feasibility without mutation. `export_episode()`, `review_state()`, `debug_solution()`, and `render(view="review")` expose privileged information for human inspection. **The formal model runner does not expose these interfaces to the tested model.** Use and close a rendering instance on the thread that created it.

The environment supports square, triangular, and regular-hexagonal tilings. Standard side lengths are 7/11/15. Squares form a square outline; the other tilings form a hexagonal outline. Triangle turns are 60°; attempting to move toward a vertex is blocked. Crossing a cell edge preserves heading.

## Test a model through its API

Supply credentials through the environment, not the command line or configuration files:

```bash
export MIRROR_MAZE_API_KEY="YOUR_KEY"
mirror-maze-bench preflight --base-url https://YOUR_GATEWAY/v1 \
  --config examples/custom-model.json --out runs/preflight
mirror-maze-bench run --base-url https://YOUR_GATEWAY/v1 \
  --config examples/custom-model.json --out runs/my-api --workers 4
```

Edit `examples/custom-model.json` before calling a real model. Set `source_alias` to the exact API model identifier, choose `api_protocol` (`chat`, `responses`, or `messages`), and set the provider-supported request parameters. The base URL may include `/v1`; gateway prefixes are retained. The two-round vision preflight checks both one-image and multi-image delivery using fresh digit cards. A successful HTTP response alone is insufficient: the transcription must match.

Gemini now runs in the official native Cursor CLI with `gemini-4-argon-medium`, using `CURSOR_API_KEY` and the configured network proxy. The current campaign uses six rolling Cursor slots in easy, medium, hard order. Its former AGY/API episodes remain archived with their original provenance; they are not mixed into the native Cursor cohort. Other models retain their recorded routes and four-slot difficulty barriers.

On macOS every task-owned Cursor invocation uses the real user home, `AGENT_CLI_CREDENTIAL_STORE=memory`, `DIRENV_DISABLE=1`, and a process-level block on executing `/usr/bin/security`. The runner verifies installed-client support before launch and refuses unsupported clients. It never resets or modifies user keychains. `PAUSED_FOR_KEYCHAIN.json` is a launch interlock; removing it requires a verified no-keychain authentication check and reconciliation of every interrupted checkpoint. Complete saved native replies are replayed once; sessions and remaining planning budgets are preserved.

`model_label` and `native_model` are machine-facing CLI IDs. `display_name` is only a readable report label. The bundled five-model configuration preserves the exact historical gateway IDs; another service may use different identifiers. Check the pinned sources in [docs/protocol.md](docs/protocol.md).

## Optional native harness

Install the chosen native CLI separately, then run:

```bash
mirror-maze-bench run --base-url https://YOUR_GATEWAY \
  --config examples/five-models.json --mode harness --out runs/native
```

Use `--model gpt --harness codex` to select one model/client, or `--harness-executable /path/to/binary` to select a specific installed binary. Prefer the original native executable: a wrapper may override routing or settings. The runner isolates each episode's client profile. The real API key stays in the local broker; the native client receives a local placeholder credential.

The historical reference clients were Codex 0.166.0, Claude Code 2.1.303, and the recorded Antigravity binary. Native CLI flags and defaults can change; preserve the actual binary/version when reproducing the reference configuration. Native clients retain their own system instructions and state-management behavior.

### Sol medium configuration

```bash
mirror-maze-bench run --base-url https://YOUR_GATEWAY \
  --config examples/sol-medium-codex.json --mode harness \
  --out runs/sol-medium --workers 4
```

This runs GPT-6.1 Sol + Codex on square/hexagonal mazes with `MIRROR-042` and `MIRROR-043`: side length 11, 121/331 cells, at most 500 planning decisions. The exact reference source alias is `Codex-GPT-6.1-Sol`. The reference run observed native-default `reasoning.effort=low` and `context=all_turns`; setting a different effort is a different experiment. The medium results are separate from the original easy/200-decision study.

## Resume and inspect

```bash
mirror-maze-bench run --base-url https://YOUR_GATEWAY \
  --config examples/custom-model.json --out runs/my-api --resume

mirror-maze-bench report --api-dir runs/my-api --out review
python -m http.server 8890 --bind 127.0.0.1 --directory review
```

The report discovers configured model counts, seeds, topologies, side lengths, and decision caps. It supports a single custom model as well as the bundled comparison. To compare recorded native and API batches, add `--harness-dir runs/native`. An optional `--cache-dir` overrides the cache recorded in case configurations.

Resume replays the saved environment state and continues the original conversation/budget. Finished `success` and `planning_cap` cases are not sampled again. `--resume` expects a clean saved interruption; if a host process was killed between a committed provider reply and its checkpoint, inspect and recover that reply before starting another inference. The recovery utilities under `tools/` preserve original checkpoints and record provenance; they are not a license to restart an unfinished episode with a fresh budget.

For a stopped Codex runner, inspect and reconcile a saved native answer with:

```bash
python tools/recover_codex_checkpoint.py runs/native/gpt-square-MIRROR-042
python tools/recover_codex_checkpoint.py runs/native/gpt-square-MIRROR-042 --apply
```

Use this after the controlling runner has stopped. Inspection makes no model request. Applying
archives the original logs, replays any completed answer once, then continues the original
native session with the saved decision cap. Ambiguous histories are rejected for review.

## Fixed visual protocol

- Original easy study: square/hexagon × `MIRROR-042`/`MIRROR-043`, side 7 (49/127 cells), 200 decisions. The CLI supports standard side lengths 7/11/15 and caps 1–500.
- Model inputs: 1280×720 PNG, 10 reflections, first-person view only. No map, coordinates, route preview, shell, browser, or maze tools.
- Actions: `left`, `right`, `forward`. Square turns are 90°; hexagon turns are 60°.
- One public JSON action sequence is one planning decision. Sequence length is unbounded. The host stops on the real goal; reflected beacons do not count.
- Valid sequences continue after collisions. An invalid sequence is rejected atomically and incurs one decision and one collision.
- The next decision receives the image after every action in the previous batch, including rotations and blocked attempts. The last image is the current state.
- On size/context limits, remove the oldest image-containing round; if a single batch is too large, remove intermediate images while pinning its latest view. Original PNG quality and text feedback are retained. The default body budget is 120,000,000 bytes.
- A planning decision has a total 3600-second deadline shared by its requests/retries. Streaming heartbeats do not reset it. Repeated source timeouts may trigger the same image-pruning rule, with a separate recorded cause.
- Empty/truncated responses and transport retries do not execute actions or add decisions/collisions. For an empty completed response caused by output-budget exhaustion, the direct API client can increase its output cap within the same deadline, up to its configured `max_output_budget`.

The direct API baseline retains public user/assistant text; it does not replay opaque encrypted reasoning items or use server-side conversation state. This is one API-agent configuration, not every possible API agent. Native/API differences are observational comparisons, with prompt, state, parameter, and source confounds. Per-case traces and settings matter alongside pass rates.

## Artifacts and privacy

Each case writes `config.json`, `progress.json`, `episode.json`, `result.json`, and `wire.json`. Public dialogue stores the model's public response. Files under `private/`, native transcripts, and isolated profiles may contain provider reasoning or sensitive runtime state. Keep them local. The static report exports brief public intentions, review geometry, compressed image previews, counters, and input-delivery hashes; it does not export provider reasoning streams.

The repository excludes inference runs, private profiles, image caches, raw logs, and credentials. The historical environment/source manifests distinguish frozen inference assets from the English presentation release.

## Validate

```bash
pytest -q
python -m pip wheel --no-deps --wheel-dir dist .
```

Tests use synthetic local API fixtures and canonical JavaScript replay; they never call a real model. The pipeline fixture's host-generated route is a transport test, not a model score. [docs/api.md](docs/api.md) covers the environment interface; [docs/protocol.md](docs/protocol.md) records the evaluation settings and interpretation.

The public-release video generator is retained under `site/media/`, with configuration, a fast preview command, per-case rebuilds and content-hash caching. Swap the evidence assets and captions through configuration to reuse the layout. The release selects four models in a 2-by-2 grid. Videos align decisions, accelerate long sequences, and must not be interpreted as wall-clock inference speed. Full HD encoding takes time; unchanged cached films are reused.

## Offline protocol stress test

```bash
node --expose-gc experiments/random_policy.mjs --out runs/random-policy
```

This code-generated blind policy pre-generates the same three-action sequence for each trial across eight fixed mazes. It investigates the unbounded-sequence decision metric. It is not a model or human score and is not token/compute matched. The fixed500-trial protocol and reproducibility notes are in [experiments/README.md](experiments/README.md).

Ideal-observer/controller references assume complete access to the required local scan. Their DFS decision bound is not a verified guarantee under image pruning, which can remove intermediate scan views. Additional sensing/decisions may be needed; these are not human measurements.

### Difficulty stop rule

The active campaign applies a prospective stop rule: if every scheduled trial for a model at one difficulty ends at its planning cap and passes independent replay audit, higher difficulties that have never started are skipped. Easy includes the model's scheduled API controls. Infrastructure interruptions do not count as failures. Higher rolling slots wait until an audited pass is observed or all prerequisite trials finish. Already-started and completed trials are preserved.

Skipped maps retain their planned case IDs and caps but have no fabricated episode or model result. Each skip records the prerequisite result/audit hashes and a separate policy audit. Reports distinguish planned, executed/audited and skipped counts; skipped trials are excluded from measured pass rates. Any all-planned estimate must identify untested outcomes explicitly.

## Text-only native harness runs

Text mode gives the model the grid rules once, then only ordered action outcomes and the invalid-sequence flag. It uses the same canonical maze engine, with no rendering. The host records all accepted primitive actions and verifies terminal results with a replay audit.

```sh
# Use an existing authorized gateway credential or enter it at the hidden prompt.
mirror-maze-bench run --mode harness --observation text \
  --config examples/text-native.json --model kimi \
  --base-url https://YOUR_GATEWAY --difficulty easy --workers 4 \
  --out runs/kimi-text-easy
```

The standard caps are 200 planning rounds for Easy and 400 for Medium. Complete all four Easy trials and their audits before opening Medium. Run Medium when at least one Easy trial succeeded, and record unstarted Medium trials as policy skips if all four audited Easy trials reached the cap. Infrastructure interruptions do not resolve this gate. Use a separate output directory for each difficulty.

For a reconciled interruption, use the same command, output directory, source, and settings with `--resume`. Saved native replies are verified before execution, and the committed action prefix is retained. A case flagged for operator attention requires reconciliation first.

Native AGY uses its existing login and needs `--network-proxy http://YOUR_PROXY:PORT`. Set `--harness-executable` to the pinned client binary when reproducing a recorded run. The text study used Codex 0.166.0, Claude Code 2.1.303, and AGY 1.5.2. A gateway alias in the example must resolve to the documented model snapshot. If you substitute a source or a client, record the change as a separate configuration.
