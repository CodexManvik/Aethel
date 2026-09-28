import pytest

from aethel.hub import EventHub
from aethel.protocol import Token

pytestmark = pytest.mark.anyio


async def test_publish_reaches_every_subscriber_and_drops_broken_ones():
    hub = EventHub()
    got_a, got_b = [], []

    async def a(payload):
        got_a.append(payload)

    async def b(payload):
        got_b.append(payload)

    async def broken(payload):
        raise RuntimeError("socket gone")

    unsub_a = hub.subscribe(a)
    hub.subscribe(b)
    hub.subscribe(broken)
    await hub.publish(Token(message_id="m", text="hi"))
    assert got_a == got_b == ['{"type":"token","message_id":"m","text":"hi"}']
    assert hub.subscriber_count == 2  # broken one removed
    unsub_a()
    await hub.publish(Token(message_id="m", text="!"))
    assert len(got_a) == 1 and len(got_b) == 2


async def test_a_stalled_subscriber_is_dropped_and_told_so(monkeypatch):
    import asyncio
    import aethel.hub as hub_mod
    monkeypatch.setattr(hub_mod, "SEND_TIMEOUT_S", 0.1)
    hub = EventHub()
    dropped, got = [], []

    async def stalled(payload):
        await asyncio.sleep(10)

    async def fine(payload):
        got.append(payload)

    hub.subscribe(stalled, on_drop=lambda: dropped.append("stalled"))
    hub.subscribe(fine)
    await hub.publish(Token(message_id="m", text="1"))
    await hub.publish(Token(message_id="m", text="2"))
    assert dropped == ["stalled"] and len(got) == 2 and hub.subscriber_count == 1
