"""Mint an HS256 JWT that PostgREST accepts for a database role.

Usage:
  KK_JWT_SECRET=... python scripts/mint_jwt.py <db_role> [--days N]

PostgREST switches to the role named in the `role` claim. The role must exist, be granted to
kk_authenticator, and be mapped to an agent in kernel.agent. Standard library only.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint(role: str, secret: str, days: float) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {"role": role, "iat": now, "exp": now + int(days * 86400)}
    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(sig)}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("role")
    p.add_argument("--days", type=float, default=30)
    a = p.parse_args()
    secret = os.environ.get("KK_JWT_SECRET", "")
    if len(secret) < 32:
        print("KK_JWT_SECRET must be set and at least 32 characters", file=sys.stderr)
        return 2
    print(mint(a.role, secret, a.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
