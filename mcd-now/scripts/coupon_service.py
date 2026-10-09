# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from datetime import datetime, time
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from models import ExpiringCoupon


SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_COUPON_PAGES = 5


class ToolClient(Protocol):
    """券包分页所需的最小 MCP 客户端接口。"""

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP 工具并返回 JSON 对象。"""
        ...


def parse_duration(value: str) -> float:
    """将小时或天数时长转换为小时。"""
    if len(value) < 2 or value[-1] not in {"h", "d"}:
        raise ValueError("duration must end with h or d")
    amount = float(value[:-1])
    if amount <= 0:
        raise ValueError("duration must be positive")
    return amount if value[-1] == "h" else amount * 24


def coupon_instance_key(coupon: dict[str, Any]) -> str:
    """返回不暴露真实券码的稳定券实例摘要。"""
    coupon_id = str(coupon.get("id") or coupon.get("couponId") or "")
    coupon_code = str(coupon.get("code") or coupon.get("couponCode") or "")
    if not coupon_id or not coupon_code:
        raise ValueError("coupon requires id and code")
    raw = f"{coupon_id}:{coupon_code}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def extract_coupons(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从 MCP 券包响应中提取券列表。"""
    data = payload.get("data", payload)
    coupons = data.get("coupons") if isinstance(data, dict) else None
    if not isinstance(coupons, list):
        raise ValueError("input does not contain data.coupons")
    return coupons


def fetch_all_coupons(client: ToolClient) -> dict[str, Any]:
    """分页读取当前用户的完整券包。

    Args:
        client: 提供只读工具调用的 MCP 客户端。

    Returns:
        合并后的 `data.coupons` 响应对象。

    Raises:
        ValueError: 分页元数据非法或超过官方五页上限。
    """
    combined: list[dict[str, Any]] = []
    total_pages = 1
    page = 1
    while page <= total_pages:
        payload = client.call_tool(
            "query-my-coupons",
            {"page": str(page), "pageSize": "200"},
        )
        combined.extend(extract_coupons(payload))
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise ValueError("coupon response data must be an object")
        raw_total_pages = data.get("totalPages", 1)
        if not isinstance(raw_total_pages, int) or raw_total_pages < 1:
            raise ValueError("coupon response has invalid totalPages")
        if raw_total_pages > MAX_COUPON_PAGES:
            raise ValueError("coupon response exceeds the official five pages limit")
        total_pages = raw_total_pages
        page += 1
    return {
        "success": True,
        "data": {
            "coupons": combined,
            "currentPage": total_pages,
            "pageSize": 200,
            "totalCount": len(combined),
            "totalPages": total_pages,
        },
    }


def coupon_is_available(
    coupon: dict[str, Any],
    now: datetime,
    order_type: int,
    be_type: int,
) -> bool:
    """判断券在指定时间和到店履约方式下是否具备基础可用条件。"""
    current = now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now
    if int(coupon.get("enable", 1)) != 1:
        return False
    if int(coupon.get("currentDayAvailableCount", 1)) <= 0:
        return False
    if not _contains(coupon, "orderTypes", "orderType", order_type):
        return False
    if not _contains(coupon, "beTypes", "beType", be_type):
        return False
    if not _within_validity(coupon, current):
        return False
    if not _within_weekdays(coupon, current):
        return False
    return _within_trade_periods(coupon, current.timetz().replace(tzinfo=None))


