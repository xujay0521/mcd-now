# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from models import Preference, VALID_CATEGORIES, VALID_STRENGTHS
from state_store import file_lock, read_json, write_json


SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEMA_VERSION = 1
OPPOSED_CATEGORIES = {
    frozenset(("favorite", "dislike")),
    frozenset(("favorite", "restriction")),
}


def find_conflicts(
    preferences: list[Preference],
) -> list[tuple[Preference, Preference]]:
    """查找内容相同但语义相反的偏好。

    Args:
        preferences: 已合并的偏好列表。

    Returns:
        按输入顺序排列的冲突偏好对。
    """
    conflicts: list[tuple[Preference, Preference]] = []
    for index, first in enumerate(preferences):
        for second in preferences[index + 1 :]:
            categories = frozenset((first.category, second.category))
            first_value = first.value.strip().casefold()
            second_value = second.value.strip().casefold()
            same_value = first_value == second_value
            if same_value and categories in OPPOSED_CATEGORIES:
                conflicts.append((first, second))
    return conflicts


def merge_preferences(
    saved: list[Preference], current: list[Preference]
) -> list[Preference]:
    """合并长期偏好和本次要求。

    本次要求替换同类别长期软偏好，已保存的硬性限制始终保留。

    Args:
        saved: 已保存的长期偏好。
        current: 仅在本次请求生效的偏好。

    Returns:
        可用于本次推荐的有序偏好列表。
    """
    current_categories = {item.category for item in current}
    retained = [
        item
        for item in saved
        if item.category == "restriction" or item.category not in current_categories
    ]
    return retained + current


class PreferenceStore:
    """使用版本化 JSON 文件管理长期点餐偏好。"""

    def __init__(self, path: Path) -> None:
        """初始化偏好存储。

        Args:
            path: 偏好 JSON 文件路径。
        """
        self.path = path

    def list(self) -> list[Preference]:
        """读取偏好列表，并自动迁移旧版列表结构。"""
        with file_lock(self.path):
            return self._load(migrate=True)

    def add(
        self,
        category: str,
        value: str,
        strength: str | None = None,
        confirmed_sensitive: bool = False,
    ) -> Preference:
        """新增偏好；相同类别和值重复添加时返回已有项。"""
        if category not in VALID_CATEGORIES:
            raise ValueError(f"unsupported category: {category}")
        if strength is not None and strength not in VALID_STRENGTHS:
            raise ValueError(f"unsupported strength: {strength}")
        if category == "restriction" and not confirmed_sensitive:
            raise ValueError("restriction requires explicit confirmation")
        normalized = value.strip()
        if not normalized:
            raise ValueError("preference value cannot be empty")
        with file_lock(self.path):
            values = self._load()
            for item in values:
                same_category = item.category == category
                same_value = item.value.casefold() == normalized.casefold()
                if same_category and same_value:
                    return item
            now = datetime.now(SHANGHAI).isoformat(timespec="seconds")
            preference = Preference(
                id=f"pref_{uuid4().hex[:12]}",
                category=category,
                value=normalized,
                strength=strength
                or ("hard" if category == "restriction" else "soft"),
                source="explicit",
                created_at=now,
                updated_at=now,
            )
            values.append(preference)
            self._write(values)
            return preference

    def remove(self, preference_id: str) -> bool:
        """按 ID 删除一项偏好。"""
        with file_lock(self.path):
            values = self._load()
            retained = [item for item in values if item.id != preference_id]
            if len(retained) == len(values):
                return False
            self._write(retained)
            return True

    def update(
        self,
        preference_id: str,
        value: str | None = None,
        strength: str | None = None,
    ) -> Preference | None:
        """更新偏好内容或强度，ID 不存在时返回空。"""
        normalized = value.strip() if value is not None else None
        if normalized == "":
            raise ValueError("preference value cannot be empty")
        if strength is not None and strength not in VALID_STRENGTHS:
            raise ValueError(f"unsupported strength: {strength}")
        with file_lock(self.path):
            values = self._load()
            updated: Preference | None = None
            now = datetime.now(SHANGHAI).isoformat(timespec="seconds")
            replacements: list[Preference] = []
            for item in values:
                if item.id != preference_id:
                    replacements.append(item)
                    continue
                updated = Preference(
                    id=item.id,
                    category=item.category,
                    value=normalized if normalized is not None else item.value,
                    strength=strength or item.strength,
                    source=item.source,
                    created_at=item.created_at,
                    updated_at=now,
                )
                replacements.append(updated)
            if updated is not None:
                self._write(replacements)
            return updated

    def clear(self) -> int:
        """清空全部偏好并返回删除数量。"""
        with file_lock(self.path):
            count = len(self._load())
            self._write([])
            return count

    def _load(self, migrate: bool = False) -> list[Preference]:
        """读取并校验当前或旧版偏好数据。"""
        payload = read_json(self.path, self._envelope([]))
        if isinstance(payload, list):
            values = [Preference.from_dict(value) for value in payload]
            if migrate:
                self._write(values)
            return values
        if not isinstance(payload, dict):
            raise ValueError("preference state must be an object")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported preference schema version")
        raw_preferences = payload.get("preferences")
        if not isinstance(raw_preferences, list):
            raise ValueError("preferences must be a list")
        return [Preference.from_dict(value) for value in raw_preferences]

    def _write(self, values: list[Preference]) -> None:
        """写入当前版本的偏好数据。"""
        write_json(self.path, self._envelope(values))

    @staticmethod
    def _envelope(values: list[Preference]) -> dict[str, object]:
        """构造版本化持久化结构。"""
        return {
            "schema_version": SCHEMA_VERSION,
            "preferences": [item.to_dict() for item in values],
        }
