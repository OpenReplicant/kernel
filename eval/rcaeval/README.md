# RCAEval on the kernel's Analyse step (M3-T8, D-63)

**Question.** The observer records telemetry as observed claims per time window (D-52), and the
anomaly module analyses those windows. Does that representation keep enough signal to find a fault's
root cause, compared with root-cause methods that read the raw series?

**Data.** [RCAEval](https://github.com/phamquiluan/RCAEval) (Pham et al., WWW 2025 Companion, arXiv
2412.17015; MIT), suite RE1: 375 injected faults (CPU, memory, disk, delay, loss) on Online Boutique,
Sock Shop and Train Ticket, metrics only. Every method is given the injection time and ranks the
metrics; Avg@5 is the mean of AC@1…AC@5 for the root-cause service. A random ranking scores 0.23 on RE1-OB.

**Method `kernel`.** Each metric averaged over windows of `--window` seconds aligned to the injection
(the observer's `KK_WINDOW_SECONDS`), then each metric's largest robust z-score (median, IQR) after the
injection against the windows before it, ranked. BARO and N-Sigma, two of RCAEval's baselines, run on
the same cases with RCAEval's own code for reference.

```sh
git clone https://github.com/phamquiluan/RCAEval ~/.cache/knowledge-kernel/eval/rcaeval/src
git -C ~/.cache/knowledge-kernel/eval/rcaeval/src checkout 259ea4167160256a74ad30004aa3d96a998c846e
python3.12 -m venv /tmp/rca && /tmp/rca/bin/pip install --no-deps -e ~/.cache/knowledge-kernel/eval/rcaeval/src
/tmp/rca/bin/pip install pandas pyarrow numpy scikit-learn scipy huggingface_hub tqdm matplotlib networkx requests
/tmp/rca/bin/python eval/rcaeval/run.py --dataset re1-ob --method kernel --window 60
/tmp/rca/bin/python eval/rcaeval/run.py --dataset re1-ob --method baro
```

RCAEval's full install pins many old packages; BARO, N-Sigma and its evaluator need only those above.
It needs Python 3.12 (or 3.8, 3.14). A suite downloads in seconds and runs in under a minute on 4 cores.

## Results (2026-10-17, Avg@5, RCAEval's evaluator at the pinned commit; D-65)

Every run starts from an empty `output/results` (RCAEval scores every file left there; D-63's first
numbers mixed runs and are superseded). BARO reproduces RCAEval's published RE2-TT numbers within 0.01
where our download is complete.

| Suite | BARO | multi-source BARO | N-Sigma | kernel, 60 s windows | Haiku over the evidence packs |
| --- | --- | --- | --- | --- | --- |
| RE1-OB | 0.896 | | 0.818 | 0.894 | |
| RE1-SS | 0.576 | | 0.868 | 0.922 | |
| RE1-TT | 0.262 | | 0.740 | 0.800 | |
| RE2-OB (90 cases) | 0.743 | 0.755 | 0.913 | 0.935 | **0.982** |
| RE2-TT (63 of 90) | 0.762 | | 0.790 | 0.643 | **0.960** |

The kernel's windows use a robust z with a fallback for flat baselines and keep columns that were flat
before the fault; both matter (D-65). Haiku reads the 40 strongest observations of a case and ranks
services; it recovers where statistics fall behind (Train Ticket, delay and loss), following how a
fault spreads to the services that call it.

```sh
python3.12 eval/rcaeval/packs.py build DATA/RE2/RE2-OB keys.json > prompts.jsonl   # opaque keys; keep keys.json from the model
python3.12 eval/rcaeval/run.py --dataset re2-ob --method answers --answers answers.jsonl --keys keys.json --label MODEL
```
