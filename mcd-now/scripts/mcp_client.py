# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROTOCOL_VERSION = "2025-03-26"
READ_ONLY_TOOLS = frozenset(
    {
        "query-my-coupons",
        "available-coupons",
        "query-nearby-stores",
        "query-store-coupons",
        "now-time-info",
    }
)


class McpError(RuntimeError):
    """MCP 客户端基础异常。"""


class McpConfigError(McpError):
    """MCP 配置或工具权限异常。"""


class McpAuthError(McpError):
    """MCP 鉴权失败。"""


class McpRateLimitError(McpError):
    """MCP 请求触发限流。"""


class McpUpstreamError(McpError):
    """MCP 上游或协议响应异常。"""


class McpClient:
    """只允许查询工具的最小 Streamable HTTP MCP 客户端。"""

    def __init__(self, url: str, token: str, timeout: float = 10.0) -> None:
        """初始化客户端。

        Args:
            url: MCP Streamable HTTP 地址。
            token: Bearer Token。
            timeout: 单次请求超时秒数。

        Raises:
            McpConfigError: 地址、Token 或超时配置非法。
        """
        if not url.strip() or not token.strip():
            raise McpConfigError("MCP URL and token are required")
        if timeout <= 0:
            raise McpConfigError("MCP timeout must be positive")
        self.url = url.strip()
        self.token = token.strip()
        self.timeout = timeout
        self.session_id: str | None = None
        self._request_id = 0
        self._initialized = False

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """发送一条带 ID 的 JSON-RPC 请求。

        Args:
            method: JSON-RPC 方法名。
            params: 可选参数对象。

        Returns:
            JSON-RPC `result` 字段。

        Raises:
            McpUpstreamError: 服务返回协议错误或无效响应。
        """
        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        response = self._post(payload)
        if not isinstance(response, dict):
            raise McpUpstreamError("MCP response must be an object")
        if "error" in response:
            error = response["error"]
            raise McpUpstreamError(f"MCP JSON-RPC error: {error}")
        if response.get("id") != self._request_id or "result" not in response:
            raise McpUpstreamError("MCP response has invalid request id or result")
        return response["result"]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """初始化会话并调用一个允许的只读工具。

        Args:
            name: MCP 官方工具名。
            arguments: 工具参数。

        Returns:
            工具返回的 JSON 对象。

        Raises:
            McpConfigError: 工具不在只读白名单内。
            McpUpstreamError: 工具返回错误或非 JSON 对象。
        """
        if name not in READ_ONLY_TOOLS:
            raise McpConfigError(f"MCP tool is not allowed in CLI mode: {name}")
        self._initialize()
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if not isinstance(result, dict):
            raise McpUpstreamError("MCP tool result must be an object")
        if result.get("isError"):
            raise McpUpstreamError("MCP tool returned an error")
        value = self._tool_value(result)
        if not isinstance(value, dict):
            raise McpUpstreamError("MCP tool payload must be a JSON object")
        return value

    def _initialize(self) -> None:
        """按 MCP 协议初始化当前会话一次。"""
        if self._initialized:
            return
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcd-now-cli", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized")
        self._initialized = True

    def _notify(self, method: str) -> None:
        """发送不要求响应的 JSON-RPC 通知。"""
        self._post({"jsonrpc": "2.0", "method": method}, parse=False)

    def _post(self, payload: dict[str, Any], parse: bool = True) -> Any:
        """发送一次 HTTP POST 并按 Content-Type 解析响应。"""
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                body = response.read()
                if not parse:
                    return None
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as error:
            self._raise_http_error(error)
        except (URLError, TimeoutError, socket.timeout) as error:
            reason = getattr(error, "reason", error)
            raise McpUpstreamError(f"MCP request failed: {reason}") from error
        if "text/event-stream" in content_type:
            return self._parse_sse(body)
        return self._parse_json(body)

    @staticmethod
    def _raise_http_error(error: HTTPError) -> None:
        """将 HTTP 状态映射为稳定的 MCP 异常。"""
        if error.code == 401:
            raise McpAuthError("MCP authentication failed") from error
        if error.code == 429:
            raise McpRateLimitError("MCP rate limit exceeded") from error
        raise McpUpstreamError(f"MCP HTTP error: {error.code}") from error

    @staticmethod
    def _parse_json(body: bytes) -> Any:
        """解析 UTF-8 JSON 响应。"""
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise McpUpstreamError("MCP returned invalid JSON") from error

    @classmethod
    def _parse_sse(cls, body: bytes) -> Any:
        """提取 Streamable HTTP SSE 中最后一条 JSON data。"""
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise McpUpstreamError("MCP returned invalid UTF-8 SSE") from error
        data_lines = [
            line[5:].strip()
            for line in text.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            raise McpUpstreamError("MCP SSE response did not contain data")
        return cls._parse_json(data_lines[-1].encode("utf-8"))

    @classmethod
    def _tool_value(cls, result: dict[str, Any]) -> Any:
        """从 MCP 工具结果提取结构化或文本 JSON 值。"""
        if "structuredContent" in result:
            return result["structuredContent"]
        content = result.get("content")
        if not isinstance(content, list):
            raise McpUpstreamError("MCP tool result does not contain content")
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    return cls._parse_json(text.encode("utf-8"))
        raise McpUpstreamError("MCP tool result does not contain JSON text")
