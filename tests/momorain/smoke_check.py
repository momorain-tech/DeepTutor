"""对运行中的本机冒烟服务做真实协议验收，不允许指向公网或真实付费网关。"""

import asyncio
import json
import uuid

import httpx
import websockets

BASE = "http://127.0.0.1:8087/deeptutor"
SOCKET = "ws://127.0.0.1:8087/deeptutor/ws"


async def chat(who, content, submission):
    async with websockets.connect(
        SOCKET, additional_headers={"Cookie": "session=" + who}, origin="http://127.0.0.1:8087"
    ) as socket:
        await socket.send(
            json.dumps(
                {
                    "type": "start_turn",
                    "protocol_version": "2.0",
                    "content": content,
                    "client_submission_id": submission,
                    "tools": [],
                }
            )
        )
        events = []
        for _ in range(100):
            event = json.loads(await asyncio.wait_for(socket.recv(), timeout=45))
            events.append(event)
            if event.get("type") in {"error", "protocol_error"}:
                raise AssertionError(event)
            if event.get("type") == "done":
                assert event["metadata"]["status"] == "completed"
                text = "".join(e.get("content", "") for e in events if e["type"] == "content")
                assert not text or who in text
                return event["session_id"], event["turn_id"], events
        raise AssertionError("未收到结束事件")


async def main():
    submission = uuid.uuid4().hex
    alice1, alice2, bob = await asyncio.gather(
        chat("alice", "数学会话", submission),
        chat("alice", "英语会话", uuid.uuid4().hex),
        chat("bob", "物理会话", uuid.uuid4().hex),
    )
    assert len({alice1[0], alice2[0], bob[0]}) == 3
    duplicate = await chat("alice", "数学会话", submission)
    assert duplicate[0] == alice1[0] and duplicate[1] == alice1[1]
    async with httpx.AsyncClient(trust_env=False) as client:
        for owner, session in [("alice", alice1), ("alice", alice2), ("bob", bob)]:
            own = await client.get(
                BASE + "/api/sessions/" + session[0], headers={"Cookie": "session=" + owner}
            )
            assert own.status_code == 200, own.text
            assert len(own.json()["messages"]) == 2
            other = "bob" if owner == "alice" else "alice"
            denied = await client.get(
                BASE + "/api/sessions/" + session[0], headers={"Cookie": "session=" + other}
            )
            assert denied.status_code == 404, denied.text
    # 模拟掉线后重连：只请求序号 4 之后的事件，历史不能从头重复。
    async with websockets.connect(
        SOCKET, additional_headers={"Cookie": "session=alice"}, origin="http://127.0.0.1:8087"
    ) as socket:
        await socket.send(
            json.dumps(
                {
                    "type": "subscribe_turn",
                    "protocol_version": "2.0",
                    "turn_id": alice1[1],
                    "after_seq": 4,
                }
            )
        )
        for _ in range(50):
            event = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
            assert event.get("seq", 5) > 4
            if event.get("type") == "done":
                break
        else:
            raise AssertionError("重连回放没有结束")
    print(
        json.dumps(
            {
                "passed": True,
                "users": 2,
                "sessions": 3,
                "duplicate_submission": "deduplicated",
                "replay": "after_seq=4",
                "cross_user_access": "404",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
