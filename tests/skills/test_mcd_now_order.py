# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).parents[2] / "mcd-now" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from order_guard import (  # noqa: E402
    build_confirmation_card,
    is_confirmation_valid,
    order_fingerprint,
    validate_order_draft,
)
from services.order_service import (  # noqa: E402
    OrderCreationUnknownError,
    query_order_status,
    submit_confirmed_pickup,
)


def pickup_draft() -> dict[str, object]:
    return {
        "storeCode": "1990262",
        "storeName": "测试门店",
        "orderType": 1,
        "beType": 1,
        "takeWayCode": "locker-in",
        "takeWayTitle": "到店自取",
        "items": [
            {
                "productCode": "1406",
                "productName": "板烧鸡腿堡",
                "quantity": 1,
                "modification": {
                    "values": [{"code": "sauce", "key": "0-0", "quantity": 0}]
                },
                "couponId": "coupon-id",
                "couponCode": "sensitive-coupon-code",
                "couponName": "板烧鸡腿堡优惠券",
            }
        ],
        "price": 2400,
    }


def test_order_draft_accepts_confirmable_pickup_order() -> None:
    draft = pickup_draft()

    validate_order_draft(draft)

    assert len(order_fingerprint(draft)) == 12


def test_order_draft_requires_take_way_for_pickup() -> None:
    draft = pickup_draft()
    draft.pop("takeWayCode")

    try:
        validate_order_draft(draft)
    except ValueError as error:
        assert "takeWayCode" in str(error)
    else:
        raise AssertionError("pickup order without takeWayCode should fail")


def test_order_fingerprint_changes_when_price_changes() -> None:
    original = pickup_draft()
    repriced = pickup_draft()
    repriced["price"] = 2500

    assert order_fingerprint(original) != order_fingerprint(repriced)


def test_order_draft_requires_coupon_pair() -> None:
    draft = pickup_draft()
    draft["items"] = [
        {"productCode": "1406", "quantity": 1, "couponId": "coupon"}
    ]

    try:
        validate_order_draft(draft)
    except ValueError as error:
        assert "provided together" in str(error)
    else:
        raise AssertionError("partial coupon identity should fail")


def test_order_draft_rejects_mismatched_order_and_business_type() -> None:
    draft = pickup_draft()
    draft["beType"] = 2

    try:
        validate_order_draft(draft)
    except ValueError as error:
        assert "pickup only" in str(error)
    else:
        raise AssertionError("pickup order cannot use delivery business type")


def test_order_draft_rejects_drive_through_even_with_be_code() -> None:
    draft = pickup_draft()
    draft["beType"] = 5
    draft["beCode"] = "drive-through-code"

    try:
        validate_order_draft(draft)
    except ValueError as error:
        assert "pickup only" in str(error)
    else:
        raise AssertionError("drive-through order should be outside the first release")


def test_order_draft_rejects_delivery_fields() -> None:
    draft = pickup_draft()
    draft["addressId"] = "address"

    try:
        validate_order_draft(draft)
    except ValueError as error:
        assert "must not contain" in str(error)
    else:
        raise AssertionError("pickup draft must reject delivery fields")


def test_confirmation_card_contains_bound_order_summary() -> None:
    card = build_confirmation_card(pickup_draft())

    assert card["store"] == {"code": "1990262", "name": "测试门店"}
    assert card["items"][0]["name"] == "板烧鸡腿堡"
    assert card["items"][0]["modification"]
    assert card["items"][0]["coupon_name"] == "板烧鸡腿堡优惠券"
    assert "sensitive-coupon-code" not in str(card)
    assert card["price_cents"] == 2400
    assert card["take_way"] == {"code": "locker-in", "title": "到店自取"}
    assert len(card["fingerprint"]) == 12


