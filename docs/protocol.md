# Evaluation protocol and source identifiers

The original study used five models, four easy mazes per model, and two configurations: native harnesses and a public-text-history direct API baseline. Each case had a 200-decision cap. A separate GPT-6 Astra + Codex extension used four medium mazes and a 500-decision cap. Results from these two difficulty/budget settings must be reported separately.

## Names and exact routing IDs

Readable display names do not change machine-facing identifiers. The bundled configurations retain the reference gateway's IDs:

| Display name | Native model ID | Source alias | Native client |
|---|---|---|---|
| DeepSeek V4.1 Flash (alternate source) | `DeepSeek-V4.1-Flash-1017-Ark` | `DeepSeek-V4.1-Flash` | Claude Code |
| Kimi K3.1 Pro | `kimi-k3.1-pro` | `Kimi-K3.1-Pro-Ark` | Claude Code |
| GPT-6.1 Sol | `gpt-6.1-sol` | `Codex-GPT-6.1-Sol` | Codex |
| Gemini 4 Argon | `gemini-4-argon` | `MC-ModelHub-Gemini-4-Argon` | Antigravity, Medium |
| Seed 2.1 Pro | `seed-2.1-pro` | `Seed2.1-Pro` | Claude Code |
| GPT-6 Astra | `gpt-6-astra` | `Codex-GPT-6-Astra-Relay` | Codex |

DeepSeek's alternate source was usable, but its correspondence to the requested 1017 snapshot was not independently established. An alias and a provider-reported deployment name do not independently prove an identical weight snapshot. For another gateway, replace the source IDs explicitly instead of assuming a similarly named endpoint is equivalent.

The Astra reference observed `reasoning.effort=low` and `reasoning.context=all_turns` from Codex 0.166.0 requests. Gemini used Medium. Other native settings followed the recorded clients' defaults. Exact request metadata and client versions should accompany any new result.

## Observation and action contract

Before each planning inference, the host attaches 1280×720 lossless PNG first-person observations. The latest image is the current state. Agents see no map, coordinates, shortest route, preview tool, or environment file access. The action space is `left`, `right`, `forward`.

One public JSON action sequence is one planning decision, with no sequence-length cap. The host executes valid actions through collisions and stops at the real goal. An invalid sequence executes nothing and incurs one decision plus one collision. All attempted-action states from the latest batch, including rotations and collisions, are attached to the next decision, subject to the recorded image-pruning policy.

History retains public text and the latest image batch. If a request exceeds body/context/image limits, remove the earliest image-containing round as a whole; pin the newest current-state image. When one batch is too large, this removes that batch's intermediate images. No image resizing or quality reduction is used. Repeated source timeout pruning is labeled separately from size-limit pruning.

Each decision has a total 3600-second deadline. Transport retries share the deadline and never execute actions or consume planning budget. An HTTP200 response containing only reasoning is not a completed public plan. Direct API output caps may grow on a completed empty answer caused by output-budget exhaustion, up to the configured limit, without changing effort.

## Interpretation

Pass rate is tied to the configured case set. Cap cases are unfinished observations, not successful completion times. Conditional success-only medians can change simply because different cases succeeded. Report decision count, primitive actions, collisions, coverage, configuration, source, and any observation pruning together.

The direct API baseline retains public user/assistant text and does not replay full opaque reasoning items. Native clients add their own prompts, defaults, and state management. Their differences have not been isolated in a matched causal experiment. The initial fixed-case study has no paired matte-wall model control. Ideal-observer policy simulations are algorithmic references, not measured human performance.

## Ideal-observer/controller reference

The constructive DFS decision bound assumes the controller receives and perfectly interprets every required new-cell scan, and remembers all observed walls and routes. It is an ideal-information/controller reference, not a verified guarantee under the actual image-pruning interface. An oversized backtracking-plus-scan batch may be reduced to its last view, removing required scan images; additional sensing actions or decisions may then be necessary. The numeric reference outputs are retained as originally computed and must not be presented as measured human performance.
