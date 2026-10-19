# Golden factuality benchmark

`golden.yaml` holds 68 PT/EN questions (38 PT, 30 EN) over the official Getnet corpus (snapshot `sources_version` 5,
crawled 2026-10-19), including 2 hallucination traps (non-existent product "Get Ultra Pro" and plan "Diamante").

Each item has:
- `relevant`: retrieval relevance rules (`url` regex + `text` regex) used for Recall@K / MRR / nDCG — rules survive
  re-crawls better than chunk ids;
- `facts`: atomic facts the answer must contain (any of the accent-insensitive regex `patterns`);
- `must_not`: hallucination traps (invented prices, rates, caps);
- `labeled_by` / `labeled_at` / `status` (in `default_label`).

## Labeling protocol
1. **Draft (done 2026-10-19, `draft:claude-opus-5.5`)**: every fact was copied from a specific official chunk (FAQ entry,
   Get Ajuda article, manual page or product page) read in full; no fact comes from model memory. Items carry
   `status: draft_requires_human_confirmation`.
2. **Human confirmation (pending)**: a Getnet-knowledgeable reviewer opens each `relevant.url`, confirms or edits every
   fact and trap, sets `labeled_by: <reviewer>`, `labeled_at: <date>`, `status: confirmed`.
3. **Change control**: when a source page changes (`make ingest` reports changed hashes), affected items are re-reviewed;
   the golden set drives model selection (`make benchmark-retrieval`) and threshold calibration (`make calibrate`), so
   edits must be committed together with re-run results.
4. **Feedback loop**: `make export-feedback` exports operator resolutions as candidate cases with
   `status: needs_human_review`; a reviewer promotes good ones into `dataset.json` or here.

## Judge calibration set
`judge_calibration.yaml`: 30 answers (15 faithful, 15 with a planted contradiction/invented number) over golden items,
labels drafted by construction and pending human confirmation. Results: `../results/judge_calibration.md`. Because the
contrasts are deliberately clear, agreement on this set is an upper bound — the judge is never a CI gate.