def match_coupon_instance(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """按券 ID 和券码同时匹配指定券实例。"""
    target_id = str(target.get("id") or target.get("couponId") or "")
    target_code = str(target.get("code") or target.get("couponCode") or "")
    if not target_id or not target_code:
        raise ValueError("target coupon requires id and code")
    for candidate in candidates:
        candidate_id = str(candidate.get("id") or candidate.get("couponId") or "")
        candidate_code = str(
            candidate.get("code") or candidate.get("couponCode") or ""
        )
        if candidate_id == target_id and candidate_code == target_code:
            return candidate
    return None


def extract_available_coupon_names(payload: dict[str, Any]) -> list[str]:
    """提取当前可领取的券名称并按原顺序去重。"""
    data = payload.get("data", payload)
    if not isinstance(data, list):
        raise ValueError("available coupon response data must be a list")
    names: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or item.get("couponStatus") != "CAN_GET":
            continue
        name = str(item.get("couponName") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def claim_available_coupons(
    client: ToolClient,
    confirmed: bool,
) -> dict[str, Any]:
    """经用户明确确认后调用一键领券工具。

    Args:
        client: 交互式宿主提供的 MCP 客户端。
        confirmed: 用户是否已确认领取全部当前可领券。

    Returns:
        `auto-bind-coupons` 的原始结果对象。

    Raises:
        PermissionError: 用户尚未明确确认。
    """
    if not confirmed:
        raise PermissionError("coupon claim requires explicit confirmation")
    return client.call_tool("auto-bind-coupons", {})


def _contains(
    coupon: dict[str, Any],
    plural_key: str,
    singular_key: str,
    expected: int,
) -> bool:
    """兼容 MCP 单值和列表字段并判断是否包含目标值。"""
    values = coupon.get(plural_key)
    if values is None:
        value = coupon.get(singular_key)
        values = [value] if value is not None else []
    if not isinstance(values, list):
        return False
    return expected in values


def _within_validity(coupon: dict[str, Any], current: datetime) -> bool:
    """判断当前时间是否位于券的日期有效期。"""
    raw_start = coupon.get("tradeStartDateTime")
    raw_end = coupon.get("tradeEndDateTime")
    if not isinstance(raw_start, str) or not isinstance(raw_end, str):
        return False
    try:
        start = datetime.strptime(raw_start, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=SHANGHAI
        )
        end = datetime.strptime(raw_end, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=SHANGHAI
        )
    except ValueError:
        return False
    return start <= current.astimezone(SHANGHAI) <= end


def _within_weekdays(coupon: dict[str, Any], current: datetime) -> bool:
    """按 ISO 星期数判断券是否限制在当天使用。"""
    weekdays = coupon.get("weekdays", [])
    if not weekdays:
        return True
    if not isinstance(weekdays, list):
        return False
    return current.astimezone(SHANGHAI).isoweekday() in weekdays


def _within_trade_periods(coupon: dict[str, Any], current: time) -> bool:
    """判断当前本地时间是否落在任一核销时段。"""
    periods = coupon.get("tradeTimePeriods", [])
    if not periods:
        return True
    if not isinstance(periods, list):
        return False
    for period in periods:
        if not isinstance(period, dict):
            continue
        try:
            start = time.fromisoformat(str(period["startTime"]))
            end = time.fromisoformat(str(period["endTime"]))
        except (KeyError, ValueError):
            continue
        if start <= end and start <= current <= end:
            return True
        if start > end and (current >= start or current <= end):
            return True
    return False


def find_expiring(
    payload: dict[str, Any],
    within_hours: float,
    now: datetime | None = None,
) -> list[ExpiringCoupon]:
    """查找指定小时范围内到期且启用的券实例。"""
    current = now or datetime.now(SHANGHAI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI)
    results: list[ExpiringCoupon] = []
    for coupon in extract_coupons(payload):
        raw_end = coupon.get("tradeEndDateTime")
        if not raw_end:
            continue
        expires_at = datetime.strptime(raw_end, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=SHANGHAI
        )
        hours_left = (expires_at - current).total_seconds() / 3600
        enabled = int(coupon.get("enable", 1)) == 1
        if 0 <= hours_left <= within_hours and enabled:
            results.append(
                ExpiringCoupon(
                    instance_key=coupon_instance_key(coupon),
                    title=str(coupon.get("title") or "未命名优惠券"),
                    expires_at=expires_at,
                    hours_left=hours_left,
                    enabled=enabled,
                )
            )
    return sorted(results, key=lambda item: item.expires_at)
