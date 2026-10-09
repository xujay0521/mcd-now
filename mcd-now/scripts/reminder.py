# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from models import ExpiringCoupon
from state_store import read_json, write_json


DEFAULT_THRESHOLDS = (72.0, 24.0)


def threshold_for(
    hours_left: float,
    thresholds: Iterable[float] = DEFAULT_THRESHOLDS,
) -> float | None:
    """返回券当前落入的最小提醒阈值。

    Args:
        hours_left: 距离到期的小时数。
        thresholds: 提醒阈值小时数。

    Returns:
        已跨过的最小阈值；尚未进入提醒窗口时返回空。

    Raises:
        ValueError: 阈值为空或包含非正数。
    """
    normalized = sorted(set(thresholds))
    if not normalized or any(value <= 0 for value in normalized):
        raise ValueError("reminder thresholds must be positive")
    return next((value for value in normalized if hours_left <= value), None)


def new_reminders(
    coupons: Iterable[ExpiringCoupon],
    state_path: Path,
    thresholds: Iterable[float] = DEFAULT_THRESHOLDS,
) -> list[ExpiringCoupon]:
    """返回尚未发送过的券提醒并记录阈值标记。"""
    state = read_json(state_path, {"sent": {}})
    sent = state.setdefault("sent", {})
    results: list[ExpiringCoupon] = []
    for coupon in coupons:
        threshold = threshold_for(coupon.hours_left, thresholds)
        if threshold is None:
            continue
        marker = f"{threshold:g}"
        coupon_sent = sent.setdefault(coupon.instance_key, [])
        if marker in coupon_sent:
            continue
        coupon_sent.append(marker)
        results.append(coupon)
    write_json(state_path, state)
    return results
