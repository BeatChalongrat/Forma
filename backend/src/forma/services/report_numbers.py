"""Bounded decimal arithmetic: never silently round an additive business total."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from forma.core.errors import ValidationFailed


def precision(*values: Decimal) -> int:
    needed = max(v.adjusted() for v in values) - min(int(v.as_tuple().exponent) for v in values) + 4
    if needed > 1000:
        raise ValidationFailed(
            "Numeric precision exceeds the report limit; confirm or normalize the unit first"
        )
    return max(28, needed)


def add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = precision(left, right)
        return left + right


def percentage(change: Decimal, baseline: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = max(precision(change, baseline), abs(change.adjusted() - baseline.adjusted()) + 32)
        if ctx.prec > 1000:
            raise ValidationFailed("Percentage exceeds the report precision limit")
        return (change / baseline.copy_abs() * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