def test_confirmation_requires_exact_phrase_and_current_fingerprint() -> None:
    fingerprint = order_fingerprint(pickup_draft())

    assert is_confirmation_valid(f"确认下单 {fingerprint}", fingerprint) is True
    assert is_confirmation_valid("可以", fingerprint) is False
    assert is_confirmation_valid("确认下单 stale-draft", fingerprint) is False


class OrderClient:
    """记录订单流程的 MCP 调用并返回固定结果。"""

    def __init__(
        self,
        repriced: int = 2400,
        create_error: bool = False,
        failed_order_id: str | None = None,
    ) -> None:
        self.repriced = repriced
        self.create_error = create_error
        self.failed_order_id = failed_order_id
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((name, arguments))
        if name == "calculate-price":
            return {
                "data": {
                    "price": self.repriced,
                    "discount": 500,
                    "takeWayList": [
                        {"code": "locker-in", "title": "到店自取"}
                    ],
                }
            }
        if name == "create-order":
            if self.create_error:
                error = TimeoutError("upstream timeout")
                error.order_id = self.failed_order_id
                raise error
            return {"data": {"orderId": "order-1", "paymentUrl": "https://pay"}}
        if name == "query-order":
            return {"data": {"orderId": arguments["orderId"], "status": "PAID"}}
        raise AssertionError(f"unexpected tool: {name}")


def test_submit_confirmed_pickup_reprices_then_creates_order() -> None:
    draft = pickup_draft()
    fingerprint = order_fingerprint(draft)
    client = OrderClient()

    result = submit_confirmed_pickup(
        client,
        draft,
        confirmation=f"确认下单 {fingerprint}",
    )

    assert result["state"] == "pending_payment"
    assert result["order_id"] == "order-1"
    assert [name for name, _ in client.calls] == ["calculate-price", "create-order"]
    create_payload = client.calls[1][1]
    assert "storeName" not in create_payload
    assert "productName" not in str(create_payload)
    assert "beCode" not in create_payload


def test_submit_confirmed_pickup_requires_reconfirmation_after_price_change() -> None:
    draft = pickup_draft()
    client = OrderClient(repriced=2500)

    result = submit_confirmed_pickup(
        client,
        draft,
        confirmation=f"确认下单 {order_fingerprint(draft)}",
    )

    assert result["state"] == "reconfirmation_required"
    assert result["changes"] == {"price_cents": {"before": 2400, "after": 2500}}
    assert [name for name, _ in client.calls] == ["calculate-price"]


def test_submit_confirmed_pickup_does_not_retry_unknown_creation() -> None:
    draft = pickup_draft()
    client = OrderClient(create_error=True)

    try:
        submit_confirmed_pickup(
            client,
            draft,
            confirmation=f"确认下单 {order_fingerprint(draft)}",
        )
    except OrderCreationUnknownError as error:
        assert error.order_id is None
    else:
        raise AssertionError("unknown create result should stop the flow")

    assert [name for name, _ in client.calls] == ["calculate-price", "create-order"]


def test_submit_confirmed_pickup_queries_known_order_after_create_error() -> None:
    draft = pickup_draft()
    client = OrderClient(create_error=True, failed_order_id="order-uncertain")

    result = submit_confirmed_pickup(
        client,
        draft,
        confirmation=f"确认下单 {order_fingerprint(draft)}",
    )

    assert result["state"] == "creation_status_checked"
    assert result["order_id"] == "order-uncertain"
    assert result["order"]["data"]["status"] == "PAID"
    assert [name for name, _ in client.calls] == [
        "calculate-price",
        "create-order",
        "query-order",
    ]


def test_query_order_status_uses_official_order_result() -> None:
    client = OrderClient()

    result = query_order_status(client, "order-1")

    assert result["data"]["status"] == "PAID"
    assert client.calls == [("query-order", {"orderId": "order-1"})]


def test_query_order_status_requires_order_id() -> None:
    client = OrderClient()

    try:
        query_order_status(client, "  ")
    except ValueError as error:
        assert "order id" in str(error)
    else:
        raise AssertionError("blank order id should fail")

    assert client.calls == []
