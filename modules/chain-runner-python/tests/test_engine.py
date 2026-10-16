"""Runner tests against the real validator chains with a fake HTTP layer (no network).

Run: python modules/chain-runner-python/tests/test_engine.py     (needs pyyaml, jsonschema)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from chain_runner.__main__ import load_chains  # noqa: E402
from chain_runner.engine import Chain, Runner  # noqa: E402

API = "http://postgrest:3000"
TOKENS = {"urn:kk:agent:validator": "validator-jwt"}
CHAINS = load_chains(ROOT / "modules", json.loads((ROOT / "kernel/schemas/chain.schema.json").read_text()))


class FakeHttp:
    def __init__(self, queue, verdicts, validator_down=False):
        self.queue, self.verdicts, self.validator_down = queue, verdicts, validator_down
        self.calls, self.runs = [], 0

    def __call__(self, method, url, token, body):
        self.calls.append((method, url, token, body))
        if url.startswith(f"{API}/validation_queue"):
            return 200, self.queue
        if url == "http://validator:8000/validate":
            if self.validator_down:
                return 599, "connection refused"
            ok = self.verdicts[body["id"]]
            return 200, {"id": body["id"], "ok": ok, "report": {"shape_violations": [] if ok else ["x"]}}
        if url == f"{API}/rpc/start_run":
            self.runs += 1
            return 200, f"run-{self.runs}"
        if url in (f"{API}/rpc/finish_run", f"{API}/rpc/record_validation"):
            return 200, None
        return 404, {"error": url}


def runner(fake):
    return Runner(CHAINS, TOKENS, API, fake)


def test_empty_queue_creates_no_runs():
    fake = FakeHttp([], {})
    ex = runner(fake).execute("urn:kk:chain:validate-change-sets")
    assert ex.ok and ex.runs == [] and fake.runs == 0, ex


def test_each_change_set_is_validated_and_recorded():
    fake = FakeHttp([{"id": "a", "ops": []}, {"id": "b", "ops": []}], {"a": True, "b": False})
    ex = runner(fake).execute("urn:kk:chain:validate-change-sets")
    assert ex.ok, ex.trace
    records = [c[3] for c in fake.calls if c[1].endswith("/rpc/record_validation")]
    assert [(r["change_set_id"], r["ok"]) for r in records] == [("a", True), ("b", False)], records
    starts = [c[3] for c in fake.calls if c[1].endswith("/rpc/start_run")]
    assert [s["plan_step"] for s in starts] == ["urn:kk:chain:validate-one/record"] * 2
    assert all(s["module"] == "urn:kk:module:validator-shacl" for s in starts)
    assert len(ex.runs) == 2


def test_kernel_calls_use_the_runs_as_token_and_others_none():
    fake = FakeHttp([{"id": "a"}], {"a": True})
    runner(fake).execute("urn:kk:chain:validate-change-sets")
    for method, url, token, _ in fake.calls:
        assert token == ("validator-jwt" if url.startswith(API) else None), (url, token)


def test_validator_down_routes_to_log_error_without_recording():
    fake = FakeHttp([{"id": "a"}], {"a": True}, validator_down=True)
    ex = runner(fake).execute("urn:kk:chain:validate-change-sets")
    assert not ex.ok
    assert not any(c[1].endswith("/rpc/record_validation") for c in fake.calls)
    one = runner(fake).execute("urn:kk:chain:validate-one", {"id": "a"})
    assert [t["step"].rsplit("/", 1)[1] for t in one.trace] == ["validate", "log_error"], one.trace


def test_chain_without_token_does_not_run():
    fake = FakeHttp([], {})
    ex = Runner(CHAINS, {}, API, fake).execute("urn:kk:chain:validate-change-sets")
    assert not ex.ok and fake.calls == []


def test_js_steps_fail_with_a_reason():
    chain = Chain.from_dict({"chain": "urn:kk:chain:t", "runs_as": "urn:kk:agent:validator",
                             "trigger": {"type": "chain"},
                             "steps": [{"id": "f", "do": "filter", "with": {"lang": "js", "source": "return true;"}}]},
                            ROOT / "modules/validator-shacl")
    ex = Runner({"urn:kk:chain:t": chain}, TOKENS, API, FakeHttp([], {})).execute("urn:kk:chain:t", {})
    assert not ex.ok and "not supported" in ex.trace[0]["error"], ex.trace


def test_python_switch_routes_on_its_label():
    chain = Chain.from_dict({"chain": "urn:kk:chain:t", "runs_as": "urn:kk:agent:validator",
                             "trigger": {"type": "chain"},
                             "steps": [{"id": "s", "do": "switch", "with": {"lang": "python",
                                        "source": "return 'big' if msg['n'] > 1 else 'small'"},
                                        "next": {"big": "b", "small": "l"}},
                                       {"id": "b", "do": "log"}, {"id": "l", "do": "log"}]},
                            ROOT / "modules/validator-shacl")
    ex = Runner({"urn:kk:chain:t": chain}, TOKENS, API, FakeHttp([], {})).execute("urn:kk:chain:t", {"n": 5})
    assert ex.ok and [t["step"] for t in ex.trace] == ["urn:kk:chain:t/s", "urn:kk:chain:t/b"], ex.trace


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"chain runner tests passed ({len(tests)})")
