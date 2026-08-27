# Atlas of Judgment

**Live: [atlas-of-judgment.pages.dev](https://atlas-of-judgment.pages.dev)**

**How a paper is judged.** Every public peer review of ICLR (2018–2026), decomposed into
atomic units of evaluative logic — *what was inspected, what was observed, which standard
was invoked, what was concluded* — and charted like a sky.

- **The Atlas** — `index.html` (interactive; live at [atlas-of-judgment.pages.dev](https://atlas-of-judgment.pages.dev))
- **About** — `about.html`: the question, the three-layer pipeline, the findings digest
- **The Method** — `method.html`: full reproduction record of the data layer, including
  the chronicle of pilots, failures, and mid-course decisions
- **Resources** — `resources.html`: source code, data downloads, the machine-reader API,
  citation, and license — every outbound door on one quiet page

## Scale

410,586 logic units from 74,380 ICLR 2026 reviews (review-level track) and 1,009,592 units
from 50,861 forums across 2018–2026 (forum-level track), extracted through a three-layer
pipeline: raw OpenReview record → DeepSeek analytic memos → Qwen schema-constrained
structuring → locally-induced taxonomy (12 objects of scrutiny × 12 reasoning standards)
and a validated inference-form classifier. Cross-cutting analyses: the within-review
itinerary, the elements and price of a charge, rebuttal dynamics, panel deliberation,
decision associations, nine-year drift, and the field's hardening. (Two early analyses —
the topic-transition wheel and the five reviewer archetypes — were retired after failing
their nulls; the archetype mirage is preserved as Appendix III, the Null Cabinet.)

## Repository layout

| Path | Contents |
|---|---|
| `index.html` / `about.html` / `method.html` | the three self-contained pages |
| `scripts/` | the full pipeline (collection → normalization → memo → structuring → taxonomy → aggregates). 1Password item IDs in the supervisor shells are redacted. |
| `provenance/` | the project's internal provenance documents (Japanese): end-to-end process, artifact registry, decision log, reproducibility limits, operational handoff |
| `data/` | derived aggregates the pages are built from (taxonomy, cluster digests, per-plate JSON) — no raw databases |
| `codex-history/` | the sanitized agent-session history behind the data pipeline (see below) |
| *(generated, not committed)* | the machine-reader layer — `/api/v1/*` (32 plate depositions + 44 data islands), `/llms.txt`, `/openapi.yaml`, `/api/` docs — is built by `scripts/deploy_pages.py` from `scripts/api_assets/` into the gitignored `.pages-dist/` at deploy time |

## The session history

`codex-history/session-2026-08-11-sanitized.jsonl` (29 MB, 21,386 events) is the raw
working log of the AI agent session that built the data pipeline — every command,
decision, and failure, as it happened. Sanitization (see `scripts/sanitize_codex_session.py`):
encrypted reasoning blobs stripped (3,430), 1Password references redacted (148), a personal
email redacted (4×). The output was machine-verified to contain no credential formats
before release. Raw databases (~GBs) are not in this repo; the pipeline in `scripts/`
regenerates them from OpenReview (see `method.html` for exact commands, costs, and what
cannot be reproduced).

## Provenance & honesty

Every number on the pages carries its method: the taxonomy was induced, not imposed;
classifier plates report cross-validated error; the reliability of the instrument itself
is charted in the atlas appendix. The analysis layers are machine readings of public
reviews — one consistent reading at scale, not ground truth.

*Data source: openreview.net (public records). Analyses verified 2026-08-18.*
