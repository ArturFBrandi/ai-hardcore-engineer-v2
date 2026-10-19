"""Development identity provider (non-production only): RSA key generation and token minting.

Keys live in the git-ignored ``.dev-keys/`` directory and are mounted read-only into the API container.
In production the JWKS comes from a real OIDC provider (``JWT_JWKS_URL``) and this module is unused.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.config import Settings, get_settings


def generate_keys(directory: str, kid: str = "dev-key-1", force: bool = False) -> Path:
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    priv_path, jwks_path = out / "dev-private.pem", out / "jwks.json"
    if priv_path.exists() and jwks_path.exists() and not force:
        return out
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    priv_path.chmod(0o600)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    jwks_path.write_text(json.dumps({"keys": [jwk]}, indent=2))
    jwks_path.chmod(0o644)
    priv_path.chmod(0o644)  # readable by the non-root container user (dev-only key, never committed)
    return out


def mint(
    sub: str,
    scope: str,
    *,
    settings: Settings | None = None,
    ttl_seconds: int = 3600,
    private_key_path: str | None = None,
    kid: str | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    s = settings or get_settings()
    if s.is_production:
        raise SystemExit("refusing to mint development tokens in production")
    key = Path(private_key_path or s.jwt_dev_private_key_path).read_bytes()
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "sub": sub,
        "scope": scope,
        "iat": now,
        "nbf": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    claims.update(extra or {})
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid or s.jwt_dev_kid})
