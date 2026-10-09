# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).parents[2] / "mcd-now" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from models import Preference  # noqa: E402
from preferences import merge_preferences  # noqa: E402
from services.menu_service import (  # noqa: E402
    build_default_order_item,
    build_menu_candidates,
    detail_requires_drink,
    extract_price_result,
    rank_candidates,
)


def preference(category: str, value: str, strength: str = "soft") -> Preference:
    return Preference(
        id=f"pref-{category}",
        category=category,
        value=value,
        strength=strength,
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
                        {"code": "single", "tags": ["单品", "板烧滋滋"]},
                    ],
                },
                {
                    "name": "巨无霸牛鱼肉堡",
                    "meals": [{"code": "beef", "tags": ["单品", "纯牛肉"]}],
                },
            ],
            "meals": {
                "combo": {"name": "板烧鸡腿堡三件套", "currentPrice": "34.5"},
                "single": {"name": "板烧鸡腿堡", "currentPrice": "23.5"},
                "beef": {"name": "巨无霸", "currentPrice": "25.5"},
            },
        }
    }


def test_build_menu_candidates_merges_official_categories_and_prices() -> None:
    candidates = build_menu_candidates(menu_payload())

    assert [item.code for item in candidates] == ["combo", "single", "beef"]
    assert candidates[0].price_cents == 3450
    assert candidates[0].is_combo is True
    assert "鸡肉汉堡/卷" in candidates[1].categories


def test_build_menu_candidates_rejects_unknown_product_code() -> None:
    payload = menu_payload()
    payload["data"]["categories"][0]["meals"].append({"code": "missing"})

    try:
        build_menu_candidates(payload)
    except ValueError as error:
        assert "missing menu detail" in str(error)
    else:
        raise AssertionError("category code without meal data should fail")


def test_rank_candidates_honors_restriction_and_single_item_preference() -> None:
    candidates = build_menu_candidates(menu_payload())
    preferences = [
        preference("restriction", "不吃牛肉", "hard"),
        preference("favorite", "鸡肉汉堡"),
        preference("meal_structure", "优先单点，不需要饮料"),
    ]

    ranked = rank_candidates(candidates, preferences)

    assert [item.candidate.code for item in ranked] == ["single", "combo"]
    assert "符合单点偏好" in ranked[0].reasons
    assert "命中偏爱：鸡肉汉堡" in ranked[0].reasons


def test_rank_candidates_treats_allergy_suffix_as_hard_restriction() -> None:
    candidates = build_menu_candidates(
        {
            "data": {
                "categories": [
                    {
                        "name": "甜品",
                        "meals": [
                            {"code": "unsafe", "tags": ["花生风味"]},
                            {"code": "safe", "tags": ["蓝莓风味"]},
                        ],
                    }
                ],
                "meals": {
                    "unsafe": {"name": "花生麦旋风", "currentPrice": "15"},
                    "safe": {"name": "蓝莓麦旋风", "currentPrice": "16"},
                },
            }
        }
    )

    ranked = rank_candidates(
        candidates,
        [preference("restriction", "花生过敏", "hard")],
    )

    assert [item.candidate.code for item in ranked] == ["safe"]


def test_rank_candidates_applies_budget_as_filter() -> None:
    candidates = build_menu_candidates(menu_payload())

    ranked = rank_candidates(candidates, [preference("budget", "预算25元以内")])

    assert [item.candidate.code for item in ranked] == ["single"]


def test_rank_candidates_applies_taste_drink_and_snack_preferences() -> None:
    candidates = build_menu_candidates(
        {
            "data": {
                "categories": [
                    {
                        "name": "鸡肉汉堡/卷",
                        "meals": [
                            {"code": "spicy", "tags": ["单品", "香辣"]},
                            {"code": "plain", "tags": ["单品"]},
                        ],
                    },
                    {
                        "name": "小食甜品",
                        "meals": [{"code": "fries", "tags": ["小食", "薯条"]}],
                    },
                    {
                        "name": "饮料",
                        "meals": [{"code": "cola", "tags": ["饮料", "可乐"]}],
                    },
                ],
                "meals": {
                    "spicy": {"name": "麦辣鸡腿堡", "currentPrice": "23"},
                    "plain": {"name": "板烧鸡腿堡", "currentPrice": "24"},
                    "fries": {"name": "中薯条", "currentPrice": "13"},
                    "cola": {"name": "中可乐", "currentPrice": "10"},
                },
            }
        }
    )
    preferences = [
        preference("taste", "喜欢辣"),
        preference("meal_structure", "不要饮料，喜欢小食"),
    ]

    ranked = rank_candidates(candidates, preferences)

    assert [item.candidate.code for item in ranked] == [
        "spicy",
        "fries",
        "plain",
        "cola",
    ]
    assert "符合口味偏好：辣" in ranked[0].reasons
    assert "符合小食偏好" in ranked[1].reasons
    assert "与饮料偏好冲突" in ranked[-1].reasons


