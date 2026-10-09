# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


VALID_CATEGORIES = {
    "restriction",
    "favorite",
    "dislike",
    "taste",
    "meal_structure",
    "budget",
    "fulfillment",
}
VALID_STRENGTHS = {"hard", "soft"}


@dataclass(frozen=True)
class Preference:
    """用户明确保存的一项长期点餐偏好。"""

    id: str
    category: str
    value: str
    strength: str
    source: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Preference":
        """从持久化字典创建并校验偏好。

        Args:
            value: 偏好字段字典。

        Returns:
            校验后的偏好对象。

        Raises:
            ValueError: 字段缺失或字段值非法。
        """
        if not isinstance(value, dict):
            raise ValueError("preference must be an object")
        required = set(cls.__dataclass_fields__)
        if set(value) != required:
            raise ValueError("preference fields are invalid")
        category = value.get("category")
        strength = value.get("strength")
        if category not in VALID_CATEGORIES:
            raise ValueError(f"unsupported category: {category}")
        if strength not in VALID_STRENGTHS:
            raise ValueError(f"unsupported strength: {strength}")
        for field in required:
            if not isinstance(value[field], str) or not value[field].strip():
                raise ValueError(f"preference {field} must be a non-empty string")
        for field in ("created_at", "updated_at"):
            try:
                datetime.fromisoformat(value[field])
            except ValueError as error:
                raise ValueError(f"preference {field} must be ISO-8601") from error
        return cls(**value)


@dataclass(frozen=True)
class ExpiringCoupon:
    """一张处于提醒时间范围内的券实例。"""

    instance_key: str
    title: str
    expires_at: datetime
    hours_left: float
    enabled: bool

    def to_dict(self) -> dict[str, Any]:
        """返回不包含真实券码的输出字典。"""
        return {
            "instance_key": self.instance_key,
            "title": self.title,
            "expires_at": self.expires_at.isoformat(),
            "hours_left": round(self.hours_left, 2),
            "enabled": self.enabled,
        }
