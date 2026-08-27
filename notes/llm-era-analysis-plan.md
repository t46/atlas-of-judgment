# LLM-era trend analysis — pre-specified plan (frozen 2026-08-27, before computation)

Purpose: measure whether ICLR reviews 2024–2026 show corpus-level trend shifts
consistent with the documented signature of LLM-assisted writing. We CANNOT and
WILL NOT claim any review was AI-written, nor that any trend is caused by AI.
The literature gives us a signature (vocabulary, convergence, correlates); we
measure whether ICLR's review corpus moved toward that signature, and when.

## Literature anchors (verified by survey agents 2026-08-27)

- Liang et al., ICML 2024 (arXiv:2403.07183): population-level MLE mixture over
  adjective frequencies. ICLR: 1.6% (2023) → 10.6% (2024) of review sentences
  estimated substantially LLM-modified. Markers: commendable 9.8×, meticulous
  34.7×, intricate 11.2×; also notable, versatile, innovative. Correlates of
  high-α: near deadline, low confidence, no "et al.", fewer rebuttal replies,
  closer to corpus mean in embedding space. Nature Portfolio = flat (negative control).
- Kobak et al., Science Advances 2025 (arXiv:2406.07016): excess-vocabulary
  method — pure counting, counterfactual by linear extrapolation of pre-LLM
  word frequencies. ≥13.5% of 2024 PubMed abstracts LLM-processed (lower bound).
  Top ratio words: delves 28.0×, underscores 13.8×, showcasing 10.7×.
- Latona et al. 2024 (arXiv:2405.02150): ICLR 2024, GPTZero-flagged reviews
  (≥15.8%) score higher (53.4% of within-paper pairs, p=.002); near-threshold
  papers with an AI-flagged review +4.9pp acceptance. (Instance-level detector —
  we use their DESIGN with a transparent marker, not their detector.)
- Detector critique: Liang et al. Patterns 2023 (non-native-speaker false
  positives); Yu et al. ICLR 2026 (788,984-pair benchmark; per-review detection
  unreliable). → Everything below is corpus-level; no review is individually labeled.
- Timeline: ChatGPT 2022-11-30 (ICLR 2023 reviews mostly already written);
  ICLR 2025 official review-feedback-agent RCT (sanctioned LLM feedback,
  26.6% of treated reviewers revised, no acceptance-rate effect); ICLR 2026
  reviewer LLM-disclosure policy (2025-08-26) + response to LLM-generated
  reviews (2025-11-19).

## Hypotheses

- H1 (vocabulary): the frozen LLM-signature word set shows positive excess
  (δ>0) at ICLR 2024, 2025, 2026 but not 2023 or earlier. Open question: does
  the excess accelerate 2025→2026, plateau, or fall (policy year / model change)?
- H2 (lexical convergence): within the constant-form window 2024→2026, both
  within-paper and cross-paper review similarity rise.
- H3 (content convergence): co-reviewers of the same paper overlap more in
  WHICH of the 12 objects they inspect, 2024→2026 (unit-taxonomy direct track).
- H4 (correlates, 2026 cross-section): marker-bearing reviews show the Liang
  profile — lower confidence, fewer "et al.", fewer rebuttal replies, later
  submission — and the Latona direction: higher ratings than their same-paper
  marker-free co-reviews.

All outcomes are reportable: no excess anywhere contradicts Liang's ICLR-2024
estimate (interesting); excess that falls in 2026 is ambiguous between policy
and model-vocabulary aging (stated); excess that rises is a prevalence floor.

## Frozen methods

Corpus: `data/processed/iclr/analysis.sqlite3`, messages where
kind='official_review', 2018–2026 (n=199,031). Text = content_text with
`[field]` header lines removed. Tokens = lowercased `[a-z]+` (≥2 chars).
Document = review. Document frequency f_w(y) = share of year-y reviews
containing w at least once.

A. Excess vocabulary.
- Frozen marker set M (13 forms, all from the two verified lists; style words
  only): commendable, meticulous, meticulously, intricate, pivotal, versatile,
  delve, delves, delving, underscores, underscoring, showcases, showcasing.
