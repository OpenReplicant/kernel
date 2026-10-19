"""M6-T16 unit checks (D-91): recall cues are checked when written, and fire in the situations they name:
always at a session start, a mention, a date near (yearly too), a path, a tool and its argument."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kernel_mcp.cues import check, fires, parse  # noqa: E402

ok = 0


def check_(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


def refused(cue) -> bool:
    try:
        check(cue)
    except ValueError:
        return True
    return False


check_(check({"mentions": "Sam"}) == {"mentions": ["Sam"]} and check({"on": "--05-30"})["before"] == 7,
       "a cue is normalised: one mention becomes a list; a date gets a week's notice")
check_(all(refused(c) for c in ({}, {"colour": "red"}, {"on": "May 30"}, {"on": "--02-30"}, {"before": 3},
                                {"mentions": []}, {"tool": ""}, {"always": False})),
       "a cue that is empty, unknown, an impossible date, before without on, or names nothing is refused")
check_(parse('{"always": true}') == {"always": True} and parse("not json") is None, "a stored cue reads back")

always = check({"always": True})
check_(fires(always, start=True) == "always" and fires(always, text="anything") is None,
       "always: at a session start, not on every prompt")
sam = check({"mentions": ["Sam", "staging server"]})
check_(fires(sam, text="Can Sam review this?") == "mentions Sam" and fires(sam, text="sample size") is None
       and fires(sam, text="is the Staging  Server up?") == "mentions staging server",
       "mentions: whole words and phrases, any case; 'sample' is not 'Sam'")
birthday = check({"on": "--05-30", "before": 7})
check_(fires(birthday, today=date(2027, 5, 25)) == "on --05-30" and fires(birthday, today=date(2027, 5, 31)) is None
       and fires(birthday, today=date(2027, 4, 1)) is None and fires(birthday, today=date(2026, 12, 30)) is None,
       "a yearly date: within the week before, not after or long before")
check_(fires(check({"on": "--01-02"}), today=date(2026, 12, 30)) == "on --01-02",
       "a yearly date early in January is near at the end of December")
check_(fires(check({"on": "2026-11-03", "before": 0}), today=date(2026, 11, 3)) is not None
       and fires(check({"on": "2026-11-03", "before": 0}), today=date(2026, 11, 2)) is None,
       "a one-off date with no notice fires on the day only")
deploy = check({"where": "deploy/*"})
check_(fires(deploy, paths=("deploy/compose.yaml",)) == "where deploy/*" and fires(deploy, paths=("src/a.py",)) is None,
       "where: a path glob")
push = check({"tool": "Bash(git push*)"})
check_(fires(push, tool="Bash", tool_input={"command": "git push -u origin x"}) == "tool Bash(git push*)"
       and fires(push, tool="Bash", tool_input={"command": "git status"}) is None
       and fires(check({"tool": "Edit"}), tool="Edit", tool_input={"file_path": "a.py"}) == "tool Edit"
       and fires(check({"tool": "mcp__*"}), tool="mcp__github__merge_pull_request") is not None,
       "tool: a name glob with an optional argument glob (the command, or the file)")
print(f"M6-T16 recall cue checks passed ({ok}/{ok})")
