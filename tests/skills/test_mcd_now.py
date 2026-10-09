# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SCRIPTS = Path(__file__).parents[2] / "mcd-now" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from coupon_service import (  # noqa: E402
    coupon_instance_key,
    find_expiring,
    parse_duration,
)
from models import Preference  # noqa: E402
from preferences import PreferenceStore, find_conflicts, merge_preferences  # noqa: E402
from reminder import new_reminders, threshold_for  # noqa: E402
from state_store import read_json, state_home, write_json  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_preference_store_add_list_remove_and_clear(tmp_path: Path) -> None:
    store = PreferenceStore(tmp_path / "preferences.json")
    added = store.add("favorite", "鸡肉汉堡")

    assert store.list() == [added]
    updated = store.update(added.id, "板烧鸡腿堡", "hard")
    assert updated is not None
    assert updated.value == "板烧鸡腿堡"
    assert updated.strength == "hard"
    assert store.update("missing", "麦乐鸡") is None
    assert store.remove(added.id) is True
    assert store.remove(added.id) is False
    store.add("restriction", "不吃牛肉", confirmed_sensitive=True)
    assert store.clear() == 1
    assert store.list() == []


def test_preference_store_rejects_empty_value(tmp_path: Path) -> None:
    store = PreferenceStore(tmp_path / "preferences.json")

    try:
        store.add("favorite", "  ")
    except ValueError as error:
        assert "cannot be empty" in str(error)
    else:
        raise AssertionError("empty preference should fail")


def test_preference_store_requires_confirmation_for_restriction(
    tmp_path: Path,
) -> None:
    store = PreferenceStore(tmp_path / "preferences.json")

    try:
        store.add("restriction", "花生过敏")
    except ValueError as error:
        assert "confirmation" in str(error)
    else:
        raise AssertionError("sensitive restriction should require confirmation")

    saved = store.add(
        "restriction",
        "花生过敏",
        confirmed_sensitive=True,
    )
    assert saved.strength == "hard"


def test_cli_restriction_requires_confirmation(tmp_path: Path) -> None:
    script = SCRIPTS / "mcd_skill.py"
    environment = os.environ.copy()
    environment["MCD_SKILL_HOME"] = str(tmp_path / "state")
    command = [
        sys.executable,
        str(script),
        "preference",
        "add",
        "--category",
        "restriction",
        "--value",
        "花生过敏",
    ]

    rejected = subprocess.run(
        command,
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
    )
    accepted = subprocess.run(
        [*command, "--confirm-sensitive", "--format", "json"],
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
    )

    assert rejected.returncode == 3
    assert "confirmation" in rejected.stderr
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["data"]["category"] == "restriction"


