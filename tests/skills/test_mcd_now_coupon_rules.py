# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SCRIPTS = Path(__file__).parents[2] / "mcd-now" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from coupon_service import (  # noqa: E402
    claim_available_coupons,
    coupon_is_available,
    extract_available_coupon_names,
    fetch_all_coupons,
    match_coupon_instance,
)
from services.store_service import (  # noqa: E402
    estimate_walking_minutes,
    find_redeemable_stores,
    inspect_coupon_redemption,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


class FakeClient:
    """记录分页 MCP 调用的最小假客户端。"""

    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, str]]] = []

    def call_tool(self, name: str, arguments: dict[str, str]) -> dict[str, object]:
        self.calls.append((name, arguments))
        return self.pages.pop(0)


def test_fetch_all_coupons_reads_all_reported_pages() -> None:
    client = FakeClient(
        [
            {
                "data": {
                    "coupons": [{"id": "1"}],
                    "currentPage": 1,
                    "totalPages": 2,
                }
            },
            {
                "data": {
                    "coupons": [{"id": "2"}],
                    "currentPage": 2,
                    "totalPages": 2,
                }
            },
        ]
    )

    payload = fetch_all_coupons(client)

    assert [item["id"] for item in payload["data"]["coupons"]] == ["1", "2"]
    assert client.calls == [
        ("query-my-coupons", {"page": "1", "pageSize": "200"}),
        ("query-my-coupons", {"page": "2", "pageSize": "200"}),
    ]


def test_fetch_all_coupons_rejects_more_than_five_pages() -> None:
    client = FakeClient(
        [
            {
                "data": {
                    "coupons": [],
                    "currentPage": 1,
                    "totalPages": 6,
                }
            }
        ]
    )

    try:
        fetch_all_coupons(client)
    except ValueError as error:
        assert "five pages" in str(error)
    else:
        raise AssertionError("page count beyond MCP limit should fail")


def available_coupon(**overrides: object) -> dict[str, object]:
    coupon: dict[str, object] = {
        "id": "coupon",
        "code": "instance",
        "enable": 1,
        "currentDayAvailableCount": 1,
        "tradeStartDateTime": "2026-10-09 00:00:00",
        "tradeEndDateTime": "2026-10-10 23:59:59",
        "tradeTimePeriods": [{"startTime": "10:30:00", "endTime": "23:59:59"}],
        "orderTypes": [1, 2],
        "beTypes": [1, 2, 5],
    }
    coupon.update(overrides)
    return coupon


def test_coupon_is_available_for_pickup_in_trade_window() -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=SHANGHAI)

    assert coupon_is_available(available_coupon(), now, order_type=1, be_type=1)


def test_coupon_is_unavailable_outside_time_or_fulfillment() -> None:
    morning = datetime(2026, 10, 9, 8, 0, tzinfo=SHANGHAI)
    noon = datetime(2026, 10, 9, 12, 0, tzinfo=SHANGHAI)

    assert not coupon_is_available(available_coupon(), morning, 1, 1)
    assert not coupon_is_available(available_coupon(orderTypes=[2]), noon, 1, 1)
    assert not coupon_is_available(available_coupon(beTypes=[2]), noon, 1, 1)
    assert not coupon_is_available(
        available_coupon(currentDayAvailableCount=0), noon, 1, 1
    )


def test_coupon_without_time_period_is_available_all_day() -> None:
    now = datetime(2026, 10, 9, 8, 0, tzinfo=SHANGHAI)

    assert coupon_is_available(available_coupon(tradeTimePeriods=[]), now, 1, 1)


def test_coupon_respects_weekday_restriction() -> None:
    thursday = datetime(2026, 10, 8, 12, 0, tzinfo=SHANGHAI)
    friday = datetime(2026, 10, 9, 12, 0, tzinfo=SHANGHAI)
    coupon = available_coupon(
        tradeStartDateTime="2026-10-08 00:00:00",
        weekdays=[4],
    )

    assert coupon_is_available(coupon, thursday, 1, 1)
    assert not coupon_is_available(coupon, friday, 1, 1)


def test_match_coupon_instance_requires_id_and_code() -> None:
    target = available_coupon()
    store_coupons = [
        available_coupon(code="other"),
        available_coupon(id="other"),
        target,
    ]

    assert match_coupon_instance(target, store_coupons) == target
    assert match_coupon_instance(target, store_coupons[:2]) is None


def test_coupon_trade_period_supports_cross_midnight() -> None:
    coupon = available_coupon(
        tradeTimePeriods=[{"startTime": "22:00:00", "endTime": "04:59:59"}]
    )
    late = datetime(2026, 10, 9, 23, 0, tzinfo=SHANGHAI)
    early = datetime(2026, 10, 10, 2, 0, tzinfo=SHANGHAI)

    assert coupon_is_available(coupon, late, 1, 1)
    assert coupon_is_available(coupon, early, 1, 1)


