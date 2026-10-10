"""Shared runner for tool-shaped scripts: one JSON object in on stdin, validated against
scripts/schemas/<name>.in.json; one JSON object out on stdout, validated against
<name>.out.json. Errors go to stderr with exit code 1."""
import json
import sys
import traceback
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import Kernel  # noqa: E402

SCHEMAS = ROOT / "scripts" / "schemas"


def _validator(name: str) -> Draft202012Validator:
    defs = json.loads((SCHEMAS / "_defs.json").read_text())
    registry = Registry().with_resource(defs["$id"], Resource.from_contents(defs))
    return Draft202012Validator(json.loads((SCHEMAS / name).read_text()), registry=registry,
                                format_checker=Draft202012Validator.FORMAT_CHECKER)


def ref(s: str):
    """A node reference from JSON: UUID strings become UUIDs, anything else is an IRI/CURIE."""
    try:
        return uuid.UUID(s)
    except ValueError:
        return s


def time(s: str | None):
    return datetime.fromisoformat(s) if s else None


def jsonable(obj):
    if isinstance(obj, (uuid.UUID, datetime)):
        return str(obj) if isinstance(obj, uuid.UUID) else obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        return jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj


def run_tool(name: str, fn):
    try:
        inp = json.loads(sys.stdin.read())
        _validator(f"{name}.in.json").validate(inp)
        k = Kernel.connect()
        try:
            out = jsonable(fn(k, inp))
        finally:
            k.conn.close()
        _validator(f"{name}.out.json").validate(out)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
