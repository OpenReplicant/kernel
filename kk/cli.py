"""kk: the one command for running a knowledge kernel.

  kk up [PROJECT]              connect a project folder to a kernel: stack, logins, MCP, skill, hooks
  kk init                      install the kernel, the default modules and the vocabulary packs
  kk person add NAME           a person's own login (reviews; --steward may also redact)
  kk agent add NAME            a harness agent's own login (MCP stdio, hooks)
  kk serve                     MCP over HTTP (+ the UI at /ui) + the validator + the belief model, in one process
  kk status                    who you are, what you can see, what is waiting
  kk review list|show|approve|reject
  kk scan --repo DIR           ingest a repository (self-encoder, ingest filter first)
  kk module install DIR        a module from this repository: migration, own login, sandboxed container
  kk export / kk import FILE   every claim with its provenance as N-Quads (scopes as named graphs)
  kk token ROLE                a bearer token for MCP over HTTP

Logins. Admin commands (init, person, agent, module, export, import) use KK_ADMIN_URL, a superuser connection string (default:
the libpq PG* variables). serve uses DATABASE_URL (kk_authenticator, which switches to each module's
role). status, review and the agent's harness use the caller's own login (KK_DATABASE_URL, else
DATABASE_URL). Agents never get the authenticator or admin logins (D-37).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg import sql

ROOT = Path(os.environ.get("KK_ROOT") or Path(__file__).resolve().parents[1])
NAME = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
WORKERS = ("validator", "belief")
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
KK_MODULE = "https://w3id.org/knowledge-kernel/ns#Module"
OWL_VERSION = "http://www.w3.org/2002/07/owl#versionInfo"


# ---------------------------------------------------------------- logins
def admin() -> psycopg.Connection:
    return psycopg.connect(os.environ.get("KK_ADMIN_URL", ""), autocommit=True)


def own_login() -> psycopg.Connection:
    url = os.environ.get("KK_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set KK_DATABASE_URL (or DATABASE_URL) to your own login")
    return psycopg.connect(url, autocommit=True)


def url_for(conn: psycopg.Connection, user: str, password: str) -> str:
    """A connection URL for `user` on the same server and database as `conn`."""
    host, port, db = conn.info.host, conn.info.port, conn.info.dbname
    auth = f"{quote(user)}:{quote(password, safe='')}"
    if host.startswith("/"):
        return f"postgresql://{auth}@/{quote(db)}?host={quote(host, safe='')}&port={port}"
    return f"postgresql://{auth}@{host}:{port}/{quote(db)}"


def password(a) -> str | None:
    if a.no_login:
        return None
    if a.password_env:
        return os.environ.get(a.password_env) or None
    return secrets.token_urlsafe(24)


def add_identity(conn, role: str, iri: str, kind: str, label: str, groups: list[str], pw: str | None) -> dict:
    """Create (or update) a login role acting only as itself, mapped to an agent IRI. Idempotent."""
    exists = conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
    if not exists:
        conn.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
    for g in groups:
        conn.execute(sql.SQL("grant {} to {}").format(sql.Identifier(g), sql.Identifier(role)))
    # Services (kernel-mcp over HTTP) act as it after checking a token; the role itself cannot switch.
    conn.execute(sql.SQL("grant {} to kk_authenticator").format(sql.Identifier(role)))
    if pw:
        conn.execute(sql.SQL("alter role {} login password {}").format(sql.Identifier(role), sql.Literal(pw)))
    with conn.transaction():
        conn.execute("set local role kk_owner")
        conn.execute("insert into kernel.agent(iri, db_role, kind, label) values (%s, %s, %s, %s)"
                     " on conflict (iri) do nothing", (iri, role, kind, label))
    out = {"iri": iri, "role": role, "login": bool(pw)}
    if pw:
        out.update(password=pw, database_url=url_for(conn, role, pw))
    return out


def cmd_person(a) -> None:
    groups = ["kk_reviewer"] + (["kk_steward"] if a.steward else [])
    with admin() as conn:
        out = add_identity(conn, f"person_{a.name.replace('-', '_')}", f"urn:kk:agent:person:{a.name}", "person",
                           a.label or a.name, groups, password(a))
    print(json.dumps(out))


def cmd_agent(a) -> None:
    with admin() as conn:
        out = add_identity(conn, f"agent_{a.name.replace('-', '_')}", f"urn:kk:agent:{a.name}", "software",
                           a.label or a.name, ["kk_agent"], password(a))
    print(json.dumps(out))


# ---------------------------------------------------------------- init
def migrations(packs: bool = True) -> list[Path]:
    """Kernel v0, then each default module's migration (role + agent row), then the vocabulary packs."""
    mods = sorted(p for p in ROOT.glob("modules/*/migration.sql") if p.parent.name != "_template")
    return [ROOT / "kernel/schema/001_kernel.sql", ROOT / "kernel/schema/002_bootstrap.sql", *mods,
            *([ROOT / "modules/pack-loader/packs.sql"] if packs else [])]


def cmd_init(a) -> None:
    with admin() as conn:
        installed = conn.execute("select to_regclass('kernel.agent') is not null").fetchone()[0]
    if installed:
        print("kernel already installed; nothing to do", file=sys.stderr)
    else:
        for f in migrations(packs=not a.no_packs):
            with admin() as conn:  # a fresh session per file: files SET ROLE
                conn.execute("set client_min_messages = warning")
                conn.execute(f.read_text())
            print(f"applied {f.relative_to(ROOT)}", file=sys.stderr)
    with admin() as conn:
        pw = os.environ.get("KK_AUTHENTICATOR_PASSWORD")
        if pw:
            conn.execute(sql.SQL("alter role kk_authenticator password {}").format(sql.Literal(pw)))
        # The local owner reviews and may redact; `kk person add owner` gives them a login.
        add_identity(conn, "person_owner", "urn:kk:agent:person:owner", "person", "Local owner",
                     ["kk_reviewer", "kk_steward"], None)
    print(json.dumps({"installed": not installed, "authenticator_password": bool(pw)}))


