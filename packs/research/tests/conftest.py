"""The research pack's tests run on kernel databases with the research pack installed."""

import pytest


@pytest.fixture(scope="session")
def wmk_packs() -> tuple[str, ...]:
    return ("research",)
