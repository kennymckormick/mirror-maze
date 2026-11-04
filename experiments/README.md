# Offline protocol stress test

This is a **code-generated state-blind policy**, not a model score or a measured human baseline. It tests the consequences of allowing an unbounded action sequence in a single planning decision. There are no model calls, image observations, state reads by the policy, or token/latency/compute matching.

```bash
node --expose-gc experiments/random_policy.mjs --out runs/random-policy
```

Node.js18+ is sufficient. `--out` selects the output directory; the default is the ignored `runs/random-policy` directory. `--rules /path/to/maze.js` can explicitly select a compatible canonical rules module.

The fixed protocol uses500 trial seeds20261104 through20261603 and budgets of1,000,10,000,100,000 actions. For each trial it pre-generates one sequence of uniformly distributed `left`, `right`, and `forward` actions, using xorshift32 with rejection-to-range3. The same sequence is reused unchanged across all eight fixed square/hexagon maps (easy and medium; MIRROR-042/043). The host stops execution only on the actual goal. The policy itself reads neither observations nor the hidden goal.

The script compiles canonical action transitions for efficient simulation, then checks sampled trajectories with the original `Episode.step` interface. The reference run verified20 trajectories at60 budget checkpoints. `reference-summary.json` contains its fixed parameters, aggregate results, generator checksum, and original rules byte hash. New runs write their own protocol record and full trial results.

English localization changes the rules file's byte hash because topology display labels and error strings are translated. It does not claim byte identity with the old asset. The12 frozen-geometry fixtures in `tests/fixtures/geometry-v0.2.0.json` verify matching cell geometry, landmarks, openings, initial pose, goal, geometry counts, and shortest routes across all three tilings at easy and medium sizes. Reproduced aggregate results should match the reference values even though the localization hash and execution timestamps differ.

Interpretation is conditional on these fixed mazes and this generator. A high solve fraction after tens of thousands of blind physical attempts does not establish spatial understanding. A serialized100,000-action sequence is also not necessarily feasible within a model provider's output limit. This stress test makes a scoring/protocol issue visible; it does not change any frozen model result.
