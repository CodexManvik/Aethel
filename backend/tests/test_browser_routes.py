"""Settings -> Browser: status, sign in, clear data, restart (Phase 3 spec §7.4), and the routing rule (§7.3)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from mcp import types as mt

from aethel.app import create_app
from aethel.runtime.prompts import BROWSER_RULE, PLANNER_SYSTEM, executor_system, planner_system
from aethel.services import build_services
from aethel.tools import browser as br
from tests.fakes import FakeLocal, factory_from


class FakeProcess:
    def __init__(self):
        self.alive = True

    def poll(self):
        return None if self.alive else 0


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "aethel_home", lambda: tmp_path)
    svc = build_services(provider_factory=factory_from({}), local_llm=FakeLocal(up=False))
    restarts, launches = [], []

    async def restart(server):
        restarts.append(server)

    svc.mcp.restart = restart
    svc.mcp.status = lambda: {"browser": "running"}
    process = FakeProcess()
    svc.browser.find_edge = lambda: "C:\\Edge\\msedge.exe"
    svc.browser.popen = lambda args, **kw: launches.append(args) or process
    profile = tmp_path / "browser" / "profile"
    svc.browser.profile_dir = profile
    svc.browser.out_dir = tmp_path / "browser" / "out"
    yield SimpleNamespace(svc=svc, client=TestClient(create_app(svc)), restarts=restarts, launches=launches,
                          process=process, profile=profile, tmp=tmp_path)
    svc.close()


def busy(svc, in_use: bool = True):
    """Say whether a running task is using the browser (the engine's own check is tested below)."""
    svc.engine.browser_in_use = lambda: in_use


def test_the_engine_knows_when_a_running_task_has_used_the_browser(env):
    svc = env.svc
    conv = svc.conversations.create()
    task = svc.tasks.create(conv.id, "look it up")
    assert svc.engine.browser_in_use() is False                        # nothing is running
    svc.tasks.add_step(task.id, "browser_navigate", {"url": "https://example.com"}, "Go to it", "allow", "agent")
    assert svc.engine.browser_in_use() is False                        # a step in a task that is over doesn't count
    svc.engine._runners[task.id] = object()                            # ...but while its runner is alive, it does
    try:
        assert svc.engine.browser_in_use() is True
        other = svc.tasks.create(conv.id, "write a poem")
        svc.engine._runners = {other.id: object()}
        svc.tasks.add_step(other.id, "fs_write", {"path": "x"}, "Write x", "allow", "agent")
        assert svc.engine.browser_in_use() is False                    # a running task that never touched it
    finally:
        svc.engine._runners.clear()


def test_status_says_whether_it_runs_what_it_shows_and_whether_a_profile_exists(env):
    with env.client as c:
        got = c.get("/api/browser").json()
        assert got == {"status": "running", "show": False, "profile_exists": False, "signing_in": False}
        env.profile.mkdir(parents=True)
        (env.profile / "Default").mkdir()
        env.svc.settings.update({"browser": {"show": True}})
        got = c.get("/api/browser").json()
        assert got["profile_exists"] is True and got["show"] is True
        env.svc.mcp.status = lambda: {}
        assert c.get("/api/browser").json()["status"] == "absent"   # no npx, so no browser at all


def test_sign_in_opens_edge_on_the_profile_with_nothing_else_holding_it(env):
    with env.client as c:
        res = c.post("/api/browser/sign-in")
        assert res.status_code == 202
        assert env.restarts == ["browser"]                       # Aethel's own Edge is stopped first: it holds the profile
        assert env.launches == [["C:\\Edge\\msedge.exe", f"--user-data-dir={env.profile}", "--no-first-run", "about:blank"]]
        assert c.get("/api/browser").json()["signing_in"] is True
        assert c.post("/api/browser/sign-in").status_code == 409  # one window at a time
        env.process.alive = False                                  # the user closed it
        assert c.get("/api/browser").json()["signing_in"] is False


def test_sign_in_is_refused_while_a_task_is_using_the_browser_or_edge_is_missing(env):
    with env.client as c:
        busy(env.svc)
        refused = c.post("/api/browser/sign-in")
        assert refused.status_code == 409 and "task" in refused.json()["detail"].lower()
        assert env.launches == [] and env.restarts == []
        busy(env.svc, False)                                         # the task is over
        env.svc.browser.find_edge = lambda: None
        gone = c.post("/api/browser/sign-in")
        assert gone.status_code == 503 and "Edge" in gone.json()["detail"]
        env.svc.mcp.status = lambda: {}
        env.svc.browser.find_edge = lambda: "C:\\Edge\\msedge.exe"
        assert c.post("/api/browser/sign-in").status_code == 409    # no background browser to sign in for


def test_while_the_sign_in_window_is_open_the_agent_leaves_the_browser_alone(env):
    import asyncio
    with env.client as c:
        c.post("/api/browser/sign-in")
    browser = env.svc.browser
    tools = {t.name: t for t in browser.adapt(SimpleNamespace(), [
        mt.Tool(name="browser_navigate", description="x", inputSchema={"type": "object"})])}
    verdict = asyncio.run(tools["browser_navigate"].assess({"url": "https://example.com"}))
    assert verdict.verdict == "deny" and "signing in" in verdict.reason.lower()
    env.process.alive = False
    assert asyncio.run(tools["browser_navigate"].assess({"url": "https://example.com"})).verdict == "allow"


def test_clear_data_deletes_the_profile_and_what_pages_left_behind(env):
    env.profile.mkdir(parents=True)
    (env.profile / "Cookies").write_text("session", encoding="utf-8")
    env.svc.browser.out_dir.mkdir(parents=True)
    (env.svc.browser.out_dir / "page-1.yml").write_text("old page", encoding="utf-8")
    with env.client as c:
        assert c.delete("/api/browser/profile").status_code == 204
    assert env.restarts == ["browser"] and not env.profile.exists()
    assert not list(env.svc.browser.out_dir.iterdir())


def test_clear_data_waits_for_a_task_and_for_the_sign_in_window(env):
    env.profile.mkdir(parents=True)
    with env.client as c:
        busy(env.svc)
        assert c.delete("/api/browser/profile").status_code == 409 and env.profile.exists()
        busy(env.svc, False)
        c.post("/api/browser/sign-in")
        locked = c.delete("/api/browser/profile")
        assert locked.status_code == 409 and "window" in locked.json()["detail"].lower() and env.profile.exists()
        env.process.alive = False
        assert c.delete("/api/browser/profile").status_code == 204


def test_restart_applies_the_show_setting_unless_a_task_is_using_it(env):
    with env.client as c:
        assert c.post("/api/browser/restart").status_code == 202 and env.restarts == ["browser"]
        busy(env.svc)
        assert c.post("/api/browser/restart").status_code == 409 and env.restarts == ["browser"]


# ---- the routing rule ----------------------------------------------------------------------------------------------
def test_the_rule_for_web_work_is_in_both_prompts_only_when_there_is_a_browser():
    assert "browser_* tools" in BROWSER_RULE and "open_url" in BROWSER_RULE
    assert planner_system(browser=False) == PLANNER_SYSTEM and BROWSER_RULE not in PLANNER_SYSTEM
    assert planner_system(browser=True) == PLANNER_SYSTEM + "\n\n" + BROWSER_RULE
    from datetime import datetime
    now = datetime(2026, 10, 7, 9, 0)
    without = executor_system("g", ["s"], [], now)
    with_rule = executor_system("g", ["s"], [], now, browser=True)
    assert BROWSER_RULE not in without and BROWSER_RULE in with_rule
    assert with_rule.replace(f"\n- {BROWSER_RULE}", "") == without              # nothing else about the prompt moved
