# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from models import Preference
from services.menu_parser import (
    MenuCandidate,
    build_default_order_item,
    build_menu_candidates,
    detail_requires_drink,
    extract_price_result,
    price_to_cents,
)


class ToolClient(Protocol):
    """菜单推荐所需 MCP 客户端的结构化约定。"""

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP 工具并返回 JSON 对象。"""
        ...


@dataclass(frozen=True)
class RankedCandidate:
    """带可解释理由的排序候选。"""

    candidate: MenuCandidate
    reasons: tuple[str, ...]
    rank_key: tuple[int, ...]


def rank_candidates(
    candidates: list[MenuCandidate],
    preferences: list[Preference],
) -> list[RankedCandidate]:
    """应用硬约束、预算和软偏好对菜单候选排序。

    Args:
        candidates: 归一后的菜单候选。
        preferences: 已合并的本次有效偏好。

    Returns:
        已过滤并按匹配程度排序的候选，不公开数值分。
    """
    restrictions = [item for item in preferences if item.category == "restriction"]
    budget_cents = _budget_limit(preferences)
    favorite_terms = _terms(preferences, "favorite")
    dislike_terms = _terms(preferences, "dislike")
    meal_preferences = " ".join(_terms(preferences, "meal_structure"))
    taste_terms = [_preference_term(value) for value in _terms(preferences, "taste")]
    taste_terms = [term for term in taste_terms if term]
    prefer_single = "单点" in meal_preferences or "不要套餐" in meal_preferences
    prefer_combo = "套餐" in meal_preferences and not prefer_single
    avoid_drink = "不要饮料" in meal_preferences or "不需要饮料" in meal_preferences
    prefer_snack = "小食" in meal_preferences and "不要小食" not in meal_preferences
    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        searchable = _candidate_text(candidate)
        if any(_restriction_matches(item.value, searchable) for item in restrictions):
            continue
        if budget_cents is not None and candidate.price_cents > budget_cents:
            continue
        reasons: list[str] = []
        favorite_hits = [term for term in favorite_terms if term in searchable]
        dislike_hits = [term for term in dislike_terms if term in searchable]
        taste_hits = [term for term in taste_terms if term in searchable]
        reasons.extend(f"命中偏爱：{term}" for term in favorite_hits)
        reasons.extend(f"命中反感：{term}" for term in dislike_hits)
        reasons.extend(f"符合口味偏好：{term}" for term in taste_hits)
        structure_score = 0
        if prefer_single and not candidate.is_combo:
            reasons.append("符合单点偏好")
            structure_score = 1
        elif prefer_single and candidate.is_combo:
            reasons.append("与单点偏好冲突")
            structure_score = -1
        elif prefer_combo and candidate.is_combo:
            reasons.append("符合套餐偏好")
            structure_score = 1
        candidate_is_drink = _contains_any(searchable, ("饮料", "可乐", "咖啡", "茶"))
        candidate_is_snack = _contains_any(searchable, ("小食", "薯条", "鸡块", "派"))
        if avoid_drink and candidate_is_drink:
            reasons.append("与饮料偏好冲突")
            structure_score -= 1
        if prefer_snack and candidate_is_snack:
            reasons.append("符合小食偏好")
            structure_score += 1
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                reasons=tuple(reasons),
                rank_key=(
                    len(favorite_hits),
                    len(taste_hits),
                    structure_score,
                    -len(dislike_hits),
                ),
            )
        )
    return sorted(
        ranked,
        key=lambda item: (
            -item.rank_key[0],
            -item.rank_key[1],
            -item.rank_key[2],
            -item.rank_key[3],
            item.candidate.price_cents,
            item.candidate.code,
        ),
    )


def recommend_pickup(
    client: ToolClient,
    store_code: str,
    preferences: list[Preference],
    limit: int = 3,
    eligible_product_codes: set[str] | None = None,
    coupon_id: str | None = None,
    coupon_code: str | None = None,
) -> list[dict[str, Any]]:
    """查询菜单、验证详情并核价生成到店自取推荐。

    Args:
        client: 交互式宿主提供的 MCP 客户端。
        store_code: 用户选定的门店编码。
        preferences: 本次已合并的有效偏好。
        limit: 最大推荐数量。
        eligible_product_codes: 目标券在门店返回的可核销商品编码。
        coupon_id: 可选的目标券 ID。
        coupon_code: 可选的目标券实例码。

    Returns:
        含解释理由、默认订单项和实时核价的推荐列表。

    Raises:
        ValueError: 门店编码或数量非法，或上游响应结构非法。
    """
    if not store_code.strip() or limit < 1:
        raise ValueError("store code and positive recommendation limit are required")
    if bool(coupon_id) != bool(coupon_code):
        raise ValueError("coupon identity requires both id and code")
    menu = client.call_tool(
        "query-meals",
        {"storeCode": store_code, "orderType": 1, "beType": 1},
    )
    candidates = build_menu_candidates(menu)
    if eligible_product_codes is not None:
        candidates = [
            item for item in candidates if item.code in eligible_product_codes
        ]
    ranked = rank_candidates(candidates, preferences)
    avoid_drink = any(
        "不要饮料" in item.value or "不需要饮料" in item.value
        for item in preferences
        if item.category == "meal_structure"
    )
    results: list[dict[str, Any]] = []
    for ranked_item in ranked:
        candidate = ranked_item.candidate
        detail = client.call_tool(
            "query-meal-detail",
            {
                "storeCode": store_code,
                "orderType": 1,
                "beType": 1,
                "code": candidate.code,
            },
        )
        if candidate.is_combo and avoid_drink and detail_requires_drink(detail):
            continue
        order_item = build_default_order_item(detail)
        if coupon_id and coupon_code:
            order_item["couponId"] = coupon_id
            order_item["couponCode"] = coupon_code
        price_payload = client.call_tool(
            "calculate-price",
            {
                "storeCode": store_code,
                "orderType": 1,
                "beType": 1,
                "items": [order_item],
            },
        )
        price = extract_price_result(price_payload)
        results.append(
            {
                "product_code": candidate.code,
                "product_name": candidate.name,
                "store_code": store_code,
                "coupon": {
                    "coupon_id": coupon_id,
                    "applied": bool(coupon_id),
                },
                "order_item": order_item,
                "price_cents": price["price_cents"],
                "discount_cents": price["discount_cents"],
                "take_ways": price["take_ways"],
                "price_verified": True,
                "price_source": "calculate-price",
                "reasons": list(ranked_item.reasons),
                "unverified": ["过敏原以麦当劳官方披露为准"],
            }
        )
        if len(results) >= limit:
            break
    return results


def _candidate_text(candidate: MenuCandidate) -> str:
    """合并仅来自官方菜单的可检索文本。"""
    return " ".join((candidate.name, *candidate.categories, *candidate.tags))


def _terms(preferences: list[Preference], category: str) -> list[str]:
    """提取指定类别的非空偏好文本。"""
    return [item.value.strip() for item in preferences if item.category == category]


def _restriction_matches(value: str, searchable: str) -> bool:
    """将常见否定前缀剥离后匹配官方菜单文本。"""
    term = re.sub(r"^(不吃|不要|忌|禁食)", "", value.strip())
    term = re.sub(r"(过敏|忌口)$", "", term).strip()
    return bool(term and term in searchable)


def _preference_term(value: str) -> str:
    """剥离常见口语前缀，保留可与官方标签匹配的偏好词。"""
    return re.sub(r"^(我)?(喜欢|偏爱|爱吃|爱喝)", "", value.strip())


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    """判断官方菜单文本是否包含任一分类词。"""
    return any(term in value for term in terms)


def _budget_limit(preferences: list[Preference]) -> int | None:
    """从明确预算偏好中提取最严格的元金额上限。"""
    limits: list[int] = []
    for item in preferences:
        if item.category != "budget":
            continue
        match = re.search(r"(\d+(?:\.\d{1,2})?)\s*元", item.value)
        if match:
            limits.append(price_to_cents(match.group(1)))
    return min(limits) if limits else None
