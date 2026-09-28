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
