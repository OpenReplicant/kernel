"""Branch conditions: one canonical form for every way of writing a condition, refusals that
say what is wrong, and evaluation against a case's attributes."""

from __future__ import annotations

import pytest

from wmk_process.conditions import ConditionError, MissingAttribute, attributes, canonical, holds, parse


@pytest.mark.parametrize(
    ("written", "canon"),
    [
        ("amount > 10000", "amount > 10000"),
        ("amount>10000", "amount > 10000"),
        ("10000 < amount", "amount > 10000"),
        ("amount >= 10000.0", "amount >= 10000"),
        ("amount > 1.50", "amount > 1.5"),
        ("urgent", "urgent = true"),
        ("urgent == TRUE AND amount > 10000", "amount > 10000 and urgent = true"),
        ("amount > 10000 and urgent = true", "amount > 10000 and urgent = true"),
        ("status <> 'open'", 'status != "open"'),
        (
            "category in ['it', \"facilities\"] or amount <= 500",
            'amount <= 500 or category in ["facilities", "it"]',
        ),
        ("a = 1 and (b = 2 or c = 3)", "(b = 2 or c = 3) and a = 1"),
        ("(a = 1 and b = 2) and c = 3", "a = 1 and b = 2 and c = 3"),
        ("not (a = 1 or b = 'x')", 'not (a = 1 or b = "x")'),
        ("not not urgent", "not not (urgent = true)"),
        ("ELSE", "else"),
        ('name = "say \\"hi\\""', 'name = "say \\"hi\\""'),
    ],
)
def test_conditions_have_one_canonical_form(written: str, canon: str) -> None:
    assert canonical(written) == canon
    assert canonical(canon) == canon


@pytest.mark.parametrize(
    ("written", "says"),
    [
        ("", "empty"),
        ("amount >", "expected an attribute or a literal"),
        ("amount > 10,000", "unexpected"),
        ("€10000 < amount", "cannot read"),
        ("10000", "a literal alone"),
        ("amount > other", "compare an attribute with a literal"),
        ("else or amount > 1", "else stands alone"),
        ("(amount > 1", "expected )"),
        ("5 in [1, 2]", "in needs an attribute"),
    ],
)
def test_malformed_conditions_are_refused(written: str, says: str) -> None:
    with pytest.raises(ConditionError, match=says.replace("(", r"\(").replace(")", r"\)")):
        canonical(written)


def test_conditions_hold_for_a_case() -> None:
    case = {"amount": 12_000, "urgent": False, "category": "it"}
    assert holds(parse("amount > 10000"), case)
    assert holds(parse("amount > 10000 and not urgent"), case)
    assert not holds(parse("amount <= 10000 or urgent"), case)
    assert holds(parse('category in ["it", "facilities"]'), case)
    assert not holds(parse('category = "IT"'), case)
    # Booleans are not numbers, and mismatched types never order.
    assert not holds(parse("urgent = 0"), case)
    assert not holds(parse('amount > "a"'), case)
    # else never holds by itself: it is what is left when no other branch does.
    assert not holds(parse("else"), case)
    with pytest.raises(MissingAttribute):
        holds(parse("cost_centre = 4100"), case)
    assert attributes(parse("amount > 1 and (urgent or not category = 'x')")) == {
        "amount",
        "urgent",
        "category",
    }
