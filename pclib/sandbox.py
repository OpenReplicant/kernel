"""Run payloads (code a model wrote, benchmark tests: anything not reviewed) in a fresh
container per call. Never exec or eval them on the host.

The container gets no network, a read-only root filesystem, no capabilities, CPU, memory and
process limits, a non-root user, and a small /tmp. The only host path it sees is one work
directory, mounted read-only at /work; nothing from the environment is passed in. A call that
runs past its wall-clock timeout is killed. The engine is `$PC_CONTAINER_CMD` (default
`podman`; `docker` takes the same flags).

Inside a step (pclib.run_step) each call emits `tool.call` and `tool.result` trace events.
scripts/env_container.py is the same call as a tool-shaped script.
"""
import os
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import contract
from .config import data_root

IMAGE = "localhost/kernel-sandbox:latest"
LIMITS = {"timeout_s": 10.0, "cpus": 1.0, "memory": "256m", "pids": 64, "max_output": 1 << 20}
TRACE_TAIL = 2000


class SandboxError(Exception):
    """The engine couldn't start the container (no image, no engine): not the payload's fault."""


def work_dir(inp: contract.StepInput) -> Path:
    """$PC_DATA/runs/<run>/tasks/<task>/ep-<n>/work (docs/FILESYSTEM.md), created."""
    d = data_root() / "runs" / str(inp.run_id) / "tasks" / str(inp.task_id) / f"ep-{inp.episode}" / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _engine() -> list[str]:
    return shlex.split(os.environ.get("PC_CONTAINER_CMD", "podman"))


def _argv(name, work, command, image, cpus, memory, pids):
    return [*_engine(), "run", "--rm", "--name", name, "--pull=never",
            "--network=none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            f"--cpus={cpus}", f"--memory={memory}", f"--memory-swap={memory}", f"--pids-limit={pids}",
            "--user=10001:10001", "-v", f"{work}:/work:ro,Z", "-w", "/work", image, *command]


def _drain(stream, buf: bytearray, cap: int, over: list):
    """Keep the first `cap` bytes; read (and count) the rest so the payload never blocks."""
    for chunk in iter(lambda: stream.read(65536), b""):
        room = cap - len(buf)
        if room > 0:
            buf += chunk[:room]
        over[0] += max(0, len(chunk) - max(room, 0))
    stream.close()


def _text(buf: bytearray, over: int) -> str:
    s = buf.decode("utf-8", "replace")
    return s + (f"\n[... {over} more bytes]" if over else "")


def run(work: Path | str, command: list[str], files: dict[str, str] | None = None,
        image: str = IMAGE, **limits) -> dict:
    """Write `files` into `work`, run `command` in a fresh container with `work` at /work,
    and return {stdout, stderr, exit_code, timed_out, duration_s}. exit_code is None when the
    call timed out."""
    lim = {**LIMITS, **{k: v for k, v in limits.items() if v is not None}}
    unknown = set(lim) - set(LIMITS)
    if unknown:
        raise TypeError(f"unknown sandbox limits {sorted(unknown)}")
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    work.chmod(0o755)                          # the container's user is not the host user
    for rel, content in (files or {}).items():
        path = (work / rel).resolve()
        if work not in path.parents:
            raise ValueError(f"{rel!r} is outside the work directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o644)
    traced = contract._current is not None
    if traced:
        contract.trace("tool.call", payload={"tool": "sandbox", "command": command, "image": image,
                                             "files": sorted(files or {}), "limits": lim})
    name = f"pc-sandbox-{uuid.uuid4().hex[:12]}"
    argv = _argv(name, work, command, image, lim["cpus"], lim["memory"], lim["pids"])
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as e:
        raise SandboxError(f"container engine not found: {argv[0]}") from e
    out, err, out_over, err_over = bytearray(), bytearray(), [0], [0]
    readers = [threading.Thread(target=_drain, args=(proc.stdout, out, lim["max_output"], out_over), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, err, lim["max_output"], err_over), daemon=True)]
    for r in readers:
        r.start()
    timed_out = False
    try:
        code = proc.wait(timeout=lim["timeout_s"])
    except subprocess.TimeoutExpired:
        timed_out, code = True, None
        # Killing the CLI client doesn't stop the container; remove it by name.
        subprocess.run([*_engine(), "rm", "-f", name], capture_output=True, timeout=60)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for r in readers:
        r.join(timeout=10)
    result = {"stdout": _text(out, out_over[0]), "stderr": _text(err, err_over[0]),
              "exit_code": code, "timed_out": timed_out, "duration_s": round(time.monotonic() - t0, 3)}
    if code == 125 and err.lstrip().startswith((b"Error", b"docker:")):     # the engine's own failure
        raise SandboxError(f"the engine refused to start the container: {result['stderr'].strip()}")
    if traced:
        contract.trace("tool.result", payload={"tool": "sandbox", **result,
                                               "stdout": result["stdout"][-TRACE_TAIL:],
                                               "stderr": result["stderr"][-TRACE_TAIL:]})
    return result
