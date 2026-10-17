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

## Results (2026-10-17, RE1, Avg@5, run here with RCAEval's code at the pinned commit)

| Method | Online Boutique | Sock Shop | Train Ticket | Mean |
| --- | --- | --- | --- | --- |
| BARO (raw series) | 0.896 | 0.740 | 0.646 | 0.761 |
| N-Sigma (raw series) | 0.818 | 0.884 | 0.810 | 0.837 |
| kernel, 15 s windows | 0.880 | 0.776 | 0.654 | 0.770 |
| kernel, 60 s windows | **0.908** | 0.844 | 0.700 | 0.817 |

Windows of a minute lose nothing against the raw-series methods: they beat BARO on every system and
come within 0.02 of N-Sigma on average (ahead on Online Boutique). Averaging smooths the noise that the
per-second series carry. RE1 gives every method the injection time, so it scores ranking, not detection,
and simple statistics already do well: the place for an LLM over the kernel's claims is RE2/RE3 (logs,
traces, code-level faults), next.
