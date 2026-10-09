# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from coupon_service import find_expiring, parse_duration
from models import VALID_CATEGORIES
from preferences import PreferenceStore
from reminder import new_reminders
from state_store import state_home


SHANGHAI = ZoneInfo("Asia/Shanghai")


def configure_console() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def emit(data: Any, output_format: str = "text") -> None:
    if output_format == "json":
        envelope = {
            "code": 200,
            "message": "success",
            "data": data,
            "timestamp": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        }
        print(json.dumps(envelope, ensure_ascii=False))
        return
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def load_payload(path: str) -> dict[str, Any]:
    """从 UTF-8 JSON 文件读取测试或回放载荷。"""
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    return value


def load_coupon_payload(path: str | None) -> dict[str, Any]:
    """从用户主动导出的脱敏快照读取券包。

    Args:
        path: 脱敏 JSON 文件。

    Returns:
        `query-my-coupons` 返回的 JSON 对象。

    Raises:
        OSError: 文件读取失败。
        ValueError: 未提供快照或文件内容不是 JSON 对象。
    """
    if not path:
        raise ValueError("a user-provided coupon snapshot is required")
    return load_payload(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcd-now")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="check local configuration")

    preference = subparsers.add_parser("preference", help="manage preferences")
    preference_sub = preference.add_subparsers(dest="action", required=True)
    add = preference_sub.add_parser("add")
    add.add_argument("--category", choices=sorted(VALID_CATEGORIES), required=True)
    add.add_argument("--value", required=True)
    add.add_argument("--strength", choices=["hard", "soft"])
    add.add_argument("--confirm-sensitive", action="store_true")
    add.add_argument("--format", choices=["text", "json"], default="text")
    listing = preference_sub.add_parser("list")
    listing.add_argument("--format", choices=["text", "json"], default="text")
    update = preference_sub.add_parser("update")
    update.add_argument("--id", required=True)
    update.add_argument("--value")
    update.add_argument("--strength", choices=["hard", "soft"])
    update.add_argument("--format", choices=["text", "json"], default="text")
    remove = preference_sub.add_parser("remove")
    remove.add_argument("--id", required=True)
    remove.add_argument("--format", choices=["text", "json"], default="text")
    clear = preference_sub.add_parser("clear")
    clear.add_argument("--yes", action="store_true")
    clear.add_argument("--format", choices=["text", "json"], default="text")

    for command in ("coupon-check", "remind"):
        check = subparsers.add_parser(command)
        check.add_argument("--input", required=True)
        check.add_argument("--expires-within", default="3d")
        check.add_argument("--format", choices=["text", "json"], default="text")
        if command == "remind":
            check.add_argument(
                "--remind-at",
                action="append",
                default=None,
                metavar="DURATION",
            )
    return parser


def run(args: argparse.Namespace) -> int:
    home = state_home()
    preferences = PreferenceStore(home / "preferences.json")
    if args.command == "doctor":
        emit(
            {
                "state_home": str(home),
                "coupon_source": "user-provided-local-snapshot",
                "automatic_mcp_access": False,
            }
        )
        return 0
    if args.command == "preference":
        if args.action == "add":
            item = preferences.add(
                args.category,
                args.value,
                args.strength,
                args.confirm_sensitive,
            )
            emit(item.to_dict(), args.format)
        elif args.action == "list":
            emit([item.to_dict() for item in preferences.list()], args.format)
        elif args.action == "update":
            if args.value is None and args.strength is None:
                raise ValueError("update requires --value or --strength")
            item = preferences.update(args.id, args.value, args.strength)
            emit({"updated": item.to_dict() if item else None}, args.format)
        elif args.action == "remove":
            emit({"removed": preferences.remove(args.id)}, args.format)
        elif args.action == "clear":
            if not args.yes:
                raise ValueError("clear requires --yes")
            emit({"cleared": preferences.clear()}, args.format)
        return 0
    payload = load_coupon_payload(args.input)
    coupons = find_expiring(payload, parse_duration(args.expires_within))
    if args.command == "remind":
        thresholds = tuple(
            parse_duration(value) for value in (args.remind_at or ("3d", "1d"))
        )
        coupons = new_reminders(
            coupons,
            home / "reminder-state.json",
            thresholds,
        )
        if not coupons:
            return 0
    emit([coupon.to_dict() for coupon in coupons], args.format)
    return 0


def main() -> int:
    configure_console()
    try:
        return run(build_parser().parse_args())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
