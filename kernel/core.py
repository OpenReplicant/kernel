"""The kernel's interfaces (docs/KERNEL.md): sources and spans, nodes and contexts,
assertions, and queries. Domain-free: everything domain-specific arrives as data.

Node references may be given as a UUID, an IRI, or a CURIE whose prefix is loaded
(`k:plays`). Argument values that are `uuid.UUID` are nodes; anything else is a literal.
"""
import hashlib
import importlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

import psycopg
from psycopg.types.json import Jsonb

from . import vocab

METHODS = ("stated", "observed", "inferred", "computed", "defaulted")
NEEDS_EVIDENCE = ("stated", "observed")
CONTEXT_KINDS = ("world", "perspective", "system", "session", "vocabulary")
MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"
LINK_KINDS = ("supersedes", "contradicts", "corroborates", "derived_from")
NODE_KINDS = ("thing", "type", "role", "port", "capability", "predicate", "context", "agent",
              "source", "constraint")


class KernelError(Exception):
    pass


class EvidenceRequired(KernelError):
    pass


class SchemaError(KernelError):
    """An assertion doesn't fit the loaded vocabularies."""


class ValidationFailed(KernelError):
    def __init__(self, errors: list[str]):
        super().__init__(f"{len(errors)} validation error(s): " + "; ".join(errors[:5]))
        self.errors = errors


@dataclass
class Assertion:
    id: uuid.UUID
    subject: str
    predicate: str
    object: str | None
    value: Any
    context: str
    method: str
    confidence: float
    status: str
    valid_from: datetime | None
    valid_to: datetime | None
    recorded_at: datetime
    asserted_by: str
    args: dict = field(default_factory=dict)


_SELECT = """
select a.id, s.iri, p.iri, o.iri, a.value, c.iri, a.method, a.confidence, {status},
       a.valid_from, a.valid_to, a.recorded_at, ag.iri,
       coalesce((select jsonb_object_agg(x.name, coalesce(to_jsonb(xn.iri), x.value))
                 from kb.assertion_arg x left join kb.node xn on xn.id = x.node
                 where x.assertion = a.id), '{{}}'::jsonb)
from kb.assertion a
join kb.node s on s.id = a.subject
join kb.node p on p.id = a.predicate
left join kb.node o on o.id = a.object
join kb.node c on c.id = a.context
join kb.node ag on ag.id = a.asserted_by
"""


