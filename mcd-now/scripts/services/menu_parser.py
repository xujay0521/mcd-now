# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class MenuCandidate:
    """从官方菜单归一出的可推荐餐品。"""

    code: str
    name: str
    price_cents: int
    categories: tuple[str, ...]
    tags: tuple[str, ...]
    is_combo: bool


def build_menu_candidates(payload: dict[str, Any]) -> list[MenuCandidate]:
    """将 `query-meals` 响应归一为去重候选列表。

    Args:
        payload: MCP 菜单响应。

    Returns:
        按官方分类首次出现顺序排列的餐品候选。

    Raises:
        ValueError: 菜单结构、商品编码或价格非法。
    """
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("menu response data must be an object")
    categories = data.get("categories")
    meals = data.get("meals")
    if not isinstance(categories, list) or not isinstance(meals, dict):
        raise ValueError("menu response requires categories and meals")
    by_code: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError("menu category must be an object")
        category_name = normalize_text(category.get("name"))
        entries = category.get("meals")
        if not category_name or not isinstance(entries, list):
            raise ValueError("menu category requires name and meals")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("menu category meal must be an object")
            code = normalize_text(entry.get("code"))
            if not code or code not in meals or not isinstance(meals[code], dict):
                raise ValueError(f"missing menu detail for product code: {code}")
            if code not in by_code:
                by_code[code] = {"categories": [], "tags": []}
                order.append(code)
            by_code[code]["categories"].append(category_name)
            tags = entry.get("tags", [])
            if not isinstance(tags, list):
                raise ValueError("menu tags must be a list")
            by_code[code]["tags"].extend(normalize_text(tag) for tag in tags)
    return [_build_candidate(code, meals[code], by_code[code]) for code in order]


def detail_requires_drink(payload: dict[str, Any]) -> bool:
    """判断套餐详情是否含必选饮料组。"""
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("meal detail data must be an object")
    rounds = data.get("rounds", [])
    if not isinstance(rounds, list):
        raise ValueError("meal detail rounds must be a list")
    for item in rounds:
        if not isinstance(item, dict):
            raise ValueError("meal detail round must be an object")
        name = normalize_text(item.get("name"))
        if "饮料" in name and positive_int(item.get("minQuantity")):
            return True
    return False


def extract_price_result(payload: dict[str, Any]) -> dict[str, Any]:
    """提取核价金额和本次可用取餐方式。"""
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("price response data must be an object")
    price = data.get("price")
    discount = data.get("discount", 0)
    take_ways = data.get("takeWayList")
    if not non_negative_int(price) or not non_negative_int(discount):
        raise ValueError("price and discount must be non-negative cents")
    if not isinstance(take_ways, list):
        raise ValueError("price response takeWayList must be a list")
    normalized_take_ways: list[dict[str, str]] = []
    for item in take_ways:
        if not isinstance(item, dict):
            raise ValueError("take way must be an object")
        code = normalize_text(item.get("code"))
        title = normalize_text(item.get("title"))
        if not code or not title:
            raise ValueError("take way requires code and title")
        normalized_take_ways.append({"code": code, "title": title})
    return {
        "price_cents": price,
        "discount_cents": discount,
        "take_ways": normalized_take_ways,
    }


def build_default_order_item(payload: dict[str, Any]) -> dict[str, Any]:
    """从餐品详情构造保持默认选择的完整核价商品参数。"""
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("meal detail data must be an object")
    code = normalize_text(data.get("code"))
    if not code:
        raise ValueError("meal detail requires product code")
    item: dict[str, Any] = {"productCode": code, "quantity": 1}
    rounds = data.get("rounds", [])
    if not isinstance(rounds, list):
        raise ValueError("meal detail rounds must be a list")
    if rounds:
        item["roundList"] = _default_rounds(rounds)
    modification = _default_modification(data.get("modification"))
    if modification:
        item["modification"] = modification
    return item


