from __future__ import annotations

import codey.saas.intelligence.router as router


def test_router_numeric_coercion_rejects_non_finite_values() -> None:
    assert router._coerce_int(float("inf"), 7) == 7
    assert router._coerce_int(float("-inf"), 7) == 7
    assert router._coerce_int(float("nan"), 7) == 7
    assert router._coerce_float("nan", 0.5) == 0.5
    assert router._coerce_float("inf", 0.5) == 0.5
    assert router._coerce_float("-inf", 0.5) == 0.5
    assert router._coerce_float("0.75", 0.5) == 0.75
