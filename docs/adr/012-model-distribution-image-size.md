# ADR 012 — Models in a volume via `models-init`, not in the image

**Status:** accepted · 2026-10-19

## Decision
The one-shot `models-init` service downloads the configured models into the `models` volume and verifies every file's
SHA-256 and the HF snapshot revision against `config/models.lock.json` (`--write-lock` after a deliberate change). API
containers load models with `local_files_only` and warm them before readiness turns green. JS rendering (Playwright +
Chromium) lives in a separate optional `render` image.

## Measured evidence
Runtime image 833 MB on disk (largest deps: onnxruntime 68 MB, numpy 70 MB) vs. ≈1.35 GB of models kept outside;
Trivy: 0 fixable HIGH/CRITICAL (2026-10-19).
