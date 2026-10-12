"""The sandbox MCP server: one tool, `sandbox_run`, over a configured backend.

    python -m sandbox.server [--config config/kernel.yaml] [--transport streamable-http]

What isolates a run is fixed here, not chosen by the caller: the backend's flags, the image
allowlist, and limits capped at the config's `sandbox.max`. With `sandbox.enabled: false` the
backend is `local`: code runs on the host, not isolated, and every result says so.
"""
import argparse
import shutil
import tempfile
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from kernel import config
from sandbox import backends

LIMIT_KEYS = ("timeout_s", "cpus", "memory", "pids", "max_output")


def settings(cfg: dict) -> dict:
    s = cfg["sandbox"]
    return {**s, "backend": s["backend"] if s["enabled"] else "local"}


def limits(s: dict, asked: dict) -> dict:
    """The run's limits: the config's defaults, the caller's choices, each capped at the max."""
    out = {}
    for k in LIMIT_KEYS:
        v = asked.get(k) if asked.get(k) is not None else s["defaults"][k]
        cap = s["max"][k]
        if k == "memory":
            v = v if backends._bytes(v) <= backends._bytes(cap) else cap
        else:
            v = min(type(cap)(v), cap)
        out[k] = v
    return out


def write_files(work: Path, files: dict[str, str]):
    for rel, content in files.items():
        p = (work / rel).resolve()
        if work not in p.parents:
            raise ValueError(f"file {rel!r} is outside the work directory")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        p.chmod(0o644)
        p.parent.chmod(0o755)


def run(s: dict, command: list[str], files: dict[str, str] | None = None, image: str = "python",
        **asked) -> dict:
    if not command or not all(isinstance(c, str) for c in command):
        raise ValueError("command is a non-empty list of strings")
    if image not in s["images"]:
        raise ValueError(f"image {image!r} is not allowed; one of {sorted(s['images'])}")
    lim = limits(s, asked)
    work = Path(tempfile.mkdtemp(prefix="kernel-sandbox-")).resolve()
    try:
        work.chmod(0o755)  # the container's user is not the host user
        write_files(work, files or {})
        if s["backend"] == "docker":
            result = backends.docker(s["engine"], s["images"][image], command, str(work), lim)
        elif s["backend"] == "local":
            result = backends.local(command, str(work), lim)
        else:
            raise ValueError(f"unknown sandbox backend {s['backend']!r}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {**result, "backend": s["backend"], "isolated": s["backend"] != "local",
            "image": s["images"][image], "limits": lim}


def build(cfg: dict) -> MCPServer:
    s = settings(cfg)
    server = MCPServer("kernel-sandbox")

    @server.tool()
    def sandbox_run(command: list[str], files: dict[str, str] | None = None, image: str = "python",
                    timeout_s: float | None = None, cpus: float | None = None, memory: str | None = None,
                    pids: int | None = None, max_output: int | None = None) -> dict:
        """Run `command` in a fresh sandbox with `files` (path -> text) read-only in its working
        directory /work. No network, read-only root (a small writable /tmp), a non-root user,
        nothing from the host's environment; limits default from and are capped by the config.
        Returns exit_code (null on timeout), stdout, stderr, timed_out, duration_s, backend,
        isolated (false only when the sandbox is switched off), image and limits."""
        try:
            return run(s, command, files, image, timeout_s=timeout_s, cpus=cpus, memory=memory, pids=pids,
                       max_output=max_output)
        except (ValueError, backends.EngineError) as e:  # refused, or couldn't start: the caller sees why
            raise ToolError(str(e)) from e

    return server


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=None)
    p.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    a = p.parse_args()
    build(config.load(a.config)).run(a.transport)


if __name__ == "__main__":
    main()
