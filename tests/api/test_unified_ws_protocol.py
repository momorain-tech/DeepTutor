from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import auth, unified_ws


class _Turns:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str]] = []

    async def cancel_turn(self, turn_id: str, *, command_id: str) -> bool:
        self.cancelled.append((turn_id, command_id))
        return True

    async def check_active_turn(self, _session_id: str) -> dict[str, str]:
        return {"turn_id": "turn-1", "status": "recovering", "owner_id": "worker-b"}


@pytest.fixture
def protocol_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _Turns]:
    turns = _Turns()

    async def allow(_ws):
        return None

    monkeypatch.setattr(auth, "ws_require_auth", allow)
    app = FastAPI()
    app.state.application_container = SimpleNamespace(turns=turns)
    app.include_router(unified_ws.router)
    return TestClient(app), turns


def test_ws_rejects_missing_and_future_protocol_versions(protocol_client) -> None:
    client, _turns = protocol_client
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "ping"})
        missing = socket.receive_json()
        socket.send_json({"type": "ping", "protocol_version": "3.0"})
        future = socket.receive_json()

    for frame in (missing, future):
        assert frame == {
            "type": "protocol_error",
            "error_code": "unsupported_protocol_version",
            "message": "Unsupported or missing protocol_version; expected 2.0.",
            "retryable": False,
            "session_id": "",
            "turn_id": "",
            "protocol_version": "2.0",
        }


def test_ws_versions_heartbeats_active_state_and_command_ack(protocol_client) -> None:
    client, turns = protocol_client
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "ping", "protocol_version": "2.0"})
        assert socket.receive_json() == {"type": "pong", "protocol_version": "2.0"}

        socket.send_json(
            {
                "type": "check_active_turn",
                "session_id": "session-1",
                "protocol_version": "2.0",
            }
        )
        assert socket.receive_json() == {
            "type": "active_turn_info",
            "turn_id": "turn-1",
            "status": "recovering",
            "owner_id": "worker-b",
            "protocol_version": "2.0",
        }

        socket.send_json(
            {
                "type": "cancel_turn",
                "turn_id": "turn-1",
                "command_id": "cancel-1",
                "protocol_version": "2.0",
            }
        )
        assert socket.receive_json() == {
            "type": "command_ack",
            "command_id": "cancel-1",
            "command_type": "cancel_turn",
            "accepted": True,
            "turn_id": "turn-1",
            "error_code": "",
            "message": "",
            "protocol_version": "2.0",
        }

    assert turns.cancelled == [("turn-1", "cancel-1")]


def test_ws_requires_command_ids_for_retryable_mutations(protocol_client) -> None:
    client, turns = protocol_client
    with client.websocket_connect("/ws") as socket:
        socket.send_json(
            {
                "type": "cancel_turn",
                "turn_id": "turn-1",
                "protocol_version": "2.0",
            }
        )
        frame = socket.receive_json()

    assert frame["type"] == "protocol_error"
    assert frame["error_code"] == "invalid_command"
    assert frame["protocol_version"] == "2.0"
    assert turns.cancelled == []


def test_ws_start_turn_http_error_is_terminal_and_keeps_socket(protocol_client) -> None:
    """取模型 key 失败（如 one-api 429）时：回一个终止的拒绝，连接不断开。

    以前异常冒到最外层把 WebSocket 关掉并标成可重试，前端就重连重发同一条消息，
    形成每秒一两次的重试循环（2026-11-04 线上事故）。
    """
    from fastapi import HTTPException

    client, turns = protocol_client
    attempts: list[dict] = []

    async def start_turn(payload):
        attempts.append(payload)
        raise HTTPException(503, "模型服务繁忙，请稍等片刻再试")

    turns.start_turn = start_turn
    with client.websocket_connect("/ws") as socket:
        socket.send_json(
            {
                "type": "start_turn",
                "protocol_version": "2.0",
                "content": "hi",
                "session_id": "session-1",
            }
        )
        frame = socket.receive_json()
        # 连接还活着：还能正常 ping。
        socket.send_json({"type": "ping", "protocol_version": "2.0"})
        assert socket.receive_json() == {"type": "pong", "protocol_version": "2.0"}

    assert frame["type"] == "protocol_error"
    assert frame["error_code"] == "start_turn_rejected"
    assert frame["message"] == "模型服务繁忙，请稍等片刻再试"
    assert frame["session_id"] == "session-1"
    assert len(attempts) == 1


def test_ws_quota_detail_dict_becomes_message(protocol_client) -> None:
    from fastapi import HTTPException

    client, turns = protocol_client

    async def start_turn(_payload):
        raise HTTPException(402, {"message": "模型额度不足，请充值", "reason": "no_quota"})

    turns.start_turn = start_turn
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "start_turn", "protocol_version": "2.0", "content": "hi"})
        frame = socket.receive_json()
    assert frame["error_code"] == "start_turn_rejected"
    assert frame["message"] == "模型额度不足，请充值"
