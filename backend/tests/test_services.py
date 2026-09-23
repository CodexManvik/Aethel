import httpx
from fastapi.testclient import TestClient

from aethel.app import create_app
from aethel.services import build_services
from aethel.settings import RouteEntry
from tests.fakes import FakeLocal


def test_default_factory_providers_share_one_http_client():
    svc = build_services(local_llm=FakeLocal())
    try:
        assert isinstance(svc.http_client, httpx.AsyncClient)
        settings = svc.settings.get()
        a = svc.provider_factory(RouteEntry(provider="groq", model="m1"), "k", settings)
        b = svc.provider_factory(RouteEntry(provider="openrouter", model="m2"), "k", settings)
        assert a._client._client is svc.http_client
        assert b._client._client is svc.http_client
    finally:
        svc.close()


def test_owning_lifespan_closes_the_http_client():
    with TestClient(create_app()) as client:
        svc = client.app.state.services
        assert svc.http_client.is_closed is False
    assert svc.http_client.is_closed is True