def test_extract_available_coupon_names_filters_and_deduplicates() -> None:
    payload = {
        "data": [
            {"couponName": "麦旋风任选", "couponStatus": "CAN_GET"},
            {"couponName": "麦旋风任选", "couponStatus": "CAN_GET"},
            {"couponName": "不可领取", "couponStatus": "SOLD_OUT"},
        ]
    }

    assert extract_available_coupon_names(payload) == ["麦旋风任选"]


def test_claim_available_coupons_requires_explicit_confirmation() -> None:
    client = FakeClient([])

    try:
        claim_available_coupons(client, confirmed=False)
    except PermissionError as error:
        assert "explicit confirmation" in str(error)
    else:
        raise AssertionError("claiming coupons should require confirmation")
    assert client.calls == []


def test_claim_available_coupons_calls_write_tool_after_confirmation() -> None:
    client = FakeClient([{"success": True, "data": {"bound": 3}}])

    result = claim_available_coupons(client, confirmed=True)

    assert result == {"success": True, "data": {"bound": 3}}
    assert client.calls == [("auto-bind-coupons", {})]


class StoreClient:
    """按门店返回可核销券列表的假客户端。"""

    def __init__(self, coupons_by_store: dict[str, list[dict[str, object]]]) -> None:
        self.coupons_by_store = coupons_by_store
        self.calls: list[str] = []

    def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        assert name == "query-store-coupons"
        store_code = str(arguments["storeCode"])
        self.calls.append(store_code)
        return {"data": self.coupons_by_store.get(store_code, [])}


def store(code: str, distance: int, open_now: bool = True) -> dict[str, object]:
    return {
        "storeCode": code,
        "storeName": f"门店-{code}",
        "distance": distance,
        "businessStatus": open_now,
    }


def test_find_redeemable_stores_only_falls_back_after_current_store() -> None:
    target = available_coupon()
    client = StoreClient(
        {
            "current": [],
            "near": [
                {
                    "couponId": target["id"],
                    "couponCode": target["code"],
                }
            ],
        }
    )
    stores = [
        store("far", 900),
        store("closed", 50, False),
        store("near", 160),
        store("current", 300),
    ]

    result = find_redeemable_stores(client, target, stores, "current")

    assert client.calls == ["current", "near", "far"]
    assert result["checked_count"] == 3
    assert result["current_store_supported"] is False
    assert result["fallback"] is True
    assert result["stores"] == [
        {
            "storeCode": "near",
            "storeName": "门店-near",
            "distance": 160,
            "etaMinutes": 2,
        }
    ]


def test_find_redeemable_stores_stops_when_current_store_works() -> None:
    target = available_coupon()
    store_coupon = {"couponId": target["id"], "couponCode": target["code"]}
    client = StoreClient({"current": [store_coupon], "other": [store_coupon]})

    result = find_redeemable_stores(
        client,
        target,
        [store("other", 100), store("current", 200)],
        "current",
    )

    assert client.calls == ["current"]
    assert result["fallback"] is False
    assert result["current_store_supported"] is True


def test_eta_requires_positive_distance_and_speed() -> None:
    assert estimate_walking_minutes(161) == 3
    assert estimate_walking_minutes(0) is None
    try:
        estimate_walking_minutes(100, 0)
    except ValueError as error:
        assert "speed" in str(error)
    else:
        raise AssertionError("walking speed must be positive")


def test_inspect_coupon_redemption_queries_user_location() -> None:
    target = available_coupon()
    store_coupon = {"couponId": target["id"], "couponCode": target["code"]}
    client = StoreClient({"near": [store_coupon]})
    original_call = client.call_tool

    def call_tool(
        name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        if name == "query-nearby-stores":
            assert arguments == {
                "searchType": 2,
                "beType": 1,
                "city": "上海市",
                "keyword": "人民广场",
            }
            return {"data": [store("near", 160)]}
        return original_call(name, arguments)

    client.call_tool = call_tool  # type: ignore[method-assign]

    result = inspect_coupon_redemption(
        client,
        target,
        city="上海市",
        keyword="人民广场",
    )

    assert result["verified"] is True
    assert result["stores"][0]["storeCode"] == "near"


def test_inspect_coupon_redemption_requires_city_and_keyword_together() -> None:
    client = StoreClient({})

    result = inspect_coupon_redemption(
        client,
        available_coupon(),
        city=None,
        keyword=None,
    )
    assert result == {
        "verified": False,
        "reason": "location_not_provided",
        "stores": [],
    }

    try:
        inspect_coupon_redemption(
            client,
            available_coupon(),
            city="上海市",
            keyword=None,
        )
    except ValueError as error:
        assert "city and keyword" in str(error)
    else:
        raise AssertionError("partial location should fail")
