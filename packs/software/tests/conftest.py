"""The software pack's tests run on kernel databases with the software pack installed."""

import pytest


@pytest.fixture(scope="session")
def wmk_packs() -> tuple[str, ...]:
    return ("software",)
