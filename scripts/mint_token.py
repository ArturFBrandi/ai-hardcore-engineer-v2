"""CLI for the development identity provider: ``make keys`` / ``make token SUB=... SCOPE=...``."""

from __future__ import annotations

import argparse
import sys

from app.api.dev_idp import generate_keys, mint


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keys")
    k.add_argument("--dir", default=".dev-keys")
    k.add_argument("--force", action="store_true")
    t = sub.add_parser("token")
    t.add_argument("--sub", required=True)
    t.add_argument("--scope", default="chat conversations:read conversations:delete")
    t.add_argument("--ttl", type=int, default=3600)
    t.add_argument("--key", default=".dev-keys/dev-private.pem")
    a = p.parse_args()
    if a.cmd == "keys":
        print(f"dev keys ready in {generate_keys(a.dir, force=a.force)}", file=sys.stderr)
    else:
        print(mint(a.sub, a.scope, ttl_seconds=a.ttl, private_key_path=a.key))


if __name__ == "__main__":
    main()
