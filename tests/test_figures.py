"""The figures stay in step with what draws them: each committed SVG is what
docs/figures/figures.py draws now, and every figure the README or an ADR embeds exists."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIGURES = ROOT / "docs" / "figures"


def test_the_committed_figures_are_what_the_script_draws() -> None:
    spec = importlib.util.spec_from_file_location("figures", FIGURES / "figures.py")
    assert spec is not None and spec.loader is not None
    figures = importlib.util.module_from_spec(spec)
    sys.modules["figures"] = figures  # dataclasses look their module up while it loads
    spec.loader.exec_module(figures)
    drawn = {
        f"{name}{theme.suffix}.svg": draw(theme)
        for name, draw in (("architecture", figures.architecture), ("trajectory", figures.trajectory))
        for theme in (figures.LIGHT, figures.DARK)
    }
    assert sorted(drawn) == sorted(p.name for p in FIGURES.glob("*.svg"))
    stale = [name for name, svg in drawn.items() if (FIGURES / name).read_text() != svg]
    assert stale == [], f"run `uv run python docs/figures/figures.py`: {stale} changed"


def test_embedded_figures_exist() -> None:
    pages = [ROOT / "README.md", *sorted((ROOT / "docs" / "decisions").glob("*.md"))]
    embedded = 0
    for page in pages:
        for ref in re.findall(r'(?:src|srcset)="([^"]+\.svg)"', page.read_text()):
            assert (page.parent / ref).resolve().is_file(), f"{page.name}: {ref}"
            embedded += 1
    assert embedded >= 4