class Kernel:
    def __init__(self, conn: psycopg.Connection, data_root: Path | str | None = None):
        if not conn.autocommit:
            raise KernelError("the kernel needs an autocommit connection; it manages transactions")
        self.conn = conn
        self.data_root = Path(data_root or os.environ.get("PC_DATA", "data")).resolve()
        self.prefixes = vocab.Prefixes()
        self.schema = vocab.Schema()
        vocab.load_recorded(conn, self.prefixes, self.schema)
        self.constraints: dict[tuple[str, str], Callable] = {}
        self.views: dict[str, tuple[Callable, Callable]] = {}
        self.modules: dict[str, dict] = {}

    @classmethod
    def connect(cls, url: str | None = None, data_root=None) -> "Kernel":
        k = cls(psycopg.connect(url or os.environ["DATABASE_URL"], autocommit=True), data_root)
        k.bootstrap()
        return k

    def bootstrap(self):
        """Load the kernel vocabulary (idempotent)."""
        return vocab.load(self, vocab.KERNEL_VOCAB)

    def load_vocabulary(self, path) -> dict:
        """Register a vocabulary file's types, roles, predicates, lists and constraints."""
        try:
            return vocab.load(self, path)
        except (ValueError, KeyError) as e:
            raise SchemaError(str(e)) from None

    # -- references --------------------------------------------------------------------

    def iri(self, ref: str) -> str:
        try:
            return self.prefixes.expand(ref)
        except KeyError as e:
            raise KernelError(e.args[0]) from None

    def node_id(self, ref, kind: str | None = None) -> uuid.UUID:
        if isinstance(ref, uuid.UUID):
            row = self.conn.execute("select id, kind from kb.node where id = %s", (ref,)).fetchone()
        else:
            row = self.conn.execute("select id, kind from kb.node where iri = %s",
                                    (self.iri(ref),)).fetchone()
        if not row:
            raise KernelError(f"no node {ref!r}")
        if kind and row[1] != kind:
            raise KernelError(f"node {ref!r} is a {row[1]}, expected {kind}")
        return row[0]

    def _node(self, ref) -> tuple[uuid.UUID, str, str]:
        """(id, kind, iri) of a node reference."""
        nid = self.node_id(ref)
        return self.conn.execute("select id, kind, iri from kb.node where id = %s", (nid,)).fetchone()

    def satisfies(self, ref, type_iri: str) -> bool:
        """Does a node satisfy a type? Its kind backs the type, or it has a k:is_a (accepted
        or staged, any context) to the type or one of its subtypes."""
        nid, kind, _ = self._node(ref)
        type_iri = self.iri(type_iri)
        if kind in self.schema.kinds_backing(type_iri):
            return True
        for (t,) in self.conn.execute(
                """select o.iri from kb.assertion a join kb.node o on o.id = a.object
                   where a.subject = %s and a.predicate = (select id from kb.node where iri = %s)
                     and a.status in ('accepted', 'staged')""", (nid, self.iri("k:is_a"))):
            if type_iri in self.schema.ancestors(t):
                return True
        return False

    def _check_schema(self, predicate_iri, subject, object, value, args):
        p = self.schema.predicates.get(predicate_iri)
        if p is None:
            raise SchemaError(f"predicate {predicate_iri} is not declared by a loaded vocabulary")
        if p["domain"] and not self.satisfies(subject, p["domain"]):
            raise SchemaError(f"{predicate_iri}: subject {subject} is not a {p['domain']}")
        if p["range"] == vocab.LITERAL:
            if value is None:
                raise SchemaError(f"{predicate_iri} takes a literal value, not an object")
            allowed = self.schema.enum_values(p)
            if allowed is not None and value not in allowed:
                raise SchemaError(f"{predicate_iri}: {value!r} is not one of {allowed}")
        else:
            if object is None:
                raise SchemaError(f"{predicate_iri} takes an object node, not a literal")
            if not self.satisfies(object, p["range"]):
                raise SchemaError(f"{predicate_iri}: object {object} is not a {p['range']}")
        missing = [a for a in p.get("args", []) if a not in (args or {})]
        if missing:
            raise SchemaError(f"{predicate_iri} requires args {missing}")

    def _actor(self, agent):
        """Inside a transaction: name the acting agent for kb.status_change."""
        self.conn.execute("select set_config('kb.actor', %s, true)", (str(agent),))

    # -- sources and spans -------------------------------------------------------------

    def put_source(self, file, media_type: str, uri: str | None = None,
                   license: str | None = None) -> str:
        """Store a file by content hash under $PC_DATA/sources/ (read-only). Idempotent."""
        file = Path(file)
        sha = hashlib.sha256(file.read_bytes()).hexdigest()
        rel = Path("sources", "sha256", sha[:2], sha[2:4], sha + file.suffix)
        dest = self.data_root / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            shutil.copyfile(file, tmp)
            os.chmod(tmp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.replace(tmp, dest)
        with self.conn.transaction():
            node = self.ensure_node("source", f"urn:pc:source:sha256:{sha}", label=file.name)
            self.conn.execute(
                "insert into kb.source (sha256, node, path, uri, media_type, license)"
                " values (%s, %s, %s, %s, %s, %s) on conflict do nothing",
                (sha, node, str(rel), uri, media_type, license))
        return sha

    def source_node(self, sha256: str) -> uuid.UUID:
        row = self.conn.execute("select node from kb.source where sha256 = %s", (sha256,)).fetchone()
        if not row:
            raise KernelError(f"no source {sha256}")
        return row[0]

    def add_span(self, sha256: str, locator: dict, excerpt: str | None = None) -> uuid.UUID:
        """An addressable piece of a source. Idempotent per (source, locator)."""
        row = self.conn.execute(
            "insert into kb.span (id, source, locator, excerpt) values (%s, %s, %s, %s)"
            " on conflict (source, locator) do nothing returning id",
            (uuid.uuid4(), sha256, Jsonb(locator), excerpt)).fetchone()
        if row:
            return row[0]
        return self.conn.execute("select id from kb.span where source = %s and locator = %s",
                                 (sha256, Jsonb(locator))).fetchone()[0]

    # -- nodes and contexts ------------------------------------------------------------

    def ensure_node(self, kind: str, iri: str, label: str | None = None,
                    props: dict | None = None) -> uuid.UUID:
        """Idempotent by IRI. An existing node keeps its label and props."""
        if kind not in NODE_KINDS:
            raise KernelError(f"unknown node kind {kind!r}")
        iri = self.iri(iri)
        row = self.conn.execute(
            "insert into kb.node (id, kind, iri, label, props) values (%s, %s, %s, %s, %s)"
            " on conflict (iri) do nothing returning id",
            (uuid.uuid4(), kind, iri, label, Jsonb(props or {}))).fetchone()
        if row:
            return row[0]
        nid, existing = self.conn.execute("select id, kind from kb.node where iri = %s", (iri,)).fetchone()
        if existing != kind:
            raise KernelError(f"node {iri} already exists as a {existing}, not a {kind}")
        return nid

    def create_context(self, kind: str, label: str, closure: str | None = None, parent=None,
                       conditions: dict | None = None, iri: str | None = None) -> uuid.UUID:
        if kind not in CONTEXT_KINDS:
            raise KernelError(f"unknown context kind {kind!r}")
        closure = closure or ("closed" if kind in ("system", "vocabulary") else "open")
        if closure not in ("open", "closed"):
            raise KernelError(f"closure must be open or closed, not {closure!r}")
        props = {"ctx_kind": kind, "closure": closure,
                 "parent": str(self.node_id(parent, "context")) if parent is not None else None,
                 "conditions": conditions or {}}
        return self.ensure_node("context", iri or f"urn:pc:context:{uuid.uuid4()}", label, props)

    def context_props(self, ctx) -> dict:
        return self.conn.execute("select props from kb.node where id = %s",
                                 (self.node_id(ctx, "context"),)).fetchone()[0]

    # -- assertions --------------------------------------------------------------------

    def assert_(self, subject, predicate, object=None, value=None, *, context, method: str,
                confidence: float, asserted_by, evidence: Iterable = (), args: dict | None = None,
                valid_from=None, valid_to=None, status: str = "accepted") -> uuid.UUID:
        if (object is None) == (value is None):
            raise KernelError("give exactly one of object or value")
        if method not in METHODS:
            raise KernelError(f"unknown method {method!r}; one of {METHODS}")
        if status not in ("staged", "accepted"):
            raise KernelError("new assertions are 'staged' or 'accepted'")
        evidence = list(evidence)
        if method in NEEDS_EVIDENCE and status == "accepted" and not evidence:
            raise EvidenceRequired(f"a {method} assertion needs evidence spans (or status='staged')")
        _, pkind, piri = self._node(predicate)
        if pkind != "predicate":
            raise KernelError(f"node {predicate!r} is a {pkind}, expected predicate")
        self._check_schema(piri, subject, object, value, args)
        aid = uuid.uuid4()
        agent = self.node_id(asserted_by, "agent")
        with self.conn.transaction():
            self._actor(agent)
            self.conn.execute(
                "insert into kb.assertion (id, subject, predicate, object, value, context, method,"
                " confidence, status, valid_from, valid_to, asserted_by)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (aid, self.node_id(subject), self.node_id(predicate, "predicate"),
                 self.node_id(object) if object is not None else None,
                 Jsonb(value) if value is not None else None,
                 self.node_id(context, "context"), method, confidence, status,
                 valid_from, valid_to, agent))
            for name, v in (args or {}).items():
                node = self.node_id(v) if isinstance(v, uuid.UUID) else None
                self.conn.execute(
                    "insert into kb.assertion_arg (assertion, name, node, value) values (%s, %s, %s, %s)",
                    (aid, name, node, None if node else Jsonb(v)))
            for span in evidence:
                self.conn.execute("insert into kb.evidence (assertion, span) values (%s, %s)", (aid, span))
        return aid

    def add_evidence(self, assertion_id, spans: Iterable, by):
        """Attach more spans to an assertion (e.g. a staged one whose source just arrived)."""
        with self.conn.transaction():
            self._actor(self.node_id(by, "agent"))
            for span in spans:
                self.conn.execute("insert into kb.evidence (assertion, span) values (%s, %s)"
                                  " on conflict do nothing", (assertion_id, span))

    def _set_status(self, assertion_id, status: str, by, reason: str | None = None):
        self._actor(self.node_id(by, "agent"))
        self.conn.execute("select set_config('kb.reason', %s, true)", (reason or "",))
        n = self.conn.execute("update kb.assertion set status = %s where id = %s",
                              (status, assertion_id)).rowcount
        if n != 1:
            raise KernelError(f"no assertion {assertion_id}")

    def retract(self, assertion_id, reason: str, by):
        with self.conn.transaction():
            self._set_status(assertion_id, "retracted", by, reason)

    def supersede(self, old_id, *, asserted_by, reason: str | None = None, **changes) -> uuid.UUID:
        """Replace an assertion: the new one copies the old's fields and arguments except
        those given in `changes` (evidence is never copied), links `supersedes` to it, and
        the old one becomes `superseded`, all in one transaction."""
        old = self.get(old_id)
        fields = dict(subject=old.subject, predicate=old.predicate, context=old.context,
                      method=old.method, confidence=old.confidence, valid_from=old.valid_from,
                      valid_to=old.valid_to, args=self._raw_args(old_id))
        if "object" in changes or "value" in changes:
            fields.update(object=None, value=None)
        else:
            fields.update(object=old.object, value=old.value)
        fields.update(changes)
        with self.conn.transaction():
            new_id = self.assert_(asserted_by=asserted_by, **fields)
            self.link(new_id, old_id, "supersedes")
            self._set_status(old_id, "superseded", asserted_by, reason)
        return new_id

    def _raw_args(self, assertion_id) -> dict:
        return {name: (node if node is not None else value) for name, node, value in self.conn.execute(
            "select name, node, value from kb.assertion_arg where assertion = %s", (assertion_id,))}

    def link(self, from_id, to_id, kind: str):
        if kind not in LINK_KINDS:
            raise KernelError(f"unknown link kind {kind!r}")
        self.conn.execute("insert into kb.assertion_link (from_id, to_id, kind) values (%s, %s, %s)"
                          " on conflict do nothing", (from_id, to_id, kind))

    # -- queries -----------------------------------------------------------------------

    def get(self, assertion_id) -> Assertion:
        row = self.conn.execute(_SELECT.format(status="a.status") + " where a.id = %s",
                                (assertion_id,)).fetchone()
        if not row:
            raise KernelError(f"no assertion {assertion_id}")
        return Assertion(*row)

    def subcontexts(self, ctx) -> list[uuid.UUID]:
        root = self.node_id(ctx, "context")
        return [r[0] for r in self.conn.execute(
            """with recursive t(id) as (select %s::uuid
                 union select n.id from kb.node n join t on n.props->>'parent' = t.id::text
                 where n.kind = 'context')
               select id from t""", (root,))]

    def query(self, subject=None, predicate=None, object=None, context=None,
              include_subcontexts: bool = True, status=("accepted",), method=None,
              valid_at: datetime | None = None, known_at: datetime | None = None) -> list[Assertion]:
        """Assertions matching the filters. `known_at` answers "what did the kernel believe at
        time T": only rows recorded by T, with the status each had at T. `valid_at` answers
        "what was true at T" by valid time (unknown bounds count as open)."""
        status = (status,) if isinstance(status, str) else tuple(status)
        if known_at is None:
            sql, params = _SELECT.format(status="a.status") + " where a.status = any(%s)", [list(status)]
        else:
            st = ("(select sc.status from kb.status_change sc where sc.assertion = a.id"
                  " and sc.at <= %s order by sc.at desc, sc.id desc limit 1)")
            sql = _SELECT.format(status=st) + f" where a.recorded_at <= %s and {st} = any(%s)"
            params = [known_at, known_at, known_at, list(status)]
        for col, ref, kind in (("a.subject", subject, None), ("a.predicate", predicate, "predicate"),
                               ("a.object", object, None)):
            if ref is not None:
                sql += f" and {col} = %s"
                params.append(self.node_id(ref, kind))
        if context is not None:
            ctxs = self.subcontexts(context) if include_subcontexts else [self.node_id(context, "context")]
            sql += " and a.context = any(%s)"
            params.append(ctxs)
        if method is not None:
            methods = [method] if isinstance(method, str) else list(method)
            sql += " and a.method = any(%s)"
            params.append(methods)
        if valid_at is not None:
            sql += " and (a.valid_from is null or a.valid_from <= %s) and (a.valid_to is null or a.valid_to > %s)"
            params += [valid_at, valid_at]
        sql += " order by a.recorded_at, a.id"
        return [Assertion(*r) for r in self.conn.execute(sql, params)]

    def why(self, assertion_id, depth: int = 3) -> dict:
        """Provenance tree: the assertion, who wrote it and how, its evidence (span, source,
        and who produced the source), its status history, and its links; inputs of
        `derived_from` and the assertion it `supersedes` are expanded recursively."""
        a = self.get(assertion_id)
        evidence = []
        for span_id, locator, excerpt, sha, path, uri, media, src_node in self.conn.execute(
                """select sp.id, sp.locator, sp.excerpt, so.sha256, so.path, so.uri, so.media_type, so.node
                   from kb.evidence e join kb.span sp on sp.id = e.span join kb.source so on so.sha256 = sp.source
                   where e.assertion = %s order by sp.id""", (assertion_id,)):
            producers = [{"agent": p.object, "as": p.args.get("as"), "method": p.method}
                         for p in self.query(subject=src_node, predicate="k:produced_by")]
            evidence.append({"span": str(span_id), "locator": locator, "excerpt": excerpt,
                             "source": {"sha256": sha, "path": path, "uri": uri, "media_type": media,
                                        "produced_by": producers}})
        history = [{"status": s, "at": at.isoformat(), "by": by, "reason": reason}
                   for s, at, by, reason in self.conn.execute(
                       """select sc.status, sc.at, n.iri, sc.reason from kb.status_change sc
                          left join kb.node n on n.id = sc.by where sc.assertion = %s order by sc.id""",
                       (assertion_id,))]
        links = []
        for direction, sql in (("out", "select kind, to_id from kb.assertion_link where from_id = %s"),
                               ("in", "select kind, from_id from kb.assertion_link where to_id = %s")):
            for kind, other in self.conn.execute(sql, (assertion_id,)):
                entry = {"kind": kind, "direction": direction, "assertion": str(other)}
                if direction == "out" and kind in ("derived_from", "supersedes") and depth > 0:
                    entry["why"] = self.why(other, depth - 1)
                links.append(entry)
        return {"id": str(a.id), "subject": a.subject, "predicate": a.predicate, "object": a.object,
                "value": a.value, "args": a.args, "context": a.context, "method": a.method,
                "confidence": a.confidence, "status": a.status, "asserted_by": a.asserted_by,
                "recorded_at": a.recorded_at.isoformat(),
                "valid_from": a.valid_from.isoformat() if a.valid_from else None,
                "valid_to": a.valid_to.isoformat() if a.valid_to else None,
                "evidence": evidence, "status_history": history, "links": links}

    def bindings(self, context) -> dict[str, list[str]]:
        """role IRI -> IRIs of the things that play it in this context (and sub-contexts)."""
        out: dict[str, list[str]] = {}
        for a in self.query(predicate="k:plays", context=context):
            out.setdefault(a.object, []).append(a.subject)
        return out

    def couplings(self, context) -> list[dict]:
        """The port graph: each coupling with the roles owning its two ports."""
        owner = {a.object: a.subject for a in self.query(predicate="k:has_port", context=context)}
        return [{"from": a.subject, "from_role": owner.get(a.subject), "to": a.object,
                 "to_role": owner.get(a.object), "kind": a.args.get("kind"), "args": a.args}
                for a in self.query(predicate="k:couples", context=context)]

    # -- validation and promotion ------------------------------------------------------

    def register_constraint(self, vocabulary: str, name: str, fn: Callable):
        """A Python check for a `{kind: python, name}` constraint in `vocabulary`.
        fn(kernel, context_id, assertions) -> list of error strings."""
        self.constraints[(vocabulary, name)] = fn

    def validate(self, context) -> list[str]:
        """Check a context (with sub-contexts), over accepted and staged assertions, against
        each predicate's `max` and the constraints of every vocabulary this kernel instance
        has loaded. A context using a vocabulary that isn't loaded is an error, not a pass."""
        ctx = self.node_id(context, "context")
        ctx_kind = self.context_props(ctx).get("ctx_kind")
        rows = self.query(context=ctx, status=("accepted", "staged"))
        errors = []
        counts: dict[tuple[str, str], int] = {}
        for a in rows:
            counts[(a.subject, a.predicate)] = counts.get((a.subject, a.predicate), 0) + 1
        for (subj, pred), n in counts.items():
            mx = self.schema.predicates.get(pred, {}).get("max")
            if mx is not None and n > mx:
                errors.append(f"{subj} has {n} {pred} (max {mx})")
        used = {self.schema.predicates[a.predicate]["vocab"] for a in rows if a.predicate in self.schema.predicates}
        used |= {self.schema.roles[a.object]["vocab"] for a in rows if a.object in self.schema.roles}
        for name in sorted(used - self.schema.active):
            errors.append(f"context uses vocabulary {name!r}, which this kernel hasn't loaded "
                          "(load its module to validate)")
        for vocab_name, c in self.schema.constraints:
            if vocab_name not in self.schema.active:
                continue
            if c.get("in") and c["in"] != ctx_kind:
                continue
            if c["kind"] == "required":
                pred, typ = self.iri(c["predicate"]), self.iri(c["for"])
                have = {a.subject for a in rows if a.predicate == pred}
                for subj in sorted({a.subject for a in rows} - have):
                    if self.satisfies(subj, typ):
                        errors.append(f"{subj} ({typ}) has no {pred}")
            elif c["kind"] == "python":
                fn = self.constraints.get((vocab_name, c["name"]))
                if fn is None:
                    errors.append(f"constraint {vocab_name}:{c['name']} is declared but not registered")
                else:
                    errors.extend(fn(self, ctx, rows))
            else:
                errors.append(f"unknown constraint kind {c['kind']!r} in {vocab_name}")
        return errors

    def promote(self, context, by) -> int:
        """Accept the context's staged assertions if the context validates. Returns how many."""
        errors = self.validate(context)
        if errors:
            raise ValidationFailed(errors)
        staged = [a.id for a in self.query(context=context, status=("staged",))]
        with self.conn.transaction():
            for aid in staged:
                self._set_status(aid, "accepted", by, "promoted")
        return len(staged)

    # -- views -------------------------------------------------------------------------

    def register_view(self, name: str, export_fn: Callable, import_fn: Callable):
        """A document format backed by contexts: export_fn(kernel, context, **kw) -> document,
        import_fn(kernel, document, **kw) -> context id."""
        self.views[name] = (export_fn, import_fn)

    def export(self, view: str, context, **kw):
        return self._view(view)[0](self, context, **kw)

    def import_(self, view: str, document, **kw):
        return self._view(view)[1](self, document, **kw)

    def _view(self, name):
        if name not in self.views:
            raise KernelError(f"no view {name!r} registered (load the module that provides it)")
        return self.views[name]

    # -- modules -----------------------------------------------------------------------

    def load_module(self, name: str, search: Iterable | None = None) -> dict:
        """Load a module: its dependencies, then its vocabularies, then its register(kernel).
        Modules are directories with a module.yaml, found under $PC_MODULES (default:
        ./modules next to the kernel package)."""
        if name in self.modules:
            return self.modules[name]
        roots = [Path(p) for p in (search or os.environ.get("PC_MODULES", str(MODULES_DIR)).split(os.pathsep))]
        where = next((r / name for r in roots if (r / name / "module.yaml").exists()), None)
        if where is None:
            raise KernelError(f"module {name!r} not found under {[str(r) for r in roots]}")
        manifest = yaml.safe_load((where / "module.yaml").read_text())
        for dep in manifest.get("depends", []):
            self.load_module(dep, roots)
        for v in manifest.get("vocab", []):
            self.load_vocabulary(where / v)
        if manifest.get("python"):
            importlib.import_module(manifest["python"]).register(self)
        self.modules[name] = {**manifest, "path": str(where)}
        return self.modules[name]
