"""Sandbox backends: run a command over a work directory and return its result.

Every backend returns {exit_code, stdout, stderr, timed_out, duration_s}; exit_code is None
when the run timed out. `docker` isolates (ported from the second iteration's
pclib/sandbox.py); `local` doesn't, and exists for when the sandbox is switched off.
"""
import os
import resource
import subprocess
import threading
import time
import uuid


class EngineError(RuntimeError):
    """The runtime couldn't start the run (no engine, no image): not the code's fault."""


def _drain(stream, buf: bytearray, cap: int, over: list):
    """Keep the first `cap` bytes; read (and count) the rest so the code never blocks."""
    for chunk in iter(lambda: stream.read(65536), b""):
        room = cap - len(buf)
        if room > 0:
            buf += chunk[:room]
        over[0] += max(0, len(chunk) - max(room, 0))
    stream.close()


def _text(buf: bytearray, over: int) -> str:
    s = buf.decode("utf-8", "replace")
    return s + (f"\n[... {over} more bytes]" if over else "")


def _run(argv, lim, on_timeout, **popen) -> dict:
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                **popen)
    except FileNotFoundError as e:
        raise EngineError(f"not found: {argv[0]}") from e
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
        on_timeout(proc)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for r in readers:
        r.join(timeout=10)
    return {"exit_code": code, "stdout": _text(out, out_over[0]), "stderr": _text(err, err_over[0]),
            "timed_out": timed_out, "duration_s": round(time.monotonic() - t0, 3)}


def docker(engine: str, image: str, command: list[str], work: str, lim: dict) -> dict:
    """A fresh container: no network, read-only root, a small /tmp, the work directory
    read-only at /work, uid 10001, no capabilities, no new privileges, CPU/memory/pids limits,
    removed when it ends or when the timeout passes. Nothing from the environment goes in."""
    name = f"kernel-sandbox-{uuid.uuid4().hex[:12]}"
    argv = [engine, "run", "--rm", "--name", name, "--pull=never",
            "--network=none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            f"--cpus={lim['cpus']}", f"--memory={lim['memory']}", f"--memory-swap={lim['memory']}",
            f"--pids-limit={lim['pids']}", "--user=10001:10001",
            "-v", f"{work}:/work:ro", "-w", "/work", image, *command]

    def remove(proc):  # killing the CLI client doesn't stop the container; remove it by name
        subprocess.run([engine, "rm", "-f", name], capture_output=True, timeout=60)

    result = _run(argv, lim, remove)
    if result["exit_code"] == 125 and result["stderr"].lstrip().startswith(("Error", "docker:", "Unable")):
        raise EngineError(f"the engine refused to start the container: {result['stderr'].strip()[:500]}")
    return {**result, "container": name}


def local(command: list[str], work: str, lim: dict) -> dict:
    """NOT isolated: a host subprocess in the work directory with an empty environment (PATH
    only), an address-space limit and a CPU-time limit, killed at the timeout. For when the
    sandbox is switched off in the config."""
    mem = _bytes(lim["memory"])
    cpu = int(lim["timeout_s"]) + 1

    def limits():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))

    def kill(proc):
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass

    return _run(command, lim, kill, cwd=work, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": work},
                preexec_fn=limits)


def _bytes(size) -> int:
    s = str(size).strip().lower()
    mult = {"k": 1 << 10, "m": 1 << 20, "g": 1 << 30}.get(s[-1:], 1)
    return int(float(s[:-1] if s[-1:] in "kmg" else s) * mult)
