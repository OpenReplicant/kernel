# Annotated-corpus results

How well a model extracts, measured against annotators who are not us. Each run is
`make annotated` on a fresh stack. The protocol and measures are in `evals/annotated.py`.
Intervals are 95% Wilson intervals: with 30 items they are wide, so read the lower bounds.

## 2026-10-05 · SciFact sample (30 items) · Sonnet 5.5 · kernel 0.3.0

Headless Claude Code with the core and research skills, on the eval profile, mapping each
abstract blind and then judging the claim from the graph alone.

| Measure | Result | 95% interval |
| --- | --- | --- |
| Verdict accuracy (SUPPORT / CONTRADICT / NEI) | 0.83 (25 of 30) | 0.66 to 0.93 |
| Macro F1 | 0.83 (SUPPORT 0.90, CONTRADICT 0.80, NEI 0.80) | |
| Evidence capture: rationale sentences quoted by a phase A finding | 0.89 (34 of 38) | 0.76 to 0.96 |
| Rationale precision of the findings phase B related | 0.66 | |
| Rationale recall of the findings phase B related | 0.61 | |
| Findings per abstract | 5.0 | |
| Relations drawn to other papers' findings | 0 | |

Calibration of phase B's relations, by the confidence the model gave them:

| Confidence | Relations | Right | Accuracy |
| --- | --- | --- | --- |
| high | 3 | 3 | 1.00 |
| medium | 23 | 22 | 0.96 |
| low | 7 | 5 | 0.71 |

Accuracy falls with the stated confidence, as it should. There are too few `high`
relations to say more.

**Cost and time:** US$14.16 and 30 minutes for 60 harness sessions, about US$0.47 and one
minute per item. **Replay:** 272 log entries, graph reproduced exactly.

The last item first ran into a session usage limit. The harness now refuses to score an
item that a harness error cut short, and `--allow-existing` runs it again. It was re-run
on the same stack and skills.

### Where it went wrong

The five wrong verdicts:

- **SUPPORT read as NEI:** "the extracellular domain of TMEM27 is cleaved in human beta
  cells". The finding was there (the domain is "cleaved and shed from the plasma membrane
  of beta cells"). The model declined because it took the paper's other findings to be
  mouse work. The annotators read "beta cells" as covering the claim.
- **CONTRADICT read as NEI:** "non-invasive ventilation use should be decreased if there
  is inadequate response". The paper's conclusion that NIV is highly cost-effective was
  extracted. The model judged a conditional recommendation unaddressed; the annotators
  counted the overall benefit against it.
- **CONTRADICT read as SUPPORT:** "new drugs for tuberculosis often do not penetrate the
  necrotic portion of a lesion". The model linked the moxifloxacin finding (it does not
  diffuse well into caseum) as support. The annotated rationale is that rifampicin
  accumulates there.
- **Two NEI read as CONTRADICT,** both inferred from a related but different quantity:
  - risk factors rising with the socio-demographic index, for a claim about disease burden;
  - enhanced calcium entry, for a claim about anergic differentiation.

  Both relations were written with low or medium confidence.

The four rationale sentences no finding quoted:

- one is background the paper attributes to earlier work, which the research skill
  deliberately does not record as the paper's finding;
- three are `CONCLUSIONS` sentences restating a result whose own sentence was quoted.

### What this says

On abstracts, extraction keeps nearly all the sentences experts consider decisive. A
model judging from the graph alone agrees with them five times in six. Its errors:
- reading claims too literally: species, conditions;
- linking findings about a neighbouring quantity, at low confidence.

Belief weighs low-confidence inferred links at 0.4 x 0.4 of an observed fact, so the
damage they do is bounded.

Limits:
- one model and one corpus (biomedical abstracts);
- no entity-level gold: SciERC's host is blocked from this environment;
- the lower bounds (0.66 on verdicts, 0.76 on capture) are what to compare future runs
  against.
