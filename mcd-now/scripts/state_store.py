# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


LOCK_TIMEOUT_SECONDS = 5.0
STALE_LOCK_SECONDS = 30.0


def state_home() -> Path:
    """返回用户状态目录。

    Returns:
        环境变量指定的目录，或用户主目录下的默认目录。
    """
    configured = os.environ.get("MCD_SKILL_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".mcd-now"


def read_json(path: Path, default: Any) -> Any:
    """读取 UTF-8 JSON 文件。

    Args:
        path: JSON 文件路径。
        default: 文件不存在时返回的默认值。

    Returns:
        解析后的 JSON 值。
    """
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    """使用同目录临时文件原子写入 UTF-8 JSON。

    Args:
        path: 目标文件路径。
        value: 可 JSON 序列化的值。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


@contextmanager
def file_lock(path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """为状态文件获取跨进程排他锁。

    Args:
        path: 需要保护的状态文件。
        timeout: 最长等待秒数。

    Yields:
        获取锁后的执行上下文。

    Raises:
        TimeoutError: 在等待时间内无法获取锁。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
        except (FileExistsError, PermissionError):
            pass
        if descriptor is None:
            if _is_stale(lock_path):
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for state lock: {path}")
            time.sleep(0.01)
    try:
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def _is_stale(lock_path: Path) -> bool:
    """判断锁文件是否已经过期。"""
    try:
        return time.time() - lock_path.stat().st_mtime > STALE_LOCK_SECONDS
    except FileNotFoundError:
        return False