def test_preference_store_migrates_legacy_list(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    legacy = PreferenceStore(path).add("favorite", "鸡肉汉堡")
    path.write_text(
        json.dumps([legacy.to_dict()], ensure_ascii=False),
        encoding="utf-8",
    )

    assert PreferenceStore(path).list() == [legacy]
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated == {
        "schema_version": 1,
        "preferences": [legacy.to_dict()],
    }


def test_preference_store_rejects_unknown_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text(
        json.dumps({"schema_version": 99, "preferences": []}),
        encoding="utf-8",
    )

    try:
        PreferenceStore(path).list()
    except ValueError as error:
        assert "schema version" in str(error)
    else:
        raise AssertionError("unknown schema version should fail")


def test_preference_store_rejects_invalid_saved_fields(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    invalid = {
        "schema_version": 1,
        "preferences": [
            {
                "id": "pref_invalid",
                "category": "unknown",
                "value": "鸡肉汉堡",
                "strength": "maybe",
                "source": "explicit",
                "created_at": "2026-10-09T12:00:00+08:00",
                "updated_at": "2026-10-09T12:00:00+08:00",
            }
        ],
    }
    path.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")

    try:
        PreferenceStore(path).list()
    except ValueError as error:
        assert "category" in str(error)
    else:
        raise AssertionError("invalid preference should fail")


def test_preference_store_add_is_idempotent(tmp_path: Path) -> None:
    store = PreferenceStore(tmp_path / "preferences.json")

    first = store.add("favorite", "鸡肉汉堡")
    duplicate = store.add("favorite", "  鸡肉汉堡  ")

    assert duplicate == first
    assert store.list() == [first]


def test_preference_store_concurrent_adds_do_not_lose_data(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"

    def add(index: int) -> None:
        PreferenceStore(path).add("favorite", f"餐品-{index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(add, range(20)))

    assert {item.value for item in PreferenceStore(path).list()} == {
        f"餐品-{index}" for index in range(20)
    }


def make_preference(
    preference_id: str,
    category: str,
    value: str,
    strength: str = "soft",
) -> Preference:
    return Preference(
        id=preference_id,
        category=category,
        value=value,
        strength=strength,
        source="explicit",
        created_at="2026-10-09T12:00:00+08:00",
        updated_at="2026-10-09T12:00:00+08:00",
    )


def test_find_conflicts_detects_opposed_categories() -> None:
    preferences = [
        make_preference("favorite", "favorite", "鸡肉汉堡"),
        make_preference("dislike", "dislike", "鸡肉汉堡"),
        make_preference("other", "taste", "少冰"),
    ]

    conflicts = find_conflicts(preferences)

    assert [(item[0].id, item[1].id) for item in conflicts] == [
        ("favorite", "dislike")
    ]


def test_merge_preferences_current_request_overrides_saved_category() -> None:
    saved = [
        make_preference("saved-meal", "meal_structure", "通常点套餐"),
        make_preference("saved-favorite", "favorite", "鸡肉汉堡"),
    ]
    current = [
        make_preference("current-meal", "meal_structure", "这次只要单点"),
    ]

    merged = merge_preferences(saved, current)

    assert [item.id for item in merged] == ["saved-favorite", "current-meal"]


def test_merge_preferences_keeps_saved_hard_restrictions() -> None:
    saved = [make_preference("restriction", "restriction", "花生", "hard")]
    current = [make_preference("favorite", "favorite", "花生", "soft")]

    merged = merge_preferences(saved, current)

    assert [item.id for item in merged] == ["restriction", "favorite"]
    assert find_conflicts(merged) == [(saved[0], current[0])]


def test_coupon_instance_uses_id_and_code() -> None:
    first = coupon_instance_key({"id": "same", "code": "a"})
    second = coupon_instance_key({"id": "same", "code": "b"})

    assert first != second


def test_find_expiring_filters_disabled_and_sorts() -> None:
    payload = {
        "data": {
            "coupons": [
                {
                    "id": "later",
                    "code": "2",
                    "title": "later",
                    "tradeEndDateTime": "2026-10-11 12:00:00",
                    "enable": 1,
                },
                {
                    "id": "first",
                    "code": "1",
                    "title": "first",
                    "tradeEndDateTime": "2026-10-10 12:00:00",
                    "enable": 1,
                },
                {
                    "id": "disabled",
                    "code": "3",
                    "title": "disabled",
                    "tradeEndDateTime": "2026-10-10 10:00:00",
                    "enable": 0,
                },
            ]
        }
    }
    now = datetime(2026, 10, 9, 12, 0, tzinfo=SHANGHAI)

    results = find_expiring(payload, 72, now)

    assert [item.title for item in results] == ["first", "later"]


def test_reminders_are_idempotent(tmp_path: Path) -> None:
    payload = {
        "data": {
            "coupons": [
                {
                    "id": "coupon",
                    "code": "instance",
                    "title": "soon",
                    "tradeEndDateTime": "2026-10-10 11:00:00",
                    "enable": 1,
                }
            ]
        }
    }
    now = datetime(2026, 10, 9, 12, 0, tzinfo=SHANGHAI)
    coupons = find_expiring(payload, 72, now)
    state = tmp_path / "reminder-state.json"

    assert len(new_reminders(coupons, state)) == 1
    assert new_reminders(coupons, state) == []


def test_reminders_support_custom_thresholds(tmp_path: Path) -> None:
    payload = {
        "data": {
            "coupons": [
                {
                    "id": "coupon",
                    "code": "instance",
                    "title": "soon",
                    "tradeEndDateTime": "2026-10-11 11:00:00",
                    "enable": 1,
                }
            ]
        }
    }
    now = datetime(2026, 10, 9, 12, 0, tzinfo=SHANGHAI)
    coupons = find_expiring(payload, 72, now)
    state = tmp_path / "reminder-state.json"

    assert threshold_for(47, (168, 48, 12)) == 48
    assert len(new_reminders(coupons, state, (168, 48, 12))) == 1
    assert new_reminders(coupons, state, (168, 48, 12)) == []


def test_reminder_thresholds_must_be_positive() -> None:
    try:
        threshold_for(12, (24, 0))
    except ValueError as error:
        assert "positive" in str(error)
    else:
        raise AssertionError("non-positive reminder threshold should fail")


def test_parse_duration() -> None:
    assert parse_duration("3d") == 72
    assert parse_duration("12h") == 12


def test_state_store_reads_default_and_round_trips_utf8(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    path = tmp_path / "状态" / "preferences.json"
    monkeypatch.setenv("MCD_SKILL_HOME", str(tmp_path / "自定义状态"))

    assert state_home() == tmp_path / "自定义状态"
    assert read_json(path, {"missing": True}) == {"missing": True}

    write_json(path, {"value": "鸡肉汉堡"})

    assert read_json(path, {}) == {"value": "鸡肉汉堡"}
    assert path.read_bytes().endswith(b"\n")


def test_state_store_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{broken", encoding="utf-8", newline="\n")

    try:
        read_json(path, {})
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("invalid state JSON should fail")
