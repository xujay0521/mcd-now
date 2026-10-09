# -*- coding: utf-8 -*-
from __future__ import annotations

import math
from typing import Any, Protocol

from coupon_service import match_coupon_instance


class ToolClient(Protocol):
    """门店核销检查所需的最小 MCP 客户端接口。"""

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP 工具并返回 JSON 对象。"""
        ...


def estimate_walking_minutes(
    distance_meters: float,
    speed_meters_per_minute: float = 80.0,
) -> int | None:
    """按直线距离粗略估算步行分钟数。

    Args:
        distance_meters: MCP 返回的距离米数。
        speed_meters_per_minute: 估算步速，默认每分钟 80 米。

    Returns:
        向上取整的分钟数；距离不为正时返回空。

    Raises:
        ValueError: 步速不为正。
    """
    if speed_meters_per_minute <= 0:
        raise ValueError("walking speed must be positive")
    if distance_meters <= 0:
        return None
    return math.ceil(distance_meters / speed_meters_per_minute)


def find_redeemable_stores(
    client: ToolClient,
    target_coupon: dict[str, Any],
    stores: list[dict[str, Any]],
    current_store_code: str | None = None,
    max_checks: int = 5,
    max_results: int = 3,
) -> dict[str, Any]:
    """验证当前门店，必要时查找附近可核销门店。

    Args:
        client: 只读 MCP 客户端。
        target_coupon: 用户券包中的目标券实例。
        stores: `query-nearby-stores` 返回的门店数组。
        current_store_code: 用户当前选择的门店编码。
        max_checks: 包含当前门店在内的最大检查数。
        max_results: 最大返回门店数。

    Returns:
        覆盖统计、当前门店结论和脱敏后的可用门店列表。

    Raises:
        ValueError: 检查或结果上限非法。
    """
    if max_checks < 1 or max_results < 1:
        raise ValueError("store limits must be positive")
    open_stores = [item for item in stores if item.get("businessStatus") is True]
    ordered = sorted(open_stores, key=_store_distance_key)
    current = next(
        (
            item
            for item in ordered
            if str(item.get("storeCode")) == str(current_store_code)
        ),
        None,
    )
    candidates = [
        item
        for item in ordered
        if str(item.get("storeCode")) != str(current_store_code)
    ]
    if current is not None:
        candidates.insert(0, current)
    checked = 0
    matched: list[dict[str, Any]] = []
    current_supported: bool | None = None
    for store in candidates[:max_checks]:
        store_code = str(store.get("storeCode") or "").strip()
        if not store_code:
            continue
        checked += 1
        payload = client.call_tool(
            "query-store-coupons",
            {"storeCode": store_code, "orderType": 1, "beType": 1},
        )
        store_coupons = _extract_store_coupons(payload)
        supported = match_coupon_instance(target_coupon, store_coupons) is not None
        is_current = store_code == str(current_store_code)
        if is_current:
            current_supported = supported
            if supported:
                matched.append(_store_summary(store))
                break
            continue
        if supported:
            matched.append(_store_summary(store))
            if len(matched) >= max_results:
                break
    return {
        "checked_count": checked,
        "current_store_supported": current_supported,
        "fallback": current_supported is False,
        "stores": matched,
        "eta_notice": "按门店距离和每分钟 80 米粗略估算，不是地图导航时间",
    }


def inspect_coupon_redemption(
    client: ToolClient,
    target_coupon: dict[str, Any],
    city: str | None,
    keyword: str | None,
    current_store_code: str | None = None,
) -> dict[str, Any]:
    """按用户本次位置查询并验证目标券可核销门店。

    Args:
        client: 只读 MCP 客户端。
        target_coupon: 用户券包中的目标券实例。
        city: 用户本次提供的城市。
        keyword: 用户本次提供的位置关键词。
        current_store_code: 可选的当前门店编码。

    Returns:
        门店核销验证结果；缺少位置时返回未验证原因。

    Raises:
        ValueError: 城市和关键词只提供了其中一个，或上游结构非法。
    """
    normalized_city = (city or "").strip()
    normalized_keyword = (keyword or "").strip()
    if not normalized_city and not normalized_keyword:
        return {
            "verified": False,
            "reason": "location_not_provided",
            "stores": [],
        }
    if not normalized_city or not normalized_keyword:
        raise ValueError("city and keyword must be provided together")
    payload = client.call_tool(
        "query-nearby-stores",
        {
            "searchType": 2,
            "beType": 1,
            "city": normalized_city,
            "keyword": normalized_keyword,
        },
    )
    data = payload.get("data", payload)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError("nearby store response data must be a list of objects")
    result = find_redeemable_stores(
        client,
        target_coupon,
        data,
        current_store_code,
    )
    return {"verified": True, **result}


def _extract_store_coupons(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从门店券响应提取券实例列表。"""
    data = payload.get("data", payload)
    if not isinstance(data, list):
        raise ValueError("store coupon response data must be a list")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("store coupons must be objects")
    return data


def _store_distance_key(store: dict[str, Any]) -> tuple[bool, float]:
    """将缺失或非正距离排在已知距离之后。"""
    distance = store.get("distance")
    if isinstance(distance, (int, float)) and distance > 0:
        return False, float(distance)
    return True, float("inf")


def _store_summary(store: dict[str, Any]) -> dict[str, Any]:
    """返回不含地址和坐标的门店提醒摘要。"""
    distance = store.get("distance")
    numeric_distance = (
        float(distance) if isinstance(distance, (int, float)) else 0.0
    )
    return {
        "storeCode": str(store.get("storeCode") or ""),
        "storeName": str(store.get("storeName") or "未命名门店"),
        "distance": distance,
        "etaMinutes": estimate_walking_minutes(numeric_distance),
    }
