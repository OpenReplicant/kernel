"""Event logs without a database: the three formats read into the same cases, only
completions count, people are dropped, the configuration is checked, and discover's plan
and digest are deterministic and regenerate the committed fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from wmk_process import config, discover, fixture
from wmk_process.logs import Columns, LogError, read_csv, read_ocel, read_xes

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "northwind-purchase-requests"

CSV = """\
case:concept:name,concept:name,time:timestamp,org:role,org:resource,lifecycle:transition,amount
PR-2,Submit,2026-03-02T10:00:00Z,requester,u1,complete,500
PR-1,Submit,2026-03-01T09:00:00Z,requester,u2,complete,12000
PR-1,Review,2026-03-01T12:00:00+01:00,manager,u3,start,12000
PR-1,Review,2026-03-01T12:30:00+01:00,manager,u3,complete,12000
PR-1,Approve,2026-03-02T09:00:00,controller,u4,complete,12000
PR-1,"Order,
 urgently",2026-03-02T09:00:00,buyer,u5,complete,
"""

XES = """\
<?xml version="1.0" encoding="UTF-8"?>
<log xes.version="1.0" xmlns="http://www.xes-standard.org/">
  <trace>
    <string key="concept:name" value="PR-1"/>
    <float key="amount" value="12000.0"/>
    <boolean key="urgent" value="false"/>
    <event>
      <string key="concept:name" value="Submit"/>
      <date key="time:timestamp" value="2026-03-01T09:00:00+00:00"/>
      <string key="org:role" value="requester"/>
      <string key="org:resource" value="Jane Doe"/>
    </event>
    <event>
      <string key="concept:name" value="Review"/>
      <string key="lifecycle:transition" value="start"/>
      <date key="time:timestamp" value="2026-03-01T11:00:00+00:00"/>
    </event>
    <event>
      <string key="concept:name" value="Review"/>
      <string key="lifecycle:transition" value="complete"/>
      <date key="time:timestamp" value="2026-03-01T11:30:00+00:00"/>
      <string key="org:role" value="manager"/>
    </event>
  </trace>
