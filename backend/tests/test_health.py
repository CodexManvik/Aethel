from fastapi.testclient import TestClient

from aethel import __version__
from aethel.app import create_app


def test_health_reports_ok_and_version():
    with TestClient(create_app()) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "version": __version__}


def test_aethel_home_is_isolated(aethel_home):
    from aethel.paths import aethel_home as home_fn, db_path

    assert home_fn() == aethel_home
    assert aethel_home.is_dir()
    assert db_path() == aethel_home / "aethel.db"
