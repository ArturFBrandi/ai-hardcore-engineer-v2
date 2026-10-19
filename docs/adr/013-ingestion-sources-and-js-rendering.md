# ADR 013 — Source catalogue, polite crawling and JS-rendered hubs

**Status:** accepted · 2026-10-19

## Context
`www.getnet.net/en` (the suggested source) is a Santander-Getnet global site with little Brazilian product detail; the
Brazilian products, FAQs and manuals are on `site.getnet.com.br`, whose `page-sitemap.xml` returns a WordPress error and
whose Get Ajuda hubs build their article lists with JavaScript.

## Decision
`ingestion/sources.yaml` (versioned): seeds on both domains, allow/deny patterns, categories/products rules, 4 official
PDF manuals, and `discovered_seeds` — 74 Get Ajuda article URLs discovered by the Playwright render profile on
2026-10-19 (the articles themselves are server-rendered). robots.txt honoured (getnet.com.br has none → allowed),
0.7–1 s per-host rate limit, ETag/Last-Modified conditional requests, content-type allow-list, body-hash dedup
(topic page preferred over the FAQ hub), cross-page boilerplate removal.

## Measured evidence (`data/ingest-report.json`, crawl of 2026-10-19)
150 pages fetched (146 HTML + 4 PDF), 925 unique chunks, 846 boilerplate blocks removed, 5 × HTTP 404 skipped, 0 pages
rendered in the static run (render profile used for discovery). Snapshot: 250 KB gzip, sha256 in `data/snapshots/manifest.json`.
