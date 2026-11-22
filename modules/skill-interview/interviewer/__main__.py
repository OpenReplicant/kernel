"""python -m interviewer say TEXT [--author NAME]   one turn of the interview (what `kk serve` does for the chat pane)
   python -m interviewer play [--delay SECONDS]    the scripted answers not said yet, one after another (kk demo --interview)

Env as core.py."""
import argparse
import json
import sys
import time

from . import core


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="interviewer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("say")
    s.add_argument("text")
    s.add_argument("--author", default="person")
    p = sub.add_parser("play")
    p.add_argument("--delay", type=float, default=0)
    p.add_argument("--author", default=None)
    a = ap.parse_args(argv)
    cfg = core.config()
    if a.cmd == "say":
        print(json.dumps(core.say(cfg, a.author, a.text)))
        return 0
    said, total = 0, 0
    for ans in cfg["answers"]:
        author = a.author or next(m["author"] for m in cfg["messages"] if m["id"] == ans["id"])
        out = core.say(cfg, author, ans["text"])
        total += out["claims"]
        said += out["extracted"] != "already said"
        if said and out["extracted"] != "already said" and a.delay:
            time.sleep(a.delay)
    print(json.dumps({"conversation": cfg["conversation"], "said": said, "claims": total, "source": cfg["source"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