</log>
"""

OCEL = {
    "objectTypes": [{"name": "purchase request", "attributes": []}, {"name": "supplier", "attributes": []}],
    "eventTypes": [{"name": "Submit", "attributes": []}, {"name": "Review", "attributes": []}],
    "objects": [
        {
            "id": "PR-1",
            "type": "purchase request",
            "attributes": [
                {"name": "amount", "time": "2026-03-01T09:00:00Z", "value": 9000},
                {"name": "amount", "time": "2026-03-01T10:00:00Z", "value": 12000},
            ],
        },
        {"id": "S-1", "type": "supplier", "attributes": []},
    ],
    "events": [
        {
            "id": "e2",
            "type": "Review",
            "time": "2026-03-01T11:00:00Z",
            "relationships": [{"objectId": "PR-1"}],
        },
        {
            "id": "e1",
            "type": "Submit",
            "time": "2026-03-01T09:00:00Z",
            "relationships": [{"objectId": "PR-1"}, {"objectId": "S-1"}],
        },
    ],
}


def test_csv_cases_keep_completions_in_time_order_without_people(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    path.write_text(CSV)
    log = read_csv(path, Columns(attributes=("amount",)))
    assert list(log.cases) == ["PR-1", "PR-2"]
    pr1 = log.cases["PR-1"]
    # The start of Review is dropped; a time without an offset is UTC; ties keep file order.
    assert [e.activity for e in pr1.events] == ["Submit", "Review", "Approve", "Order,\n urgently"]
    assert [e.role for e in pr1.events] == ["requester", "manager", "controller", "buyer"]
    assert pr1.attrs == {"amount": 12000}
    assert log.events == 5 and log.format == "csv" and len(log.sha256) == 64
    assert not hasattr(pr1.events[0], "resource")


def test_xes_and_ocel_read_into_the_same_cases(tmp_path: Path) -> None:
    (tmp_path / "log.xes").write_text(XES)
    xes = read_xes(tmp_path / "log.xes")
    case = xes.cases["PR-1"]
    assert [e.activity for e in case.events] == ["Submit", "Review"]
    assert case.attrs == {"amount": 12000.0, "urgent": False}
    assert read_xes(tmp_path / "log.xes", ["amount"]).cases["PR-1"].attrs == {"amount": 12000.0}

    (tmp_path / "log.json").write_text(json.dumps(OCEL))
    ocel = read_ocel(tmp_path / "log.json", "purchase request")
    assert list(ocel.cases) == ["PR-1"]
    # Events in time order, and the latest value of each attribute.
    assert [e.activity for e in ocel.cases["PR-1"].events] == ["Submit", "Review"]
    assert ocel.cases["PR-1"].attrs == {"amount": 12000}
    with pytest.raises(LogError, match="no object type 'invoice'"):
        read_ocel(tmp_path / "log.json", "invoice")


def test_logs_that_cannot_be_read_say_why(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    path.write_text("case,activity\nPR-1,Submit\n")
    with pytest.raises(LogError, match="no column 'time:timestamp'"):
        read_csv(path, Columns(case="case", activity="activity"))
    path.write_text("case,activity,time\nPR-1,Submit,yesterday\n")
    with pytest.raises(LogError, match="not an ISO 8601 time"):
        read_csv(path, Columns(case="case", activity="activity", timestamp="time"))


def test_configuration_is_checked(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump({"process": "P", "log": {"file": "x.csv"}, "extra": 1}))
    with pytest.raises(config.ConfigError, match="unknown keys extra"):
        config.load(tmp_path / "bad.yaml")
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump({"process": "P", "log": {"file": "x.txt"}}))
    with pytest.raises(config.ConfigError, match="csv, xes or ocel"):
        config.load(tmp_path / "bad.yaml")
    cfg = config.load(FIXTURE / "northwind.yaml")
    assert (cfg.collection, cfg.conformance_collection) == (
        "event-log:purchase-requests",
        "conformance:purchase-requests",
    )
    assert cfg.origins == ("system:northwind-coupa",) and cfg.step("PO_SEND") == "Send purchase order"


def test_the_fixture_regenerates_from_its_log(tmp_path: Path) -> None:
    out = tmp_path / "fixture"
    out.mkdir()
    (out / "fixture.yaml").write_text((FIXTURE / "fixture.yaml").read_text())
    fixture.write(FIXTURE / "northwind.yaml", out, name="northwind-purchase-requests")
    for name in ("fixture.yaml", "script.yaml", "sources/purchase-requests.csv.digest"):
        assert (out / name).read_text() == (FIXTURE / name).read_text(), name


def test_discover_counts_what_the_log_shows() -> None:
    cfg = config.load(FIXTURE / "northwind.yaml")
    log = cfg.read()
    st = discover.stats(log, cfg)
    assert len(log.cases) == 120 and log.events == 543
    assert st.executions["Review purchase request"] == 137 and len(st.cases["Review purchase request"]) == 120
    # Urgent requests over 10,000 euros approved after the order went out.
    assert st.follows[("Send purchase order", "Approve purchase request")] == 6
    assert st.roles["Approve purchase request"] == {"finance controller": 36}
    plan = discover.build(cfg, log)
    assert discover.build(cfg, log).sources[0].content == plan.sources[0].content
    digest = plan.sources[0].content
    # Every claim cites the start of a digest line.
    for fact in plan.facts:
        assert fact.at == 0 or digest[fact.at - 1] == "\n", fact.text
    flows = [f for f in plan.facts if any(op.get("edge") == "flows_to" for op in f.ops)]
    assert len(flows) == 9

    cfg.min_count = 7
    sparse = discover.build(cfg, log)
    assert len([f for f in sparse.facts if any(op.get("edge") == "flows_to" for op in f.ops)]) == 8
    assert (
        "Seen fewer than 7 times, not mapped:\n- Send purchase order -> Approve purchase request: 6 times"
        in (sparse.sources[0].content)
    )
