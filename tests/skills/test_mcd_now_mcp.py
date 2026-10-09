# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError, URLError


SCRIPTS = Path(__file__).parents[2] / "mcd-now" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from mcp_client import (  # noqa: E402
    McpAuthError,
    McpClient,
    McpConfigError,
    McpRateLimitError,
    McpUpstreamError,
)
from mcd_skill import build_parser, load_coupon_payload, main  # noqa: E402


class FakeResponse:
    """模拟 urllib 响应。"""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
        session_id: str | None = None,
    ) -> None:
        self.body = body or json.dumps(payload or {}).encode("utf-8")
        self.headers = {
            "Content-Type": content_type,
        }
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def result(request_id: int, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def test_client_initializes_session_and_calls_allowed_tool() -> None:
    responses = [
        FakeResponse(result(1, {"protocolVersion": "2025-03-26"}), session_id="s1"),
        FakeResponse(body=b"", content_type="application/json"),
        FakeResponse(
            result(
                2,
                {
                    "content": [
                        {"type": "text", "text": '{"success":true,"data":{}}'}
                    ]
                },
            )
        ),
    ]
    requests = []

    def open_request(request: object, timeout: float) -> FakeResponse:
        requests.append((request, timeout))
        return responses.pop(0)

    with patch("mcp_client.urlopen", side_effect=open_request):
        client = McpClient("https://mcp.example.test", "secret", timeout=2.5)
        payload = client.call_tool("query-my-coupons", {"page": "1"})

    assert payload == {"success": True, "data": {}}
    initialized = json.loads(requests[1][0].data.decode("utf-8"))
    called = json.loads(requests[2][0].data.decode("utf-8"))
    assert initialized["method"] == "notifications/initialized"
    assert called["params"]["name"] == "query-my-coupons"
    assert requests[2][0].headers["Mcp-session-id"] == "s1"
    assert requests[2][0].headers["Authorization"] == "Bearer secret"


def test_client_parses_sse_response() -> None:
    sse = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
    response = FakeResponse(body=sse, content_type="text/event-stream")

    with patch("mcp_client.urlopen", return_value=response):
        client = McpClient("https://mcp.example.test", "secret")
        assert client.request("tools/list") == {"ok": True}


def test_client_rejects_write_or_unknown_tools() -> None:
    client = McpClient("https://mcp.example.test", "secret")

    try:
        client.call_tool("create-order", {})
    except McpConfigError as error:
        assert "not allowed" in str(error)
    else:
        raise AssertionError("write tool should be rejected")


def test_client_maps_http_errors() -> None:
    cases = [
        (401, McpAuthError),
        (429, McpRateLimitError),
        (500, McpUpstreamError),
    ]
    for status, expected in cases:
        error = HTTPError("https://mcp.example.test", status, "failed", {}, None)
        with patch("mcp_client.urlopen", side_effect=error):
            try:
                McpClient("https://mcp.example.test", "secret").request("tools/list")
            except expected:
                pass
            else:
                raise AssertionError(f"HTTP {status} should map to {expected.__name__}")


def test_client_maps_timeout() -> None:
    timeout = URLError(socket.timeout("timed out"))

    with patch("mcp_client.urlopen", side_effect=timeout):
        try:
            McpClient("https://mcp.example.test", "secret").request("tools/list")
        except McpUpstreamError as error:
            assert "timed out" in str(error)
        else:
            raise AssertionError("timeout should map to upstream error")


def test_client_requires_url_and_token() -> None:
    for url, token in (("", "secret"), ("https://mcp.example.test", "")):
        try:
            McpClient(url, token)
        except McpConfigError:
            pass
        else:
            raise AssertionError("URL and token should both be required")


def test_load_coupon_payload_prefers_input_file(tmp_path: Path) -> None:
    path = tmp_path / "coupons.json"
    path.write_text('{"data":{"coupons":[]}}', encoding="utf-8")

    payload = load_coupon_payload(str(path))

    assert payload == {"data": {"coupons": []}}


def test_load_coupon_payload_requires_explicit_snapshot() -> None:
    try:
        load_coupon_payload(None)
    except ValueError as error:
        assert "snapshot" in str(error)
    else:
        raise AssertionError("CLI must not fetch MCP data automatically")


def test_cli_parser_requires_snapshot_for_coupon_commands() -> None:
    for command in ("coupon-check", "remind"):
        try:
            build_parser().parse_args([command])
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError(f"{command} must require --input")


def test_cli_maps_invalid_snapshot_to_exit_three(
    capsys: object,
    tmp_path: Path,
) -> None:
    path = tmp_path / "broken.json"
    path.write_text("not-json", encoding="utf-8", newline="\n")
    with patch(
        "sys.argv",
        ["mcd-now", "coupon-check", "--input", str(path)],
    ):
        assert main() == 3
    assert "error:" in capsys.readouterr().err