- Indicator series I(y) = share of reviews containing ≥1 word of M.
- Counterfactual q(2023..2026) = OLS linear extrapolation of I over 2018–2022,
  clamped to [0,1]. Excess Δ(y) = I(y) − q(y). Same per-word for the 13 words.
- Placebo 1: Δ(2023) expected ≈ 0 (reviews pre-date ChatGPT by ~1 month).
- Placebo 2: frozen negative-control set C (reviewer-register style words with
  no documented LLM preference): interesting, unclear, convincing, marginal,
  concerns, thorough, sound, novel, weak, solid. Expect |Δ_C| small; computed
  identically, reported regardless.
- Discovery (exploratory, labeled as such): all words with f(2026) ≥ 1e-3,
  ranked by ratio r = f(2026)/max(q,1e-4) with baseline f(2018–2022 mean)
  < 0.02; content words (LLM/topic drift: e.g. "llm", "transformer",
  "diffusion", "hallucination") annotated and excluded from any style claim.

B. Lexical convergence.
- Per year: TF-IDF (sublinear tf, min_df=5, max_features=100k, unigram) fit on
  that year's header-stripped reviews; L2-normalized rows.
- Within-paper: papers with ≥2 reviews → mean pairwise cosine → per-year median.
- Cross-paper baseline: 50,000 random pairs of reviews from different papers,
  same year → median cosine.
- Primary window: 2024→2026 (identical review form). Secondary: 2018/2019/2021
  (identical form). Cross-era level differences are NOT interpreted (form confound).

C. Content convergence.
- direct-v1 units DB: per forum, per reviewer_key, the set of object_key values;
  mean pairwise Jaccard among co-reviewers; per-year median across forums with
  ≥2 reviewers. Cross-forum baseline: sampled pairs across forums, same year.

D. Correlate battery, ICLR 2026 cross-section (one form, one scale).
- Marked := review contains ≥1 of M (transparent, reproducible; NOT an
  AI-authorship claim — stated in every output).
- Compare marked vs unmarked: rating (within-paper paired mean difference +
  sign test over papers having both), confidence, share containing "et al.",
  rebuttal replies by the same signature in the forum, word count, cdate
  percentile within year. Bootstrap 95% CIs (paper-level resampling).
- Repeat rating pairing for 2024 and 2025 as robustness.

Outputs: data/analysis/iclr/unit-taxonomy-2026-v1/llmtrace-data.json with every
number above + metadata; script scripts/build_llmtrace_data.py (deterministic,
seeded). Site figure(s) only after numbers are inspected against this plan.

## Addenda (added after first computation — labeled exploratory, not frozen)

- 2026-08-27, after the deep survey surfaced Sharma, Joachims & Dean
  (arXiv:2601.20920: apparent LLM self-preference at ICLR/NeurIPS/ICML is a
  leniency-toward-weak-papers confound; fully-LLM reviews compress to 6–7):
  paired-rating differences are additionally stratified by the unmarked
  co-reviews' mean rating (weak <4 / mid 4–6 / strong >6) to check whether our
  marked-review score premium is likewise concentrated on weak papers.
- Robustness additions after run 1 (documented in-session): fixed shared
  vocabulary for the 2024–2026 convergence window; length-decile
  stratification for the correlate battery; longest-review-vs-rest paired
  benchmark for the rating comparison.
- Prior art to cite (deep survey, 2026-08-27): Wu, Zhang, Zhao & Bao
  (arXiv:2604.19578, Scientometrics 2026) — nearest neighbour: ICLR 2017–2025
  + NeurIPS, LLM-assisted reviews longer/more standardized, summary share up,
  originality attention down; explicitly does NOT compute co-review
  similarity. Sharma et al. 2601.20920 — 26.65% of ICLR 2025 reviews show LLM
  involvement by the Liang-method threshold. No published work computes
  within-paper co-review textual similarity over years on real OpenReview
  data (checked via S2 + arXiv, 2026-08-27).
