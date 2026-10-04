"""One test (or more) per invariant in CLAUDE.md."""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from kernel.testing import KernelDB, Rejected, login_dsn

ROOT = Path(__file__).resolve().parent.parent
LOG_TABLES = ["log", "claims", "assertions", "sources", "chunks", "cites"]
WRITE_FUNCTIONS = {"write", "ingest_source", "cite"}


# 1. One write path ----------------------------------------------------------------------


def test_writer_role_can_only_execute_the_three_write_functions(kdb: KernelDB) -> None:
    rows = kdb.q(
        "SELECT p.proname, has_function_privilege('kernel_writer', p.oid, 'EXECUTE') "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'kernel'"
    )
    executable = {name for name, allowed in rows if allowed}
    assert executable == WRITE_FUNCTIONS
    tables = [r[0] for r in kdb.q("SELECT tablename FROM pg_tables WHERE schemaname IN ('kernel', 'world')")]
    for table in tables:
        schema = (
            "world"
            if kdb.one("SELECT 1 FROM pg_tables WHERE schemaname = 'world' AND tablename = %s", [table])
            else "kernel"
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not kdb.one(
                "SELECT has_table_privilege('kernel_writer', format('%%I.%%I', %s::text, %s::text), %s)",
                [schema, table, privilege],
            ), (table, privilege)


def test_reader_role_can_only_select(kdb: KernelDB, agent: str, dbname: str) -> None:
    rows = kdb.q(
        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'kernel' AND has_function_privilege('kernel_reader', p.oid, 'EXECUTE')"
    )
    assert not WRITE_FUNCTIONS & {r[0] for r in rows}
    assert not {"project_ops", "rebuild", "touch", "new_id", "install_pack"} & {r[0] for r in rows}
    with psycopg.connect(login_dsn(dbname, "wmk_reader"), autocommit=True) as conn:
        for statement in (
            "INSERT INTO kernel.nodes (id) VALUES ('x')",
            "UPDATE kernel.edges SET belief_status = 'accepted'",
            "DELETE FROM kernel.claim_redactions",
            "INSERT INTO world.\"Entity\" (properties) VALUES ('{}')",
            "SELECT kernel.write('{}'::jsonb, NULL)",
            "SELECT kernel.project_ops('{}'::jsonb)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_projection_functions_are_not_reachable_by_the_writer(kdb: KernelDB, dbname: str) -> None:
    with psycopg.connect(login_dsn(dbname, "wmk_writer"), autocommit=True) as conn:
        for statement in (
            "SELECT kernel.project_ops('{}'::jsonb)",
            "SELECT kernel.rebuild()",
            "SELECT * FROM kernel.nodes",
            "INSERT INTO kernel.log DEFAULT VALUES",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_gateway_code_issues_no_insert_update_or_delete() -> None:
    pattern = re.compile(
        r"\b(INSERT\s+INTO|UPDATE\s+[\w.\"]+\s+SET|DELETE\s+FROM|TRUNCATE|COPY\s+[\w.]+\s+FROM)\b",
        re.IGNORECASE,
    )
    for path in (ROOT / "gateway").glob("*.py"):
        assert not pattern.search(path.read_text()), path.name


# 2. The log is append-only ------------------------------------------------------------------


@pytest.mark.parametrize("table", LOG_TABLES)
def test_log_tables_refuse_update_delete_truncate_even_for_superuser(
    kdb: KernelDB, agent: str, table: str
) -> None:
    chunk = kdb.source(agent, "A source.")
    result = kdb.claim(agent, "A claim", [{"op": "create", "kind": "concept", "name": "Thing"}], source=chunk)
    kdb.cite({"sentences": [{"text": "Thing exists.", "claims": [result["claim_id"]]}]}, agent)
    assert kdb.one(f"SELECT count(*) FROM kernel.{table}") > 0
    for statement in (
        f"UPDATE kernel.{table} SET recorded_at = recorded_at"
        if table != "chunks"
        else "UPDATE kernel.chunks SET seq = seq",
        f"DELETE FROM kernel.{table}",
        f"TRUNCATE kernel.{table} CASCADE",
    ):
        with pytest.raises(psycopg.Error) as err:
            kdb.admin.execute(statement)
        assert err.value.sqlstate == "WMK02", statement


def test_there_is_no_delete_operation(kdb: KernelDB, agent: str) -> None:
    with pytest.raises(Rejected) as err:
        kdb.claim(agent, "x", [{"op": "delete", "node": "ent_x"}], basis="observed")
    assert err.value.problem == "payload"


# 3. Projection is deterministic ----------------------------------------------------------------

PROJECTION_FUNCTIONS = [
    "project_ops",
    "project_conflicts",
    "rebuild",
    "touch",
    "refresh_edge",
    "refresh_edge_conflicts",
    "refresh_claim_node",
    "refresh_node_status",
    "on_assertion",
    "on_conflict",
    "mirror_node",
    "mirror_edge",
    "node_graph_props",
    "edge_graph_props",
    "belief_v1",
    "belief_status_v1",
]
FORBIDDEN = re.compile(
    r"\b(now|clock_timestamp|statement_timestamp|transaction_timestamp|timeofday|random|"
    r"gen_random_uuid|uuidv4|uuidv7|new_id|setseed|pg_sleep|dblink\w*|http\w*)\s*\(|"
    r"current_(timestamp|date|time)\b|localtime(stamp)?\b",
    re.IGNORECASE,
)


def test_projection_makes_no_clock_random_or_network_calls(kdb: KernelDB) -> None:
    for name in PROJECTION_FUNCTIONS:
        source = kdb.one(
            "SELECT string_agg(prosrc, ' ') FROM pg_proc WHERE proname = %s AND "
            "pronamespace = 'kernel'::regnamespace",
            [name],
        )
        assert source, name
        assert not FORBIDDEN.search(source), name


def test_replay_reproduces_the_graph(kdb: KernelDB, agent: str, dbname: str) -> None:
    from evals.replay import replay
    from tests.test_belief import implements

    roles = kdb.claim(
        agent,
        "people",
        [
            {"op": "create", "ref": "$dana", "type": "Agent", "kind": "human", "name": "Dana"},
            {"op": "create", "ref": "$sam", "type": "Agent", "kind": "human", "name": "Sam"},
            {
                "op": "create",
                "ref": "$role",
                "kind": "role",
                "namespace": "bpm",
                "name": "Approver",
                "embedding": [0.1, 0.2, 0.3],
            },
            {
                "op": "create",
                "ref": "$ev",
                "type": "Event",
                "kind": "meeting",
                "name": "Handover",
                "status": "planned",
            },
        ],
        basis="observed",
    )
    r = roles["refs"]
    hr = kdb.source(agent, "HR: Dana approves since 2025.")
    mail = kdb.source(agent, "Sam took over in March 2026.", collection="conv", uri="turn:1")
    dana = implements(kdb, agent, hr, r["$dana"], r["$role"], valid_from="2025-01-01")
    implements(kdb, agent, mail, r["$sam"], r["$role"], valid_from="2026-03-01")
    kdb.claim(
        agent,
        "Dana did not approve",
        [{"op": "assert", "edge_id": dana, "valid_to": "2026-03-01"}],
        source=mail,
    )
    kdb.claim(
        agent,
        "Handover happened",
        [{"op": "transition", "node": r["$ev"], "status": "completed"}],
        basis="observed",
    )
    promoted = kdb.claim(
        agent,
        "Approvals must be dual",
        [{"op": "promote", "about": [r["$role"]]}],
        source=hr,
        modality="normative",
    )
    kdb.claim(
        agent,
        "Not dual",
        [{"op": "assert", "claim_id": promoted["claim_id"], "polarity": "negative"}],
        source=mail,
    )
    kdb.claim(agent, "redact", [{"op": "redact", "node": r["$sam"], "fields": ["name"]}], basis="observed")
    kdb.claim(agent, "unresolved", [], source=hr)
    entries, lines = replay(dbname)
    assert entries == kdb.head()
    assert lines == []


# 4. The log stores operations in kernel vocabulary -------------------------------------------------


def test_log_check_constraint_refuses_anything_but_kernel_operations(kdb: KernelDB) -> None:
    for ops in (
        [{"op": "cypher", "query": "MATCH (n) DETACH DELETE n"}],
        [{"op": "create", "sql": "DROP TABLE kernel.log"}],
        [{"op": "assert", "prompt": "ask the model"}],
    ):
        assert kdb.one("SELECT kernel.is_kernel_vocabulary(%s)", [Jsonb(ops)]) is False
    assert (
        kdb.one(
            "SELECT kernel.is_kernel_vocabulary(%s)",
            [Jsonb([{"op": "transition", "node": "x", "status": "active"}])],
        )
        is True
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        kdb.admin.execute(
            "INSERT INTO kernel.log (log_offset, entry_id, agent_id, agent_trust, claim, ops, "
            "read_at_offset, "
            "recorded_at, belief_version) VALUES (999, uuidv7(), 'a', 'low', '{}', %s, 0, now(), 1)",
            [Jsonb([{"op": "cypher", "query": "MATCH (n) RETURN n"}])],
        )


def test_written_log_entries_are_kernel_vocabulary(kdb: KernelDB, agent: str) -> None:
    kdb.claim(
        agent,
        "x",
        [
            {"op": "create", "ref": "$a", "kind": "concept", "name": "A"},
            {"op": "create", "ref": "$b", "kind": "concept", "name": "B"},
            {"op": "assert", "edge": "depends_on", "from": "$a", "to": "$b"},
        ],
        basis="observed",
    )
    assert kdb.one("SELECT bool_and(kernel.is_kernel_vocabulary(ops)) FROM kernel.log") is True


# 5. No model calls inside the database ------------------------------------------------------------


def test_database_has_no_network_or_untrusted_language_extensions(kdb: KernelDB) -> None:
    extensions = {r[0] for r in kdb.q("SELECT extname FROM pg_extension")}
    assert extensions <= {"plpgsql", "age", "vector", "pg_trgm"}
    languages = {
        r[0]
        for r in kdb.q(
            "SELECT DISTINCT l.lanname FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang "
            "WHERE p.pronamespace = 'kernel'::regnamespace"
        )
    }
    assert languages <= {"sql", "plpgsql"}


# 6. Belief is a pure function of assertions: see test_belief.py ----------------------------------


def test_belief_reads_only_assertions(kdb: KernelDB) -> None:
    source = kdb.one("SELECT prosrc FROM pg_proc WHERE proname = 'belief_v1'")
    tables = set(re.findall(r"kernel\.(\w+)", source))
    assert tables == {"assertions", "belief_status_v1", "belief"}


# 7. Kernel node types and edges are fixed --------------------------------------------------------


def test_node_types_and_kernel_edges_are_fixed(kdb: KernelDB) -> None:
    assert {r[0] for r in kdb.q("SELECT name FROM kernel.node_types")} == {
        "Entity",
        "Agent",
        "Claim",
        "Event",
    }
    assert kdb.one("SELECT count(*) FROM kernel.edge_types") == 22
    for statement in (
        "INSERT INTO kernel.node_types VALUES ('Process', 'Process', 'A pack type', 'prc')",
        "INSERT INTO kernel.edge_types VALUES "
        "('reports_to', 'structure', 'reports to', 'x', '{Agent}', '{Agent}')",
        "INSERT INTO kernel.edge_kinds VALUES ('implements', 'implements', 'x', 'y', 'pack')",
        "INSERT INTO kernel.edge_kinds VALUES ('violates', 'depends_on', 'x', 'y', 'pack')",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            kdb.admin.execute(statement)


def test_ontology_entries_need_label_and_description(kdb: KernelDB) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        kdb.admin.execute("INSERT INTO kernel.kinds VALUES ('supplier', 'Entity', '', 'x', 'pack')")
    with pytest.raises(psycopg.errors.NotNullViolation):
        kdb.admin.execute(
            "INSERT INTO kernel.rules (id, category, params, label, defined_by) "
            "VALUES ('pack.x', 'time', '{}', 'x', 'pack')"
        )


# 8. Rejections are RFC 9457 problem documents: see test_gateway.py ------------------------------
# 9. No personal data in telemetry: see test_gateway.py -----------------------------------------


# 10. The kernel is passive -------------------------------------------------------------------------


def test_nothing_acts_on_the_outside_world() -> None:
    """Only the optional embedding client makes outbound calls; there is no actuation code."""
    for path in [*(ROOT / "gateway").glob("*.py"), *(ROOT / "kernel").glob("*.py")]:
        text = path.read_text()
        if path.name != "embeddings.py":
            assert not re.search(
                r"^\s*(import|from)\s+(httpx|requests|urllib|aiohttp|smtplib|subprocess)\b",
                text,
                re.MULTILINE,
            ), path.name
        assert "os.system" not in text and "subprocess" not in text, path.name


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cypher",
    [
        "CREATE (n:Entity {id: 'ent_x', name: 'smuggled'}) RETURN n",
        "MATCH (n:Agent) SET n.trust_level = 'high' RETURN n",
        "MATCH (n:Agent) DETACH DELETE n RETURN 1",
    ],
)
async def test_graph_queries_cannot_write_even_past_the_lexical_check(dbname: str, cypher: str) -> None:
    """query_graph refuses write clauses by parsing; the database refuses them again: the reader
    role may only select and the transaction is read-only."""
    from kernel.testing import gateway_client

    async with gateway_client(dbname) as client:
        kernel = client.tools.kernel
        before = await kernel.cypher("MATCH (n) RETURN n", {}, ["n"], 100)
        with pytest.raises(psycopg.Error):
            await kernel.cypher(cypher, {}, ["n"], 10)
        assert await kernel.cypher("MATCH (n) RETURN n", {}, ["n"], 100) == before
