"""kk up writes the harness's commands where KK_COMMANDS_DIR says (D-95), and otherwise as before."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kk import up  # noqa: E402

TOOLS = ("kernel-mcp", "kernel-mcp-hook", "kernel-mcp-cue")


def commands(settings: dict) -> list[str]:
    return [h["command"] for entries in settings["hooks"].values() for e in entries for h in e["hooks"]]


def main() -> None:
    login = Path("/home/someone/.config/knowledge-kernel/demo.json")
    os.environ.pop("KK_COMMANDS_DIR", None)
    default = up.command("kernel-mcp")
    assert default.endswith("kernel-mcp"), default

    with tempfile.TemporaryDirectory() as wrappers:
        os.environ["KK_COMMANDS_DIR"] = wrappers
        want = {t: str(Path(wrappers).resolve() / t) for t in TOOLS}
        assert up.mcp_entry(login)["command"] == want["kernel-mcp"]
        hooks = commands(up.with_hooks({}, login, "memory"))
        assert any(c == want["kernel-mcp-cue"] for c in hooks), hooks
        assert all(want["kernel-mcp-hook"] in c for c in hooks if "kernel-mcp-hook" in c), hooks
        assert sum("kernel-mcp-hook" in c for c in hooks) == len(up.HOOKS), hooks
        # a second run adds nothing (the hooks are recognised by name, wherever they live)
        again = up.with_hooks(up.with_hooks({}, login, "memory"), login, "memory")
        assert len(commands(again)) == len(hooks)
    os.environ.pop("KK_COMMANDS_DIR", None)
    assert up.command("kernel-mcp") == default
    print("kk up --commands-dir: 6/6")


if __name__ == "__main__":
    main()
