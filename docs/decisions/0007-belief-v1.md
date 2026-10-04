# 0007. belief_v1: weights and thresholds

Date: 2026-10-04 · Status: accepted

## Decision

For each edge, Claim node or node status, `kernel.belief_v1` takes each source's latest
assertion and weighs it as agent trust x basis x confidence band, with
high/medium/low = 1.0/0.7/0.4 and observed/reported/inferred = 1.0/0.7/0.4. Denials weigh
against. Status: `unknown` without weight; `contested` when both sides reach 0.25, when
counted sources disagree on the window, or when the sides tie; else `accepted` or
`rejected` by the heavier side. Score = for / (for + against).

Raw confidence numbers map to bands (>= 0.8 high, >= 0.5 medium, else low) before use.
Trust is taken from the writing agent at write time and recorded in the log entry. No
term depends on time. The version is stored on each log entry (`belief_version`).

## Consequences

The thresholds are placeholders until calibration from evals exists; changing them is a
new belief version, never an edit of v1.
