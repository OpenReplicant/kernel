# BPI Challenge 2019 at scale (M3-T4, D-72)

**Question.** Do the event-log encoder and the conformance processor work on a real, public log of
production size, in bounded memory and a reasonable time?

**Data.** [BPI Challenge 2019](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1)
(van Dongen 2019, 4TU.ResearchData, **CC BY 4.0**): purchase order handling at a large company,
251,734 cases (purchase order items), 1,595,923 events, 42 activities, 627 users; one 728 MB XES file
(a single line). Downloaded at run time, checked by sha256, not committed. (BPI Challenge 2017 is under
4TU's non-commercial terms, so it was not used.)

**Documented process** (`documented.bpmn`, written here): the dataset description's category
"3-way matching, invoice after goods receipt" — create the item, record the goods receipt, record the
invoice, clear it. The log mixes four categories, so most discrepancies are paths of the other
categories; this measures conformance at scale, it is not an audit.

```sh
python eval/bpi2019/run.py fetch
DATABASE_URL=<a login that may SET ROLE to the encoders and conformance> python eval/bpi2019/run.py scale --window month
```

with `kk serve` running the validator on the same kernel and gitleaks on PATH.

## Result (2026-10-18, 4 CPU cores, monthly windows; `runs/2026-10-18-scale-month.json`)

| Step | Time | Peak memory |
| --- | --- | --- |
| ingest filter (gitleaks, 4 processes over 23 overlapping chunks) | 206 s | — |
| encode (streamed, one trace at a time) → 28,396 claims in 6 change sets | 31 s | 94 MB |
| validate and apply (validator 0.3.1) | 72 s | — |
| documented model, one conformance pass → 495 discrepancies to review | 4 s (+ 8 s validation) | 95 MB |
| **whole run** | **325 s** | |

Before the fixes this needed (D-72): the encoder read the whole XES into memory; the ingest filter
timed out after 300 s (gitleaks alone took 771 s on the file, on one core); validation took 7 min 19 s.

The discrepancies by kind: 470 unexpected paths, 21 rework, 2 swaps, 2 skips. The largest are the other
categories' paths (110,215 items went from "Vendor creates invoice" to "Record Goods Receipt"); the
swaps are the compliance question the challenge asks: 15,580 items had the invoice recorded before the
goods receipt, and 3,958 an invoice recorded after the invoice was cleared.
