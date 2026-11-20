"""尺子口径：相对误差率全局唯一，绝对差值仅作对照物。"""
from __future__ import annotations

import pytest

from app.services import tolerance as tol


def test_rel_ruler_verdicts():
    # 5% ruler
    assert tol.classify_rel(100.0, 105.0, 0.05, 1e-9).outcome == tol.OUTCOME_AUTO
    assert tol.classify_rel(100.0, 105.0, 0.05, 1e-9).reason == tol.REASON_WITHIN
    assert tol.classify_rel(100.0, 100.0, 0.05, 1e-9).outcome == tol.OUTCOME_EXACT
    v = tol.classify_rel(100.0, 105.000001, 0.05, 1e-9)
    assert v.outcome == tol.OUTCOME_VARIANCE
    assert v.reason == tol.REASON_OVER


def test_rel_ruler_is_scale_free():
    """大基数微差与小基数同比例偏差得到相同判定。"""
    assert tol.classify_rel(10_000.0, 10_200.0, 0.05, 1e-9).outcome == tol.OUTCOME_AUTO
    assert tol.classify_rel(10.0, 10.2, 0.05, 1e-9).outcome == tol.OUTCOME_AUTO
    # 小基数大偏差比例上更大，必须被拦下
    assert tol.classify_rel(10.0, 20.0, 0.05, 1e-9).outcome == tol.OUTCOME_VARIANCE


def test_rel_ruler_guards_zero_and_missing():
    """零基数不得因分母为 0 而误合拢；缺边一律进差额。"""
    assert tol.classify_rel(0.0, 0.5, 0.05, 1e-9).outcome == tol.OUTCOME_VARIANCE
    assert tol.classify_rel(None, 10.0, 0.05, 1e-9).reason == tol.REASON_MISSING_ESTIMATE
    assert tol.classify_rel(10.0, None, 0.05, 1e-9).reason == tol.REASON_MISSING_ACTUAL
    assert tol.classify_rel(10.0, 10.0, 0.05, 1e-9, ambiguous=True).reason == tol.REASON_MULTI_DOC


def test_rel_ruler_rejects_bad_parameters():
    with pytest.raises(ValueError):
        tol.classify_rel(1.0, 1.0, 0.0, 1e-9)
    with pytest.raises(ValueError):
        tol.classify_rel(1.0, 1.0, 1.0, 1e-9)
    with pytest.raises(ValueError):
        tol.classify_rel(1.0, 1.0, 0.05, 0.0)


def test_two_rulers_diverge_on_same_readings():
    """同一组读数：绝对尺子与相对尺子给出不同 matched 集合。"""
    # 小基数、大比例：绝对 1.0 放过，相对 5% 拦下
    assert tol.classify_abs(10.0, 10.6, 1.0).outcome == tol.OUTCOME_AUTO
    assert tol.classify_rel(10.0, 10.6, 0.05, 1e-9).outcome == tol.OUTCOME_VARIANCE
    # 大基数、小比例：绝对 0.5 拦下，相对 5% 放过
    assert tol.classify_abs(310.0, 311.0, 0.5).outcome == tol.OUTCOME_VARIANCE
    assert tol.classify_rel(310.0, 311.0, 0.05, 1e-9).outcome == tol.OUTCOME_AUTO
