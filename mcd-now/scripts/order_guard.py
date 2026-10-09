from __future__ import annotations

import hashlib
import json
from typing import Any


CONFIRMATION_PREFIX = "确认下单"


def validate_order_draft(draft: dict[str, Any]) -> None:
    """校验首版到店自取订单的确认绑定字段。"""
    if not str(draft.get("storeCode", "")).strip():
        raise ValueError("order draft requires storeCode")
    order_type = draft.get("orderType")
    be_type = draft.get("beType")
    if order_type != 1 or be_type != 1:
        raise ValueError("first release supports in-store pickup only")
    forbidden = ("beCode", "addressId", "gmServiceCode", "remark")
    if any(key in draft for key in forbidden):
        raise ValueError("pickup order must not contain delivery or group meal fields")
    if not str(draft.get("takeWayCode", "")).strip():
        raise ValueError("pickup order requires takeWayCode from calculate-price")
    items = draft.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("order draft requires at least one item")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("order item must be an object")
        if not str(item.get("productCode", "")).strip():
            raise ValueError("order item requires productCode")
        quantity = item.get("quantity")
        if (
            not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity < 1
        ):
            raise ValueError("order item quantity must be a positive integer")
        coupon_id = item.get("couponId")
        coupon_code = item.get("couponCode")
        if bool(coupon_id) != bool(coupon_code):
            raise ValueError("couponId and couponCode must be provided together")
    price = draft.get("price")
    if not isinstance(price, int) or isinstance(price, bool) or price < 0:
        raise ValueError("order draft price must be a non-negative integer in cents")


def order_fingerprint(draft: dict[str, Any]) -> str:
    """Return a stable short fingerprint for the exact confirmed order draft."""
    validate_order_draft(draft)
    bound_fields = {
        key: draft.get(key)
        for key in (
            "storeCode",
            "storeName",
            "orderType",
            "beType",
            "beCode",
            "addressId",
            "takeWayCode",
            "takeWayTitle",
            "gmServiceCode",
            "reservationDate",
            "items",
            "withOrder",
            "needTableware",
            "remark",
            "price",
        )
    }
    canonical = json.dumps(
        bound_fields,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:12]


def build_confirmation_card(draft: dict[str, Any]) -> dict[str, Any]:
    """生成供用户核对且与订单指纹绑定的确认卡。"""
    fingerprint = order_fingerprint(draft)
    items = draft["items"]
    return {
        "fingerprint": fingerprint,
        "confirmation_phrase": f"{CONFIRMATION_PREFIX} {fingerprint}",
        "store": {
            "code": draft["storeCode"],
            "name": draft.get("storeName"),
        },
        "items": [
            {
                "code": item["productCode"],
                "name": item.get("productName"),
                "quantity": item["quantity"],
                "rounds": item.get("roundList", []),
                "modification": item.get("modification"),
                "coupon_id": item.get("couponId"),
                "coupon_name": item.get("couponName"),
            }
            for item in items
        ],
        "price_cents": draft["price"],
        "take_way": {
            "code": draft["takeWayCode"],
            "title": draft.get("takeWayTitle"),
        },
        "reservation_date": draft.get("reservationDate"),
    }


def is_confirmation_valid(message: str, fingerprint: str) -> bool:
    """仅接受包含当前草稿短指纹的精确确认口令。"""
    return message.strip() == f"{CONFIRMATION_PREFIX} {fingerprint}"
