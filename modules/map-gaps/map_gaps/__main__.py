"""Map gap worker (M5-T4): lists what the organisation and process maps people describe still lack, as
questions an interviewing agent asks next. Needs no runner.

It is a detector (D-110): the detector engine (modules/detectors) reads what the kernel applied from a stored
cursor, runs the gap finder as this module's role, and records each gap that formed or dissolved as an event.
detector.py writes what the interviewer reads: one change set per gap, an oa:Annotation (dcterms:type
urn:kk:gap-kind:<kind>, oa:hasTarget the step or post, oa:bodyValue the question), inferred and derived from
the claims that put the target on the map; a gap that is closed has its claims retracted, so the list
shrinks as the person answers. An unchanged gap is a no-op.

  python -m map_gaps [--once]
Env: DATABASE_URL (the module's own login, or one that may SET ROLE to KK_ROLE), KK_ROLE (default
kkm_map_gaps), KK_SWEEP_SECONDS (default 30).
"""
from __future__ import annotations

import argparse
import os
import sys

from detectors.__main__ import worker

from .detector import AGENT, DETECTOR, MODULE


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="List what the organisation and process maps lack, as questions.")
    ap.add_argument("--once", action="store_true", help="one pass, print the result and exit")
    a = ap.parse_args(argv)
    return worker([DETECTOR], os.environ.get("KK_ROLE", "kkm_map_gaps"), AGENT, MODULE, once=a.once)


if __name__ == "__main__":
    sys.exit(main())
