# ADR 009 — JWT/JWKS identity with an explicit, production-refused demo mode

**Status:** accepted · 2026-10-19

## Decision
- `AUTH_MODE=jwt` (default, required in production): RS256/ES256 tokens verified against a JWKS (`iss`, `aud`, `exp`,
  `nbf`, `iat`, `sub`, `scope`), 30 s leeway, unknown `kid` → JWKS refetch (rotation), `alg` allow-list. Identity = `sub`.
  The challenge's body `user_id` stays valid but is non-authoritative: present and ≠ `sub` → **403**, security event + metric.
- `AUTH_MODE=demo`: body `user_id` is the identity for customer chat (exact challenge payload); refused when
  `APP_ENV=production`; operator routes always require JWTs.
- Dev identity provider: `make keys` (git-ignored `.dev-keys/`), `make token`, `/v1/dev/token` (non-production only).
- Scopes via FastAPI dependencies (`require_scopes`), customer `chat conversations:read conversations:delete`, operator
  `cases:read cases:write approvals:write`, admin `ingest:run`.

## Measured evidence
`tests/security/test_security.py`: route enumeration (every non-public route declares the auth dependency), 7 invalid-token
cases (expired, forged, wrong aud/iss, unknown kid, alg none, garbage) → 401, mismatch → 403 + metric, scope checks;
`tests/e2e/test_stack.py::test_demo_auth_mode_accepts_exact_challenge_payload`.