# ---------------------------------------------------------------- serve
def worker(name: str):
    if name == "validator":
        from validator.__main__ import main
    else:
        from belief_corroboration.__main__ import main
    return main


def cmd_serve(a) -> None:
    parts = [p.strip() for p in a.only.split(",") if p.strip()] if a.only else ["mcp", *WORKERS]
    unknown = set(parts) - {"mcp", *WORKERS}
    if unknown:
        sys.exit(f"unknown part(s): {', '.join(sorted(unknown))}")

    def guard(name, main):
        try:
            main()
        except BaseException:  # a dead worker takes the process down, so the orchestrator restarts it
            traceback.print_exc()
            os._exit(1)

    os.environ.pop("KK_ROLE", None)  # each worker switches to its own role
    for name in parts:
        if name in WORKERS:
            threading.Thread(target=guard, args=(name, worker(name)), name=name, daemon=True).start()
    if "mcp" in parts:
        from kernel_mcp.__main__ import http

        from .ui import routes
        http(host=a.host, port=a.port, extra_routes=routes(ROOT))
    else:
        while True:
            time.sleep(3600)


# ---------------------------------------------------------------- status
def cmd_status(a) -> None:
    with own_login() as conn:
        one = lambda q: conn.execute(q).fetchone()[0]  # noqa: E731
        out = {
            "login": one("select current_user"),
            "agent": one("select kernel.my_agent()"),
            "claims_visible": one("select count(*) from api.claims where status <> 'deprecated'"),
            "change_sets_visible": dict(conn.execute(
                "select status, count(*) from api.change_sets group by status order by status").fetchall()),
            "awaiting_review": one("select count(*) from api.review_queue"),
            "modules": dict(conn.execute(
                "select v.subject, v.object_value->>'@value' from api.claims v join api.claims t"
                " on t.subject = v.subject and t.predicate = %s and t.object_entity = %s and t.status <> 'deprecated'"
                " where v.predicate = %s and v.status <> 'deprecated' order by v.subject",
                (RDF_TYPE, KK_MODULE, OWL_VERSION)).fetchall()),
        }
    print(json.dumps(out, indent=2, default=str))


# ---------------------------------------------------------------- token
def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint(role: str, secret: str, days: float) -> str:
    """HS256 JWT with a `role` claim, as kernel-mcp --http verifies it (KK_JWT_SECRET)."""
    now = int(time.time())
    head = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64url(json.dumps({"role": role, "iat": now, "exp": now + int(days * 86400)},
                             separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64url(sig)}"


def cmd_token(a) -> None:
    secret = os.environ.get("KK_JWT_SECRET", "")
    if len(secret) < 32:
        sys.exit("KK_JWT_SECRET must be set and at least 32 characters")
    print(mint(a.role, secret, a.days))


# ---------------------------------------------------------------- main
def name(value: str) -> str:
    if not NAME.match(value):
        raise argparse.ArgumentTypeError("lowercase letters, digits and '-', starting with a letter")
    return value


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    # Pass-through subcommands keep their own argument parsers.
    if argv[:1] == ["review"]:
        from .review import main as review
        return review(argv[1:])
    if argv[:1] in (["export"], ["import"]):
        from .transfer import main as transfer
        return transfer(argv[0], argv[1:])
    if argv[:1] == ["up"]:
        from .up import main as up
        return up(argv[1:])
    if argv[:1] == ["module"]:
        from .modules import main as module
        return module(argv[1:])
    if argv[:1] == ["scan"]:
        from self_encoder.__main__ import main as scan
        sys.exit(scan(["scan", *argv[1:]]))

    ap = argparse.ArgumentParser(prog="kk", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.splitlines()[2:]))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("up", help="connect a project folder to a kernel in one command")
    p = sub.add_parser("init", help="install the kernel, default modules and packs")
    p.add_argument("--no-packs", action="store_true", help="leave out the vocabulary packs (e.g. to import a kk export)")
    p.set_defaults(fn=cmd_init)
    for kind, fn in (("person", cmd_person), ("agent", cmd_agent)):
        p = sub.add_parser(kind, help=f"add an own login for a {kind}").add_subparsers(dest="action", required=True)
        p = p.add_parser("add")
        p.add_argument("name", type=name)
        p.add_argument("--label")
        p.add_argument("--password-env", help="take the password from this variable instead of generating one")
        p.add_argument("--no-login", action="store_true", help="identity only; no password")
        if kind == "person":
            p.add_argument("--steward", action="store_true", help="may also redact")
        p.set_defaults(fn=fn)
    p = sub.add_parser("serve", help="MCP over HTTP + validator + belief")
    p.add_argument("--only", help="comma-separated parts: mcp, validator, belief (default: all)")
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    p.set_defaults(fn=cmd_serve)
    sub.add_parser("status", help="who you are, what you can see, what is waiting").set_defaults(fn=cmd_status)
    sub.add_parser("review", help="review lane: list, show, approve, reject")
    sub.add_parser("scan", help="ingest a repository (--repo DIR)")
    sub.add_parser("module", help="install a module from this repository, sandboxed")
    sub.add_parser("export", help="every claim with provenance as N-Quads, scopes as named graphs")
    sub.add_parser("import", help="claims from a kk export, keeping attribution")
    p = sub.add_parser("token", help="bearer token for MCP over HTTP (needs KK_JWT_SECRET)")
    p.add_argument("role")
    p.add_argument("--days", type=float, default=30)
    p.set_defaults(fn=cmd_token)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