- Exhaustive sweep (2026-08-27, 70 curated 2025–26 papers): additional
  citations folded into the plate — Kim et al. arXiv:2605.20668 (AI reviewers
  overlap 21% vs humans' 3%, expert annotation — independent corroboration of
  convergence via a different instrument); Baumann et al. arXiv:2605.03202
  ("hivemind effect"); Kahneman4Review arXiv:2607.10511 (2022–23 shift in ICLR
  review-text diagnostics — timing neighbour; our placebo puts vocabulary
  arrival one cycle later); Gray arXiv:2512.01560 (excess-vocabulary on papers
  at large). TTR-200 lexical-diversity series added as a second standardization
  signal after Li et al. arXiv:2605.25415 named lower lexical diversity a
  property of LLM reviews. Boos arXiv:2608.14625 relays a "~21% of
  ICLR 2026 reviews fully AI-generated" estimate — traced 2026-08-27 to its
  primary source: a Pangram Labs (commercial AI-detection vendor) self-published
  blog post (2025-11-18, "Pangram predicts 21% of ICLR reviews are AI-generated"),
  using their proprietary then-private-beta EditLens per-review detector with
  self-reported, unaudited false-positive rates. VERDICT: fails this plan's
  citation bar — it is exactly the per-review-detector category the plate's
  own first voice block declares unreliable, and ICLR's chairs' 2025-11-19
  statement pointedly cites no such number and requires human confirmation
  over detector output. PERMANENTLY UNCITED. Boos's companion "up from 15.8%
  at ICLR 2024" is Latona et al. arXiv:2405.02150's GPTZero figure, so the
  15.8%→21% "trend" compares two different proprietary detectors — never cite
  it as a trend. Checked: no shipped claim depends on either number ("spreads"
  rests on Sharma's 2025 likelihood-method estimate alone).

## Addendum E+F (2026-08-27, user-requested after shipping: category
## dispersion and sub-unit-grain convergence; frozen before computation)

E. Category-mix concentration (direct track, official_reviewer units,
2018–2026, one extraction pipeline throughout):
- Per year: normalized Shannon entropy (H / log2 K) and HHI of (i) the
  12-object mix, (ii) the 12-standard mix, (iii) the joint 144-cell mix
  (K=144). Unit-weighted.
- Per-reviewer view: for reviewers with ≥5 units, entropy of their own
  object mix, normalized by log2(min(12, n_units)); per-year mean —
  "does one reviewer spread attention more narrowly than before?"
- No frozen direction; descriptive, all outcomes reportable. Guard: units
  per reviewer changed over years; the per-reviewer entropy is normalized
  by its own ceiling for that reason.

F. Reasoning-prose dispersion at the sub-unit grain (the unit's reasoning
component, bge-small embeddings, row i = unit_pk i+1):
- Dispersion: per year, sample 20,000 official_reviewer units (seed
  20260827); mean cosine distance to the year centroid.
- Sub-unit convergence: per forum with ≥2 reviewers, sample up to 20
  cross-reviewer unit pairs; cosine similarity; per-year median across
  forums. Baseline: 50,000 random same-year cross-forum unit pairs.
- Guard stated in caption: embeddings are of the DISTILLED reasoning text,
  so the extractor's uniform style is shared by all years — this measures
  convergence of reasoning content, complementing the raw-text TF-IDF
  measure which includes style.
Output: "mix" section appended to llmtrace-data.json by
scripts/build_llmtrace_mix.py; new Fig 29d (profile battery relettered 29e).

## Addendum G (2026-08-27, same day): correction of the object-overlap claim

Results of E+F: object entropy 0.916→0.940 (more even), reviewer attention
entropy 0.742→0.766 (wider), reasoning-embedding dispersion and within-forum
gap flat. Prompted a size-matched null for the co-reviewer Jaccard (same
forums, same set sizes, contents drawn from the year's mix, 20 sims): the
null rises 0.216→0.242 with mean set size 3.4→3.8, absorbing the observed
0.252→0.284 rise. Excess flat +0.037..+0.054 (max 2018). The shipped claim
"the turn lands on the watermark's arrival year" was WRONG as a content-
convergence reading and was corrected in place the same day; recorded in
method §10. Standing summary: convergence is real in raw wording (Fig 29c
left, fixed vocabulary), unproven in targets and structure (29c right null,
29d entropy/embedding nulls), and prose-level assistance is the honest —
unprovable — reading of that divide.

## Addendum H (2026-08-27, evening): adversarial audit — one more correction,
## one refinement

A deliberate hunt for siblings of the addendum-G artifact, run before readers
could find them. Two findings changed the plate; both computations were then
moved into build_llmtrace_mix.py (mix.attention_null, mix.section_convergence)
so they are reproducible from the builder.

1. CORRECTION (Fig 29d middle panel). The shipped reading — per-reviewer
   attention entropy rises 0.742→0.766, "the individual reviewer looks at
   slightly more kinds of things" — is mostly mechanical: an n-matched null
   (same reviewers' unit counts, objects drawn iid from the year's own mix,
   seed 20260827, 5 sims) itself rises 0.788→0.804, because units per
   ≥5-unit reviewer grew 6.48→6.68 and the year mix grew more even. Excess:
   −0.046 (2018) → −0.038 (2026), non-monotone, no post-2023 downward bend
   (−0.044, −0.048, −0.047, −0.038). Only the defensive claim (no narrowing)
   survives; the panel is now drawn with the null and read as the left
   panel's person-grain check, not an independent instrument. Corrected in
   place the same evening; method §10 records it as the third correction.

2. REFINEMENT (Fig 29c left panel). The +14% fixed-vocab within-paper cosine
   rise decomposes by section: summary-only 0.2801→0.3102→0.3357 (+20%,
   still climbing); weaknesses+questions-only 0.1608→0.1720→0.1708 (+6%,
   flat after 2025). Section lengths near-constant (summary 89/87/93 words,
   criticism 273/294/275) — not a length artifact. The convergence
   concentrates in the most delegable section; sharpens the plate's
   assistance-not-delegation reading. Added to the caption with a prov-door.

Also cleared by the same audit (evidence in-session): the cosine rise is not
a pair-length artifact (rises within every length band) nor panel-size
composition (rises at every panel size); the 2026 fade is not a length
artifact (falls inside every fixed length band while reviews did not
shorten); marked reviews are slightly EARLIER (cdate −0.02 pct), making the
timing correlate conservative; the embedding row contract (row i = unit_pk
i+1, unit-norm) holds; the reasoning co-review gap is 0.0115–0.0134 in all
nine years; aggregate mix entropy is population-level (plug-in bias ≤0.001)
and not n-mechanical; all spot-checked caption numbers match the shipped
JSON. Fig 29e's deck now carries the 2026 marginality (p=.075) explicitly.
Follow-up (same day): the length-artifact exclusion was builder-ized as
mix.fade_length_check in build_llmtrace_mix.py (deterministic, no RNG:
marker share per fixed word-count band 2023–2026 + mean review length;
2024→2026 the share falls in every band, 500–699w 10.4%→4.4%, 700+ 10.2%→
7.1%, while mean length rose 406→425 words) and stated on the plate as a
fourth excluded explanation beside the three-way ambiguity, with a
prov-door sourcing that key.

## Interpretation guards (bind the captions)

- Never "AI wrote X% of reviews". Only: "at least Δ% of reviews contain
  vocabulary in excess of the pre-2023 trend — vocabulary that two independent
  studies identify as the signature of LLM-assisted text".
- Marker vocabulary ages with model generations: a 2026 decline in M is
  ambiguous (disclosure policy, model change, or laundering) — say so.
- Form eras: never compare similarity LEVELS across form boundaries.
- 2025 contains sanctioned LLM feedback (official RCT) — its trace is
  legitimate by that year's rules; say so.
- Score-scale era caveat applies to any rating comparison (within-year only).
