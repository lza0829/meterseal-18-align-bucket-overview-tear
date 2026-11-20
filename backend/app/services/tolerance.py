"""全局统一的容差尺子。

本系统全局只贯彻 **相对误差率** 一种架构 (``tol_mode = "rel"``)：

    ratio = |actual - estimate| / max(|estimate|, eps)
    ratio <= rel_tol   -> 自动合拢 (matched, 写审计)
    ratio >  rel_tol   -> 生成差额 (fate = pending, 必须人工处置)

绝对差值尺子 (``tol_mode = "abs"``) 只保留在本模块中作为测试对照物，
用于证明两把尺子会在同一组读数上给出不同的 matched 集合；
它绝不会被用于落库，也不允许通过设置接口切换。
"""
from __future__ import annotations

from dataclasses import dataclass

TOL_MODE = "rel"

OUTCOME_EXACT = "exact"
OUTCOME_AUTO = "auto_matched"
OUTCOME_VARIANCE = "variance"

REASON_EXACT = "exact"
REASON_WITHIN = "within_tolerance"
REASON_OVER = "over_tolerance"
REASON_MISSING_ESTIMATE = "missing_estimate"
REASON_MISSING_ACTUAL = "missing_actual"
REASON_MULTI_DOC = "multi_doc"

DEFAULT_REL_TOL = 0.05
DEFAULT_REL_EPS = 1e-9


@dataclass(frozen=True)
class Verdict:
    outcome: str
    ratio: float | None
    reason: str


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _base_verdict(estimate, actual, *, ambiguous: bool = False) -> tuple[float, float] | Verdict:
    """Shared guard rails: missing sides and duplicate documents never auto-close."""
    est = _as_float(estimate)
    act = _as_float(actual)
    if est is None:
        return Verdict(OUTCOME_VARIANCE, None, REASON_MISSING_ESTIMATE)
    if act is None:
        return Verdict(OUTCOME_VARIANCE, None, REASON_MISSING_ACTUAL)
    if ambiguous:
        return Verdict(OUTCOME_VARIANCE, None, REASON_MULTI_DOC)
    return est, act


def classify_rel(
    estimate,
    actual,
    rel_tol: float,
    eps: float,
    *,
    ambiguous: bool = False,
) -> Verdict:
    """相对误差率尺子（本系统唯一在用的口径）。"""
    if rel_tol <= 0 or rel_tol >= 1:
        raise ValueError("rel_tol 必须落在 (0, 1) 区间")
    if eps <= 0:
        raise ValueError("rel_eps 必须为正数")

    guarded = _base_verdict(estimate, actual, ambiguous=ambiguous)
    if isinstance(guarded, Verdict):
        return guarded
    est, act = guarded

    delta = act - est
    if delta == 0:
        return Verdict(OUTCOME_EXACT, 0.0, REASON_EXACT)

    ratio = abs(delta) / max(abs(est), eps)
    if ratio <= rel_tol:
        return Verdict(OUTCOME_AUTO, ratio, REASON_WITHIN)
    return Verdict(OUTCOME_VARIANCE, ratio, REASON_OVER)


def classify_abs(
    estimate,
    actual,
    abs_tol: float,
    *,
    ambiguous: bool = False,
) -> Verdict:
    """绝对差值尺子。仅供测试对照，生产路径不得调用。"""
    if abs_tol < 0:
        raise ValueError("abs_tol 不得为负")

    guarded = _base_verdict(estimate, actual, ambiguous=ambiguous)
    if isinstance(guarded, Verdict):
        return guarded
    est, act = guarded

    delta = act - est
    if delta == 0:
        return Verdict(OUTCOME_EXACT, 0.0, REASON_EXACT)

    ratio = abs(delta) / max(abs(est), 1e-9)
    if abs(delta) <= abs_tol:
        return Verdict(OUTCOME_AUTO, ratio, REASON_WITHIN)
    return Verdict(OUTCOME_VARIANCE, ratio, REASON_OVER)
