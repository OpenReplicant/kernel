"""The software adapter's plan: the shared kit's (`wmk_adapter.plan`), named after this
adapter so its runs and run claims say who mapped."""

from __future__ import annotations

from wmk_adapter.plan import SELF, Fact, Slugs, Source, normalize, similarity, trigrams
from wmk_adapter.plan import Plan as KitPlan

EXTRACTOR = "wmk-software"
EXTRACTOR_VERSION = "0.4.0"


class Plan(KitPlan):
    def __init__(self) -> None:
        super().__init__(EXTRACTOR, EXTRACTOR_VERSION)


__all__ = ["SELF", "Fact", "Plan", "Slugs", "Source", "normalize", "similarity", "trigrams"]
