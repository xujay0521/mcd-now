# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).parents[2] / "mcd-now" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from models import Preference  # noqa: E402
from services.menu_service import recommend_pickup  # noqa: E402


def preference(category: str, value: str) -> Preference:
    return Preference(
        id=f"pref-{category}",
        category=category,
        value=value,
        strength="soft",
        source="explicit",
        created_at="2026-10-09T12:00:00+08:00",
        updated_at="2026-10-09T12:00:00+08:00",
    )


def menu_payload() -> dict[str, object]:
    return {
        "data": {
            "categories": [
                {
                    "name": "鸡肉汉堡/卷",
                    "meals": [
                        {"code": "combo", "tags": ["套餐"]},
                        {"code": "single", "tags": ["单品", "不辣"]},
                    ],
                },
                {
                    "name": "安格斯牛肉堡",
                    "meals": [{"code": "beef", "tags": ["单品", "含牛肉"]}],
                },
            ],
            "meals": {
                "combo": {"name": "板烧鸡腿堡套餐", "currentPrice": "34.5"},
                "single": {"name": "板烧鸡腿堡", "currentPrice": "23.5"},
                "beef": {"name": "安格斯堡", "currentPrice": "25.5"},
            },
        }
    }


class RecommendationClient:
    """为推荐编排提供固定 MCP 响应。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((name, arguments))
        if name == "query-meals":
            return menu_payload()
        if name == "query-meal-detail":
            code = str(arguments["code"])
            if code == "combo":
                return {
                    "data": {
                        "code": code,
                        "rounds": [
                            {
                                "id": 3,
                                "name": "选择套餐内饮料",
                                "minQuantity": 1,
                                "choices": [],
                            }
                        ],
                    }
                }
            return {"data": {"code": code, "rounds": []}}
        if name == "calculate-price":
            item = arguments["items"][0]
            price = 2350 if item["productCode"] == "single" else 2550
            return {
                "data": {
                    "price": price,
                    "discount": 0,
                    "takeWayList": [{"code": "eat-in", "title": "堂食"}],
                }
            }
        raise AssertionError(f"unexpected tool: {name}")


def test_recommend_pickup_excludes_combo_with_required_drink() -> None:
    client = RecommendationClient()
    preferences = [
        preference("favorite", "鸡肉汉堡"),
        preference("meal_structure", "优先单点，不需要饮料"),
    ]

    results = recommend_pickup(client, "1450713", preferences, limit=2)

    assert [item["product_code"] for item in results] == ["single", "beef"]
    assert results[0]["price_cents"] == 2350
    assert results[0]["price_verified"] is True
    assert "符合单点偏好" in results[0]["reasons"]
    detail_calls = [call for call in client.calls if call[0] == "query-meal-detail"]
    assert [call[1]["code"] for call in detail_calls] == [
        "single",
        "combo",
        "beef",
    ]


def test_recommend_pickup_limits_candidates_to_coupon_products() -> None:
    client = RecommendationClient()

    results = recommend_pickup(
        client,
        "1450713",
        [preference("favorite", "鸡肉汉堡")],
        eligible_product_codes={"single"},
        coupon_id="coupon-id",
        coupon_code="coupon-code",
    )

    assert [item["product_code"] for item in results] == ["single"]
    assert results[0]["order_item"]["couponId"] == "coupon-id"
    assert results[0]["order_item"]["couponCode"] == "coupon-code"
    assert results[0]["store_code"] == "1450713"
    assert results[0]["coupon"] == {"coupon_id": "coupon-id", "applied": True}
    assert "coupon-code" not in str(results[0]["coupon"])
    assert results[0]["price_source"] == "calculate-price"
    price_call = next(call for call in client.calls if call[0] == "calculate-price")
    assert price_call[1]["items"][0]["couponCode"] == "coupon-code"


def test_recommend_pickup_requires_coupon_identity_pair() -> None:
    try:
        recommend_pickup(
            RecommendationClient(),
            "1450713",
            [],
            coupon_id="coupon-id",
        )
    except ValueError as error:
        assert "coupon identity" in str(error)
    else:
        raise AssertionError("partial coupon identity should fail")
