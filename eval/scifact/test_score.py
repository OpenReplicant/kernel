"""The SciFact scorer on a tiny fixture: right answers score 1, broken ones do not (mutation check, D-59)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import parse, score  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        sys.exit(f"FAIL: {msg}")
    ok += 1
    print("ok:", msg)


abstracts = {"10": ["Background.", "Drug X lowered blood pressure.", "Side effects were rare."],
             "20": ["Drug X raised blood pressure in mice.", "Conclusion."],
             "30": ["Unrelated work on soil."]}
claims = [{"id": 1, "claim": "Drug X lowers blood pressure.",
           "evidence": {"10": [{"sentences": [1], "label": "SUPPORT"}]}},
          {"id": 2, "claim": "Drug X lowers blood pressure in mice.",
           "evidence": {"20": [{"sentences": [0], "label": "CONTRADICT"}]}},
          {"id": 3, "claim": "Soil is blue.", "evidence": {}}]
right = {"1|10": {"label": "SUPPORT", "quotes": ["Drug X lowered blood pressure."]},
         "1|30": {"label": "NOT_ENOUGH_INFO", "quotes": []},
         "1|closed": {"label": "SUPPORT"},
         "2|20": {"label": "CONTRADICT", "quotes": ["Drug X raised blood pressure in mice."]},
         "2|closed": {"label": "SUPPORT"},
         "3|30": {"label": "NOT_ENOUGH_INFO", "quotes": []}}
r = score(claims, abstracts, right)
check(r["abstract_label_only"]["f1"] == 1 and r["abstract_rationalized"]["f1"] == 1, "right answers score 1")
check(r["claim_accuracy_with_kernel"] == 1 and r["claim_accuracy_closed_book"] == 0.5, "claim-level accuracy, with and without")
check(r["quote_fidelity"]["rate"] == 1, "quotes found verbatim")

flipped = {**right, "2|20": {"label": "SUPPORT", "quotes": ["Drug X raised blood pressure in mice."]}}
check(score(claims, abstracts, flipped)["abstract_label_only"]["f1"] < 1, "a wrong label loses")
made_up = {**right, "1|10": {"label": "SUPPORT", "quotes": ["Drug X cured everyone."]}}
m = score(claims, abstracts, made_up)
check(m["abstract_rationalized"]["f1"] < 1 and m["abstract_label_only"]["f1"] == 1 and m["quote_fidelity"]["rate"] < 1,
      "an invented quote keeps the label but loses the rationale and fidelity")
check(score(claims, abstracts, {**right, "3|30": {"label": "SUPPORT", "quotes": []}})["abstract_label_only"]["precision"] < 1,
      "a verdict on an irrelevant abstract costs precision")
check(parse("Sure! {\"label\": \"contradict\", \"quotes\": [\"a\"]}")["label"] == "CONTRADICT", "parses JSON inside prose")
check(parse("no json here")["label"] == "NOT_ENOUGH_INFO", "an unparseable answer counts as no verdict")
print(f"SciFact scorer: {ok} checks passed")
