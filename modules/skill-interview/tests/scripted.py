"""The scripted interview (interviewer/data/acme.yaml): kept here so the tests import it as before."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interviewer.script import *  # noqa: E402,F401,F403
