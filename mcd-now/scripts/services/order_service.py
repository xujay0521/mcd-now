# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Protocol

from order_guard import is_confirmation_valid, order_fingerprint, validate_order_draft
from services.menu_parser import extract_price_result


class ToolClient(Protocol):
    """订单流程所需 MCP 客户端的结构化约定。"""

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP 工具并返回 JSON 对象。"""
        ...


class OrderCreationUnknownError(RuntimeError):
    """表示创建请求已发出但无法确认是否成功。"""

    def __init__(self, message: str, order_id: str | None = None) -> None:
        super().__init__(message)
        self.order_id = order_id


def submit_confirmed_pickup(
    client: ToolClient,
    draft: dict[str, Any],
    confirmation: str,
) -> dict[str, Any]:
    """二次核价后提交已精确确认的到店自取订单。

    Args:
        client: 交互式宿主提供的 MCP 客户端。
        draft: 已展示给用户的订单草稿。
        confirmation: 用户包含草稿指纹的确认口令。

    Returns:
        待支付结果，或要求用户重新确认的差异。

    Raises:
        ValueError: 草稿、确认口令或上游响应不合法。
        OrderCreationUnknownError: 创建结果未知，调用方不得直接重试。
    """
    validate_order_draft(draft)
    fingerprint = order_fingerprint(draft)
    if not is_confirmation_valid(confirmation, fingerprint):
        raise ValueError("confirmation does not match the current order draft")

    business_items = [_business_item(item) for item in draft["items"]]
    price_payload = _price_payload(draft, business_items)
    repriced = extract_price_result(client.call_tool("calculate-price", price_payload))
    changes = _reprice_changes(draft, repriced)
    if changes:
        return {"state": "reconfirmation_required", "changes": changes}

    create_payload = dict(price_payload)
    create_payload["takeWayCode"] = draft["takeWayCode"]
    try:
        response = client.call_tool("create-order", create_payload)
    except Exception as error:
        order_id = _exception_order_id(error)
        if order_id:
            try:
                order = query_order_status(client, order_id)
            except Exception as query_error:
                raise OrderCreationUnknownError(
                    "order creation and status query results are unknown",
                    order_id,
                ) from query_error
            return {
                "state": "creation_status_checked",
                "order_id": order_id,
                "order": order,
            }
        raise OrderCreationUnknownError(
            "order creation result is unknown; do not retry automatically"
        ) from error
    return _created_order(response)


def query_order_status(client: ToolClient, order_id: str) -> dict[str, Any]:
    """使用官方订单查询结果核实支付及履约状态。"""
    normalized = order_id.strip()
    if not normalized:
        raise ValueError("order id is required")
    return client.call_tool("query-order", {"orderId": normalized})


def _price_payload(
    draft: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造首版到店自取的核价参数。"""
    payload: dict[str, Any] = {
        "storeCode": draft["storeCode"],
        "orderType": 1,
        "beType": 1,
        "items": items,
    }
    for key in ("reservationDate", "withOrder", "needTableware"):
        if key in draft:
            payload[key] = draft[key]
    return payload


def _business_item(item: dict[str, Any]) -> dict[str, Any]:
    """去掉确认卡展示字段，只保留 MCP 接受的商品参数。"""
    allowed = (
        "productCode",
        "quantity",
        "roundList",
        "couponId",
        "couponCode",
        "modification",
    )
    return {key: item[key] for key in allowed if key in item}


def _reprice_changes(
    draft: dict[str, Any],
    repriced: dict[str, Any],
) -> dict[str, Any]:
    """比较价格及取餐方式是否仍与用户确认内容一致。"""
    changes: dict[str, Any] = {}
    if repriced["price_cents"] != draft["price"]:
        changes["price_cents"] = {
            "before": draft["price"],
            "after": repriced["price_cents"],
        }
    valid_take_ways = {item["code"] for item in repriced["take_ways"]}
    if draft["takeWayCode"] not in valid_take_ways:
        changes["take_way"] = {
            "before": draft["takeWayCode"],
            "available": sorted(valid_take_ways),
        }
    return changes


def _created_order(payload: dict[str, Any]) -> dict[str, Any]:
    """归一化订单创建响应，不将创建成功误报为已支付。"""
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise OrderCreationUnknownError("order creation response is invalid")
    order_id = str(data.get("orderId", "")).strip()
    if not order_id:
        raise OrderCreationUnknownError("order creation response has no order id")
    return {
        "state": "pending_payment",
        "order_id": order_id,
        "payment_url": data.get("paymentUrl"),
        "next": "请通过支付链接或麦当劳 APP 支付，随后回复支付完成或支付失败。",
    }


def _exception_order_id(error: Exception) -> str | None:
    """从上游异常中读取可用于状态查询的订单号。"""
    value = getattr(error, "order_id", None)
    normalized = str(value or "").strip()
    return normalized or None
