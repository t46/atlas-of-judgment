---
license: cc-by-4.0
language:
  - en
pretty_name: Atlas of Judgment — ICLR Peer-Review Logic Units
size_categories:
  - 1M<n<10M
tags:
  - peer-review
  - metascience
  - ICLR
  - openreview
  - science-of-science
configs:
  - config_name: review_level_2026
    data_files:
      - split: train
        path: review_level_2026/units-*.parquet
  - config_name: review_level_2026_reviews
    data_files:
      - split: train
        path: review_level_2026/reviews.parquet
  - config_name: forum_level_2018_2026
    data_files:
      - split: train
        path: forum_level_2018_2026/units-*.parquet
---

# Atlas of Judgment — ICLR Peer-Review Logic Units

1,420,178 atomic units of evaluative logic extracted from public ICLR peer
reviews (OpenReview), each decomposed into *what was inspected → what was
observed → how it was reasoned about → what was concluded*, and labeled with a
data-induced taxonomy of **12 objects of scrutiny × 12 epistemic standards**.

This is the dataset behind the [Atlas of Judgment](https://atlas-of-judgment.pages.dev)
(interactive visual atlas). Full reproduction record — every script, model,
parameter, seed, count, cost, and failure mode — at
[atlas-of-judgment.pages.dev/method](https://atlas-of-judgment.pages.dev/method)
and [github.com/t46/atlas-of-judgment](https://github.com/t46/atlas-of-judgment).

## ⚠️ AI-generated content disclosure

Every row is a **machine reading** of a human review, produced by a two-stage
LLM pipeline (DeepSeek memo → Qwen structuring). Units are grounded in the
reviewers' public text, but the decomposition, phrasing, and labels are model
output and may contain errors or omissions; they are for research reference
only. The `support_status` field records whether a unit is grounded in the
reviewer's explicit words or inferred by the memo layer. Known instrument
bias: 71.8% of units resolve negative — a property of the extraction
instrument as much as of reviewers.

## Subsets

| Config | Rows | Grain | Years |
|---|---|---|---|
| `review_level_2026` | 410,586 | one unit of one official review | ICLR 2026 |
| `review_level_2026_reviews` | 74,380 | one official review (summary + unit count) | ICLR 2026 |
| `forum_level_2018_2026` | 1,009,592 | one unit of one reviewer within one forum | ICLR 2018–2026 |

The 2026 review-level track reads 98.1% of the venue's official reviews at the
finest grain; `review_id` is the OpenReview note id, so every unit can be held
against the original review. The forum-level track reads 50,861 of 51,813
public forums (98.2%) across nine years and adds discussion-phase fields
(`temporal_position`, `judgment_change`, `update_trigger`).

`taxonomy/taxonomy_v1.json` carries the label definitions (12 objects × 12
standards, induced by UMAP+HDBSCAN over a 12k sample, then human-named).
`taxonomy/rhetoric_labels_analyst.csv` carries 600 analyst-labeled units for
the six inference forms (NORM / BLOCK / DOUBT / ANCHOR / REACH / WEIGH) used
by the atlas's rhetoric classifier.

## Fields (both unit tables)

- `inspected_object` / `observation` / `reasoning` / `judgment` — the four-step
  logic decomposition, in the pipeline's words
- `valence` — negative · positive · conditional · uncertain · mixed
- `suggested_improvement` — the concrete fix, when the review offered one
- `support_status` — explicitly_supported vs memo-inferred grounding
- `confidence` — the structuring model's own confidence in the unit
- `object_key`, `object_sim`, `standard_key`, `standard_sim` — nearest-centroid
  taxonomy assignment with cosine similarity to the centroid
- forum-level only: `year`, `forum_id`, `reviewer_key` (per-forum anonymous
  reviewer id), `reviewer_role`, `temporal_position` (initial vs
  post-author-response), `judgment_change` (maintained / strengthened /
  weakened / reversed), `update_trigger`
- review-level only: `paper_id`, `review_id` (OpenReview note ids),
  `n_evidence_refs`, `n_missing_links`

## Provenance & pipeline

1. **Raw**: public OpenReview API v1/v2, 52,460 ICLR forums, 2018–2026.
2. **Memos**: `deepseek-v4-flash` (temp 0.4) writes qualitative-metascience
   memos — review-level (ICLR 2026) and forum-level (2018–2026) tracks.
3. **Units**: `qwen3.7-flash` normalizes memos into the structured units here.
4. **Taxonomy**: induced from a 12k-unit sample (bge-small-en-v1.5 embeddings,
   UMAP n=15 / HDBSCAN 60/10, seed 7), human-named, assigned corpus-wide by
   nearest centroid.

Decisions, scores, and other outcome metadata were **never shown to the
extraction pipeline**; join them from OpenReview at analysis time if needed.

## License & attribution

Released under **CC BY 4.0**, matching the upstream license of the source
material: reviews and comments on OpenReview are released by their submitters
under CC BY 4.0 ([OpenReview Terms of Use](https://openreview.net/legal/terms)),
and ICLR 2026 submissions carry a CC BY 4.0 license field. Please attribute
**OpenReview and the ICLR reviewer community** (source text) and cite this
dataset (machine reading). Reviewer identities are the public anonymous ids;
no non-public personal data is included.

```bibtex
@misc{atlas-of-judgment-2026,
  title  = {Atlas of Judgment: ICLR Peer-Review Logic Units},
  author = {Takagi, Shiro},
  year   = {2026},
  url    = {https://huggingface.co/datasets/t46/atlas-of-judgment}
}
```

## Limitations

- Machine reading, not ground truth: validate before drawing strong claims.
- 1.9% of 2026 reviews and 1.8% of forums failed extraction; missingness is
  not verified to be topic-random.
- The standard taxonomy is a skeleton induced from ~40% of the pilot sample
  (61.5% HDBSCAN noise on the reasoning side); per-unit assignment similarity
  is provided so you can filter.
- Sub-scores and decisions are not included here — join them from OpenReview.
