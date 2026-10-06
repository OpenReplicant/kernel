"""The process pack's tests run on kernel databases with the process pack installed."""

import pytest


@pytest.fixture(scope="session")
def wmk_packs() -> tuple[str, ...]:
    return ("process",)
