"""M6-T4 unit checks (no database, no model): the model's answer is read leniently; a memory needs a kind,
a sentence and a quote found in the message; its claims quote the message and cite its transcript; a memory
that replaces an earlier one supersedes that one's claims; topics are labelled once; dates and standing
instructions become recall cues."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_extractor.extract import AS, DCT, KIND, LABEL, RECALL_WHEN, locate, memories, ops, parse, prompt  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


TEXT = ("Thanks! I set a personal best in the charity 5K: 27:12.\nAlso, my sister Maya's birthday is on June 3, "
        "and please always answer in metric units.")
check(parse('<think>maybe [1]</think>Here: [{"kind": "fact"}] done') == [{"kind": "fact"}]
      and parse("nothing to remember") == [] and parse('[1, {"a": 2}, "x"]') == [{"a": 2}],
      "the first JSON array in the answer, objects only; thinking and prose around it are ignored")
check(locate("personal best in the charity 5K", TEXT) == "personal best in the charity 5K"
      and locate("Personal best, in the charity 5k", TEXT) == "personal best in the charity 5K"
      and locate("a marathon", TEXT) is None and locate("27:12. Also, my sister", TEXT) is None,
      "a quote is found exactly or by its words (case, spacing, punctuation), within one line")
items = [{"kind": "fact", "text": "The user's 5K personal best is 27:12.", "quote": "a personal best in the charity 5K: 27:12",
          "replaces": 1, "about": "charity 5K"},
         {"kind": "fact", "text": "Maya, the user's sister, has her birthday on June 3.", "quote": "my sister Maya's birthday is on June 3",
          "about": "Maya", "on": "2023-06-03"},
         {"kind": "preference", "text": "Answer the user in metric units.", "quote": "please always answer in metric units",
          "always": True, "replaces": 7},
         {"kind": "opinion", "text": "The user ran a marathon in Perth.", "quote": "Thanks"},
         {"kind": "fact", "text": "The user ran a marathon.", "quote": "I ran a marathon"},
         {"kind": "fact", "text": "", "quote": "Thanks"}]
found = memories(items, TEXT, earlier=2)
check(memories([{"kind": "commitment", "text": "Always answer in metric units.", "quote": "please always answer in"}],
               TEXT, 0)[0]["cue"] == {"always": True}
      and [m["kind"] for m in memories(items + [{"kind": "commitment", "text": "I will convert to metric.", "quote": "Thanks"}],
                                        TEXT, 2, agent=True)] == ["commitment"]
      and parse('[{"a": 1}, {"b": 2}, {"c":') == [{"a": 1}, {"b": 2}]
      and memories([{"kind": "fact", "text": "x y", "quote": "Thanks", "about": "The user"}], TEXT, 0)[0]["about"] is None,
      "always/never is a standing instruction; an agent's message gives commitments only; a cut-off array keeps"
      " its complete items; 'the user' is not a topic")
check([m["kind"] for m in found] == ["fact", "fact", "preference"],
      "a sentence not grounded in the message, a quote not in it, or an empty sentence is dropped")
check([(m["kind"], m["cue"]) for m in memories([{"kind": "always", "text": "Answer in metric units.",
                                                 "quote": "please always answer in metric units"},
                                                {"kind": "event", "text": "Maya's birthday is June 3.", "quote": "Maya's birthday"}],
                                               TEXT, 0)] == [("preference", {"always": True}), ("fact", None)],
      "a kind of the model's own: a standing instruction is a preference, anything else a fact")
check(found[0]["replaces"] == 1 and found[2]["replaces"] is None and found[1]["cue"] == {"on": "2023-06-03"}
      and found[2]["cue"] == {"always": True} and found[0]["cue"] is None,
      "replaces names an earlier memory (or nothing); a date ahead and a standing instruction become cues")
message = {"subject": "urn:kk:message:t:c:2", "author": "urn:kk:actor:t:user", "context": "urn:kk:conversation:t:c",
           "published": "2023-05-23T13:01:00Z"}
old = [{"id": "11111111-0000-4000-8000-000000000000", "predicate": AS + "content", "status": "normal"},
       {"id": "22222222-0000-4000-8000-000000000000", "predicate": "urn:x:other", "status": "preferred"}]
out = ops(found, message, "abc123", {1: old}, named={"urn:kk:topic:maya"})
adds = [o for o in out if o["op"] == "add_claim"]
first = [o for o in adds if o["subject"] == adds[0]["subject"]]
by = {o["predicate"]: o for o in first}
check(by[AS + "content"]["object"] == {"value": {"@value": "The user's 5K personal best is 27:12."}}
      and by[AS + "attributedTo"]["object"] == {"entity": message["author"]}
      and by[DCT + "type"]["object"] == {"entity": KIND + "fact"} and by[DCT + "isPartOf"]["object"] == {"entity": message["subject"]}
      and by[AS + "context"]["object"] == {"entity": message["context"]} and DCT + "subject" in by,
      "a memory is an as:Note: its sentence, who wrote the message, its kind, the message, the conversation, a topic")
check(all(o["evidence_type"] == "extracted" and o["references"] == [{"quote": "a personal best in the charity 5K: 27:12",
                                                                     "artifact": "abc123"}] for o in first),
      "every claim is extracted, quoting the message's words in its transcript")
labels = [o for o in adds if o["predicate"] == LABEL]
check([o["subject"] for o in labels] == ["urn:kk:topic:charity-5k"],
      "a new topic is labelled; one already named (Maya) is not")
statuses = [o for o in out if o["op"] == "set_status"]
check([(o["claim"], o["expect_status"], o["reason"]) for o in statuses]
      == [(old[0]["id"], "normal", "superseded"), (old[1]["id"], "preferred", "superseded")]
      and statuses[0]["replaced_by"] == by[AS + "content"]["ref"] and statuses[1]["replaced_by"] == by[AS + "content"]["ref"],
      "the earlier memory it replaces is superseded, claim by claim, by the new one")
cues = [json.loads(o["object"]["value"]["@value"]) for o in adds if o["predicate"] == RECALL_WHEN]
check(cues == [{"on": "2023-06-03"}, {"always": True}], "cues are stored as kk:recallWhen (D-91)")
check(len({o["subject"] for o in adds if o["predicate"] == AS + "content"}) == 3
      and ops(found, message, "abc123", {1: old}, named={"urn:kk:topic:maya"}) == out,
      "one note per memory, and the same answer gives the same ops (idempotent)")
p = prompt(TEXT, "user", "2023-05-23T13:01:00Z", "Hi", "assistant", ["(2023-05-01) The user's 5K best is 28:00."])
check("1. (2023-05-01) The user's 5K best is 28:00." in p and "sent 2023-05-23 by user" in p and TEXT in p,
      "the prompt numbers the earlier memories and dates the message")
print(f"M6-T4 memory extractor checks passed ({ok}/{ok})")
