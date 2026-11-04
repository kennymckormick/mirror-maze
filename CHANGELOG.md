# Changelog

## 0.4.0 — English research preview

- English playable UI, API documentation, review pages, and synchronized video labels.
- Model-free `mirror-maze-bench play` entry point, with the review map initially hidden.
- Review generation discovers custom models, seeds, standard sizes, and decision caps rather than assuming a five-model/easy grid.
- Recovery utilities retain the saved planning cap and handle custom API model keys.
- Original native model/source identifiers and inference behavior are retained. Geometry/actions remain environment version0.2.0; localization changes asset byte hashes, not generated connectivity or renderer shader logic.

## Reference runner 0.3.4

- Standard side lengths7/11/15 and caps up to500.
- Shared environment/prompt/cache/replay geometry settings.
- Valid Responses completions may contain `incomplete_details: null`.
