"""Identity and authorization.

* ``AUTH_MODE=jwt`` (default; required in production): RS256/ES256 bearer tokens verified against a JWKS
  (``iss``, ``aud``, ``exp``, ``nbf``, ``iat``, ``sub``, ``scope``) with clock-skew leeway and key-rotation
  support (unknown ``kid`` → JWKS refetch, rate limited). Identity = ``sub``.
* ``AUTH_MODE=demo`` (development/evaluation only, refused in production): the body ``user_id`` of the
  exact challenge payload is the identity for customer chat; operator routes still require JWTs.

Scopes are enforced by FastAPI dependencies marked with ``AUTH_DEPENDENCY`` (a test enumerates every route).
The LLM never participates in any of this.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from fastapi import Depends, Request
from jwt import PyJWK

from app.config import AuthMode, Settings
from app.errors import AuthenticationError, AuthorizationError
from app.observability import metrics
from app.observability.logging import get_logger
from app.tools.context import AuthContext

log = get_logger(__name__)

CUSTOMER_SCOPES = frozenset({"chat", "conversations:read", "conversations:delete"})
OPERATOR_SCOPES = frozenset({"cases:read", "cases:write", "approvals:write"})
ADMIN_SCOPES = frozenset({"ingest:run"})


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]
    kind: str  # customer | operator | admin
    method: str  # jwt | demo
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def customer_id(self) -> str:
        return self.subject

    def auth_context(self, request_id: str | None) -> AuthContext:
        return AuthContext(
            customer_id=self.subject,
            subject=self.subject,
            actor_type="customer",
            scopes=self.scopes,
            request_id=request_id,
        )


class JWKSCache:
    def __init__(self, url: str, ttl_seconds: int, min_refresh_seconds: float = 30.0) -> None:
        self.url = url
        self.ttl = ttl_seconds
        self.min_refresh = min_refresh_seconds
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at = 0.0

    def _load(self) -> dict[str, Any]:
        parsed = urlparse(self.url)
        if parsed.scheme == "file":
            return dict(json.loads(Path(parsed.path).read_text()))
        resp = httpx.get(self.url, timeout=5.0)
        resp.raise_for_status()
        return dict(resp.json())

    def _refresh(self) -> None:
        try:
            data = self._load()
        except (OSError, httpx.HTTPError, json.JSONDecodeError) as exc:
            log.error("jwks_unavailable", error=type(exc).__name__)
            raise AuthenticationError("authentication service unavailable") from exc
        keys = {}
        for jwk in data.get("keys", []):
            try:
                keys[jwk["kid"]] = PyJWK(jwk)
            except (KeyError, jwt.PyJWTError):
                continue
        self._keys = keys
        self._fetched_at = time.monotonic()

    def key_for(self, kid: str | None) -> PyJWK:
        now = time.monotonic()
        if not self._keys or now - self._fetched_at > self.ttl:
            self._refresh()
        if kid not in self._keys and now - self._fetched_at > self.min_refresh:
            self._refresh()  # key rotation
        if kid not in self._keys:
            raise AuthenticationError("unknown signing key")
        return self._keys[kid]


class TokenVerifier:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.jwks = JWKSCache(settings.jwt_jwks_url, settings.jwks_cache_seconds)

    def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            metrics.AUTH_FAILURES.labels(reason="malformed").inc()
            raise AuthenticationError("invalid token") from exc
        if header.get("alg") not in self.s.jwt_algorithms:
            metrics.AUTH_FAILURES.labels(reason="algorithm").inc()
            raise AuthenticationError("invalid token")
        key = self.jwks.key_for(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=self.s.jwt_algorithms,
                audience=self.s.jwt_audience,
                issuer=self.s.jwt_issuer,
                leeway=self.s.jwt_leeway_seconds,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            metrics.AUTH_FAILURES.labels(reason="expired").inc()
            raise AuthenticationError("token expired") from exc
        except jwt.PyJWTError as exc:
            metrics.AUTH_FAILURES.labels(reason="invalid").inc()
            raise AuthenticationError("invalid token") from exc
        raw_scope = claims.get("scope") or claims.get("scp") or ""
        scopes = frozenset(raw_scope.split() if isinstance(raw_scope, str) else raw_scope)
        sub = str(claims["sub"])
        if not sub or len(sub) > 64:
            raise AuthenticationError("invalid subject")
        kind = "admin" if scopes & ADMIN_SCOPES else "operator" if scopes & OPERATOR_SCOPES else "customer"
        return Principal(
            subject=sub,
            scopes=scopes,
            kind=kind,
            method="jwt",
            claims={k: claims[k] for k in ("iss", "exp", "iat") if k in claims},
        )


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    # EventSource cannot set headers: allowed for the SSE endpoint only.
    if request.url.path.endswith("/events"):
        return request.query_params.get("access_token")
    return None


AUTH_DEPENDENCY = "__auth_dependency__"


def require_scopes(*needed: str, allow_demo: bool = False) -> Any:
    """FastAPI dependency factory: verified principal with all ``needed`` scopes."""

    async def dependency(request: Request) -> Principal:
        settings: Settings = request.app.state.settings
        token = _bearer(request)
        if token is None:
            if allow_demo and settings.auth_mode is AuthMode.DEMO and not settings.is_production:
                principal = await _demo_principal(request)
            else:
                metrics.AUTH_FAILURES.labels(reason="missing").inc()
                raise AuthenticationError("missing bearer token")
        else:
            # Off the event loop: a JWKS refresh may perform network I/O.
            principal = await asyncio.to_thread(request.app.state.verifier.verify, token)
        missing = set(needed) - principal.scopes
        if missing:
            metrics.AUTH_FAILURES.labels(reason="scope").inc()
            log.warning("auth_scope_denied", subject_kind=principal.kind, missing=sorted(missing))
            raise AuthorizationError("insufficient scope")
        request.state.principal = principal
        limiter = request.app.state.rate_limiter
        await limiter.check_subject(
            principal.subject, operator_write="cases:write" in needed or "approvals:write" in needed
        )
        return principal

    setattr(dependency, AUTH_DEPENDENCY, True)
    return Depends(dependency)


async def _demo_principal(request: Request) -> Principal:
    user_id: str | None = None
    if request.method == "POST" and request.url.path == "/v1/chat":
        try:
            body = await request.json()
            user_id = body.get("user_id") if isinstance(body, dict) else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            user_id = None
    else:
        user_id = request.headers.get("x-demo-user") or request.query_params.get("user_id")
    if not user_id:
        metrics.AUTH_FAILURES.labels(reason="missing").inc()
        raise AuthenticationError("demo mode requires user_id")
    return Principal(subject=str(user_id), scopes=CUSTOMER_SCOPES, kind="customer", method="demo")


def enforce_body_identity(principal: Principal, body_user_id: str | None) -> None:
    """The challenge payload's ``user_id`` is optional and NON-authoritative: if present it must equal ``sub``."""
    if body_user_id is not None and body_user_id != principal.subject:
        metrics.AUTH_FAILURES.labels(reason="user_id_mismatch").inc()
        log.warning("security_event", kind="user_id_mismatch", method=principal.method)
        raise AuthorizationError("user_id does not match the authenticated subject")
