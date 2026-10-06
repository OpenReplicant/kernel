"""Sign-in tokens for the explorer (ADR 0030): a token for one person, signed with the
stack's `WMK_JWT_SECRET`, that PostgREST verifies before switching to kernel_approver.

    WMK_JWT_SECRET=... python -m kernel.token --email you@example.org [--name "You"] [--days 7]

An operator's tool. Anyone holding the secret can mint a token for anyone, so it lives in the
operator's environment and never in an agent's. The token is HS256 (RFC 7519) with the
claims `role` (kernel_approver), `email`, `name`, `iat` and `exp`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from typing import Any

ROLE = "kernel_approver"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TokenError(ValueError):
    """A token that cannot be made: no secret, a short one, or no email."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint(
    secret: str, email: str, *, name: str | None = None, days: float = 7, now: float | None = None
) -> str:
    """A signed token naming `email` for `days` days."""
    if len(secret) < 32:
        raise TokenError("WMK_JWT_SECRET must be at least 32 characters (PostgREST refuses shorter ones)")
    email = email.strip().lower()
    if not EMAIL.match(email):
        raise TokenError(f"{email!r} is not an email address")
    issued = int(time.time() if now is None else now)
    claims: dict[str, Any] = {"role": ROLE, "email": email, "iat": issued, "exp": issued + int(days * 86400)}
    if name:
        claims["name"] = name
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a sign-in token for the explorer (operators only).")
    parser.add_argument("--email", required=True, help="the person's email: their identity in the kernel")
    parser.add_argument("--name", help="their name, used when their agent registers")
    parser.add_argument("--days", type=float, default=7, help="how long the token is valid (default 7)")
    args = parser.parse_args()
    try:
        print(mint(os.environ.get("WMK_JWT_SECRET", ""), args.email, name=args.name, days=args.days))
    except TokenError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
