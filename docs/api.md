# Environment API

`MirrorMaze` delegates rules to the same JavaScript engine used by the browser. Node.js is required for actions; an isolated Playwright browser starts lazily for rendering.

```python
from mirror_maze import MirrorMaze

with MirrorMaze(topology="square", difficulty="medium", seed="MIRROR-042") as env:
    assert env.info()["geometry"]["cell_count"] == 121
    before = env.state()
    preview = env.preview_sequence(["left", "forward"])
    assert env.state() == before
    result = env.perform(["left", "forward"], stop_on_collision=False)
    env.render("observation.png", width=1280, height=720)
```

| Interface | Purpose |
|---|---|
| `initialize(...)` / `reset(...)` | Start a new episode. Omitted options retain their previous values. |
| `info()` | Public configuration, geometry counts, action names, and turn angle. |
| `state()` | Public counters and success state; no hidden pose or map. |
| `perform(action_or_sequence, stop_on_collision=True)` | Execute one action or sequence. Collisions count as actions. |
| `preview_sequence(actions)` | Non-mutating feasibility preview, restricted to host review. |
| `render(path=None, width=960, height=600)` | PNG bytes, optionally saved. Default is first person with no map or HUD. |
| `export_episode()` / `review_state()` | Privileged geometry, pose, goal, and history for evaluation/replay. |
| `debug_solution()` | Privileged shortest action route from current state. |
| `close()` | Release Node, Chromium, and temporary local HTTP resources. |

The formal runner sets `stop_on_collision=False`, validates the whole submitted sequence before execution, and stops immediately upon entering the real goal. An illegal sequence is rejected atomically and adds one collision penalty. The environment alone raises on invalid action strings; the runner implements that planning-level penalty.

`collisionType` distinguishes `wall` and `vertex`. `blockedAt` is a zero-based sequence index, and `allExecutable` describes whether the sequence encountered any block. `completed` says whether all requested actions were accepted, which differs from collision-free execution.

## Geometry

| Tiling | Outline | Easy | Medium | Hard |
|---|---|---:|---:|---:|
| Square | Square | 49 | 121 | 225 |
| Equilateral triangle | Hexagon | 294 | 726 | 1,350 |
| Regular hexagon | Hexagonal arrangement | 127 | 331 | 631 |

Difficulty selects side lengths 7, 11, and 15. The environment API also permits explicit side lengths 2–31; the benchmark CLI intentionally accepts only the standard three sizes. Square turns are 90°; triangle and hexagon turns are 60°. Triangle headings alternate between edges and vertices.

Render-only options include mirror walls and 1–12 reflections (default10). Geometry/actions are deterministic for a fixed engine, topology, size, and seed. Pixel identity can still depend on browser/GPU implementation.