def price_to_cents(value: Any) -> int:
    """将菜单元价格转换为整数分。"""
    try:
        cents = Decimal(str(value)) * 100
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"invalid menu price: {value}") from error
    if cents < 0 or cents != cents.to_integral_value():
        raise ValueError(f"invalid menu price: {value}")
    return int(cents)


def normalize_text(value: Any) -> str:
    """清理 MCP 展示文本中的换行和多余空白。"""
    return " ".join(str(value or "").split())


def _build_candidate(
    code: str,
    detail: dict[str, Any],
    discovered: dict[str, Any],
) -> MenuCandidate:
    """从已校验的菜单索引构造单个候选。"""
    name = normalize_text(detail.get("name"))
    if not name:
        raise ValueError(f"menu product has no name: {code}")
    tags = tuple(dict.fromkeys(filter(None, discovered["tags"])))
    categories = tuple(dict.fromkeys(discovered["categories"]))
    is_combo = "套餐" in tags or "套餐" in name or "餐" in name and "单品" not in tags
    return MenuCandidate(
        code=code,
        name=name,
        price_cents=price_to_cents(detail.get("currentPrice")),
        categories=categories,
        tags=tags,
        is_combo=is_combo,
    )


def _default_rounds(rounds: list[Any]) -> list[dict[str, Any]]:
    """提取每个套餐组的默认选项并校验必选数量。"""
    results: list[dict[str, Any]] = []
    for round_item in rounds:
        if not isinstance(round_item, dict):
            raise ValueError("meal round must be an object")
        choices = round_item.get("choices", [])
        if not isinstance(choices, list):
            raise ValueError("meal round choices must be a list")
        combo_items = _default_choices(choices)
        minimum = round_item.get("minQuantity", 0)
        if not non_negative_int(minimum):
            raise ValueError("meal round minQuantity must be a non-negative integer")
        selected = sum(item["quantity"] for item in combo_items)
        if selected < minimum:
            raise ValueError("mandatory combo round does not meet required quantity")
        if combo_items:
            results.append(
                {
                    "round": str(round_item.get("id") or ""),
                    "comboItemList": combo_items,
                }
            )
    return results


def _default_choices(choices: list[Any]) -> list[dict[str, Any]]:
    """提取套餐组中所有官方默认选项。"""
    results: list[dict[str, Any]] = []
    for choice in choices:
        if not isinstance(choice, dict):
            raise ValueError("meal round choice must be an object")
        if choice.get("isDefault") != 1:
            continue
        code = normalize_text(choice.get("code"))
        quantity = choice.get("quantity", 1)
        if not code or not positive_int(quantity):
            raise ValueError("default combo choice requires code and quantity")
        item: dict[str, Any] = {"code": code, "quantity": quantity}
        modification = _default_modification(choice.get("modification"))
        if modification:
            item["modification"] = modification
        results.append(item)
    return results


def _default_modification(value: Any) -> dict[str, Any] | None:
    """保留已选特调和所有带 unselectedKey 的未选项。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("modification must be an object")
    groups = value.get("items", [])
    if not isinstance(groups, list):
        raise ValueError("modification items must be a list")
    values: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("values", []), list):
            raise ValueError("modification group must contain values")
        values.extend(_modification_values(group.get("values", [])))
    return {"values": values} if values else None


def _modification_values(options: list[Any]) -> list[dict[str, Any]]:
    """归一化一个特调组中的已选与未选键。"""
    values: list[dict[str, Any]] = []
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("modification option must be an object")
        quantity = option.get("selectedQuantity", 0)
        key = option.get("selectedKey") if quantity else option.get("unselectedKey")
        code = normalize_text(option.get("code"))
        if key is None:
            continue
        if not code or not isinstance(quantity, int) or isinstance(quantity, bool):
            raise ValueError("modification option has invalid code or quantity")
        values.append({"code": code, "key": str(key), "quantity": quantity})
    return values


def positive_int(value: Any) -> bool:
    """判断值是否为正整数且排除布尔值。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def non_negative_int(value: Any) -> bool:
    """判断值是否为非负整数且排除布尔值。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