def test_current_request_overrides_saved_meal_structure_before_ranking() -> None:
    candidates = build_menu_candidates(menu_payload())
    saved = [preference("meal_structure", "优先套餐")]
    current = [preference("meal_structure", "这次优先单点，不需要饮料")]

    ranked = rank_candidates(candidates, merge_preferences(saved, current))

    assert ranked[0].candidate.code == "single"
    assert "符合单点偏好" in ranked[0].reasons


def test_detail_requires_drink_only_for_mandatory_round() -> None:
    required = {
        "data": {
            "rounds": [
                {"name": "选择套餐内饮料", "minQuantity": 1, "choices": []}
            ]
        }
    }
    optional = {
        "data": {
            "rounds": [
                {"name": "可选饮料", "minQuantity": 0, "choices": []}
            ]
        }
    }

    assert detail_requires_drink(required) is True
    assert detail_requires_drink(optional) is False


def test_extract_price_result_uses_cents_and_take_way_list() -> None:
    payload = {
        "data": {
            "price": 1763,
            "discount": 587,
            "takeWayList": [
                {"code": "eat-in", "title": "堂食"},
                {"code": "locker-out", "title": "外带"},
            ],
        }
    }

    assert extract_price_result(payload) == {
        "price_cents": 1763,
        "discount_cents": 587,
        "take_ways": [
            {"code": "eat-in", "title": "堂食"},
            {"code": "locker-out", "title": "外带"},
        ],
    }


def test_extract_price_result_rejects_non_integer_cents() -> None:
    try:
        extract_price_result(
            {
                "data": {
                    "price": "17.63",
                    "discount": 0,
                    "takeWayList": [],
                }
            }
        )
    except ValueError as error:
        assert "non-negative cents" in str(error)
    else:
        raise AssertionError("price must use integer cents")


def test_build_menu_candidates_rejects_invalid_price_precision() -> None:
    payload = menu_payload()
    payload["data"]["meals"]["single"]["currentPrice"] = "23.555"

    try:
        build_menu_candidates(payload)
    except ValueError as error:
        assert "invalid menu price" in str(error)
    else:
        raise AssertionError("sub-cent menu price should fail")


def test_build_default_order_item_keeps_complete_modification_keys() -> None:
    detail = {
        "data": {
            "code": "single",
            "rounds": [],
            "modification": {
                "items": [
                    {
                        "values": [
                            {
                                "code": "sauce",
                                "selectedQuantity": 1,
                                "selectedKey": "0-1",
                                "unselectedKey": "0-0",
                            },
                            {
                                "code": "lettuce",
                                "selectedQuantity": 0,
                                "selectedKey": "0-1",
                                "unselectedKey": "0-0",
                            },
                        ]
                    }
                ]
            },
        }
    }

    assert build_default_order_item(detail) == {
        "productCode": "single",
        "quantity": 1,
        "modification": {
            "values": [
                {"code": "sauce", "key": "0-1", "quantity": 1},
                {"code": "lettuce", "key": "0-0", "quantity": 0},
            ]
        },
    }


def test_build_default_order_item_rejects_incomplete_required_round() -> None:
    detail = {
        "data": {
            "code": "combo",
            "rounds": [
                {
                    "id": 3,
                    "minQuantity": 2,
                    "choices": [
                        {"code": "drink", "quantity": 1, "isDefault": 1}
                    ],
                }
            ],
        }
    }

    try:
        build_default_order_item(detail)
    except ValueError as error:
        assert "required quantity" in str(error)
    else:
        raise AssertionError("incomplete mandatory combo round should fail")

