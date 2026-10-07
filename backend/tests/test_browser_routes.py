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
        assert env.launches == [["C:\\Edge\\msedge.exe", f"--user-data-dir={env.profile}", "--no-first-run",
                                 "--no-default-browser-check", "--disable-sync", "--disable-background-mode", "about:blank"]]
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


# ---- review fixes -------------------------------------------------------------------------------------------------
def test_stopping_the_browser_makes_it_forget_the_page_it_was_on(env):
    with env.client as c:
        for call in (lambda: c.post("/api/browser/restart"), lambda: c.post("/api/browser/sign-in"),
                     lambda: c.delete("/api/browser/profile")):
            env.svc.browser._url, env.svc.browser._elements = "https://old.example/", {"e1": object()}
            env.process.alive = False
            call()
            assert env.svc.browser._url is None and env.svc.browser._elements == {}


def test_clear_data_tries_again_while_edge_lets_go_of_its_files_and_says_so_if_it_cannot(env, monkeypatch):
    from aethel.api.routes import browser as routes
    monkeypatch.setattr(routes, "RETRY_PAUSE_S", 0)
    env.profile.mkdir(parents=True)
    (env.profile / "Cookies").write_text("x", encoding="utf-8")
    real = routes.shutil.rmtree
    attempts = []

    def flaky(path, *a, **k):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError("still held by msedge.exe")
        return real(path, *a, **k)

    monkeypatch.setattr(routes.shutil, "rmtree", flaky)
    with env.client as c:
        assert c.delete("/api/browser/profile").status_code == 204 and not env.profile.exists() and len(attempts) == 3
        env.profile.mkdir(parents=True)
        monkeypatch.setattr(routes.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(PermissionError("held")))
        stuck = c.delete("/api/browser/profile")
        assert stuck.status_code == 409 and "still in use" in stuck.json()["detail"] and env.profile.exists()


def test_clear_data_also_forgets_the_replay_pictures_of_browser_steps_only(env):
    from aethel.paths import aethel_home
    svc = env.svc
    media = aethel_home() / "media" / "tasks" / "t1"
    media.mkdir(parents=True, exist_ok=True)
    conv = svc.conversations.create()
    task = svc.tasks.create(conv.id, "look it up")
    shots = {}
    for tool in ("browser_click", "win_click", "browser_type"):
        step = svc.tasks.add_step(task.id, tool, {}, tool, "allow", "agent")
        rel = f"tasks/t1/{step.id}.jpg"
        (aethel_home() / "media" / rel).write_bytes(b"\xff\xd8\xff")
        svc.tasks.finish_step(step.id, True, "ok", 1, False, None, rel)
        shots[tool] = rel
    with env.client as c:
        assert c.delete("/api/browser/profile").status_code == 204
    assert not (aethel_home() / "media" / shots["browser_click"]).exists()
    assert not (aethel_home() / "media" / shots["browser_type"]).exists()
    assert (aethel_home() / "media" / shots["win_click"]).exists()                   # a desktop picture isn't the browser's
    assert {s.tool: s.thumbnail for s in svc.tasks.steps(task.id)} == {"browser_click": None, "win_click": shots["win_click"],
                                                                      "browser_type": None}


def test_a_restart_relaunches_with_a_command_built_from_the_settings_of_now():
    import asyncio
    from aethel.tools.mcp_hub import McpHub, ServerSpec, _Conn
    from aethel.tools.registry import ToolRegistry
    hub, launched, state = McpHub(ToolRegistry()), [], {"show": False}
    hub._launch = launched.append

    def build():
        spec = ServerSpec("browser", ["npx", "pkg", *([] if state["show"] else ["--headless"])], lambda h, t: [])
        spec.rebuild = build
        return spec

    hub._conns["browser"] = _Conn(build())
    state["show"] = True                                      # Settings -> Browser -> Show browser, then Restart now
    asyncio.run(hub.restart("browser"))
    assert launched[-1].command == ["npx", "pkg"]
    hub._conns["browser"].stop.clear()
    hub._conns["browser"].spec.rebuild = lambda: None          # nothing to rebuild with (npx is gone): the old one stays
    asyncio.run(hub.restart("browser"))
    assert launched[-1] is hub._conns["browser"].spec
