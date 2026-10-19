"""Operator CLI — a thin client over the operator API (same endpoints as the /operator console).

make operator ARGS="list"
make operator ARGS="show CASE-20261019-0001"
make operator ARGS="claim CASE-..."
make operator ARGS="reply CASE-... 'Verifiquei e o repasse sai amanhã.'"
make operator ARGS="approve CASE-... 'Troca aprovada'"   |  reject CASE-... 'motivo'
make operator ARGS="resolve CASE-... RESOLVED_BY_OPERATOR"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

from app.api.dev_idp import mint


def _client(base: str, operator: str) -> httpx.Client:
    token = os.environ.get("OPERATOR_TOKEN") or mint(
        operator, "cases:read cases:write approvals:write", private_key_path=".dev-keys/dev-private.pem"
    )
    return httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=180)


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> int:
    p = argparse.ArgumentParser(description="Getnet support operator CLI")
    p.add_argument("--base-url", default=os.environ.get("API_URL", "http://localhost:8000"))
    p.add_argument("--operator", default=os.environ.get("OPERATOR_ID", "operator.ana"))
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    for name in ("show", "claim"):
        sub.add_parser(name).add_argument("case_id")
    r = sub.add_parser("reply")
    r.add_argument("case_id")
    r.add_argument("message")
    r.add_argument("--as-is", action="store_true")
    for name in ("approve", "reject"):
        d = sub.add_parser(name)
        d.add_argument("case_id")
        d.add_argument("note", nargs="?", default="")
    e = sub.add_parser("edit")
    e.add_argument("case_id")
    e.add_argument("arguments", help='JSON, e.g. {"terminal_id": 1001}')
    e.add_argument("note", nargs="?", default="")
    rs = sub.add_parser("resolve")
    rs.add_argument("case_id")
    rs.add_argument("code", nargs="?", default="RESOLVED_BY_OPERATOR")
    a = p.parse_args()
    with _client(a.base_url, a.operator) as c:
        if a.cmd == "list":
            resp = c.get("/v1/operator/cases", params={"status": a.status} if a.status else None)
            if resp.is_success:
                for case in resp.json()["items"]:
                    flag = f"[{case['interrupt_kind']}]" if case["awaiting_human"] else ""
                    print(
                        f"{case['case_id']:<24} {case['priority']:<7} {case['status']:<18} {case['customer_id']:<12} {flag:<11} {case['subject'][:60]}"
                    )
                return 0
        elif a.cmd == "show":
            resp = c.get(f"/v1/operator/cases/{a.case_id}")
        elif a.cmd == "claim":
            resp = c.post(f"/v1/operator/cases/{a.case_id}/claim")
        elif a.cmd == "reply":
            resp = c.post(f"/v1/operator/cases/{a.case_id}/reply", json={"message": a.message, "send_as_is": a.as_is})
        elif a.cmd in ("approve", "reject"):
            resp = c.post(f"/v1/operator/cases/{a.case_id}/decision", json={"decision": a.cmd, "note": a.note})
        elif a.cmd == "edit":
            resp = c.post(
                f"/v1/operator/cases/{a.case_id}/decision",
                json={"decision": "edit", "edited_arguments": json.loads(a.arguments), "note": a.note},
            )
        else:
            resp = c.post(f"/v1/operator/cases/{a.case_id}/resolve", json={"resolution_code": a.code, "note": ""})
        _print(resp.json())
        return 0 if resp.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
