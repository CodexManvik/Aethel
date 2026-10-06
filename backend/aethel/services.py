import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from openai import DefaultAsyncHttpxClient

from .auth import AuthConfig
from .chat.service import ChatService
from .hub import EventHub
from .keys import KeyStore
from .protocol import CursorIntent
from .paths import LEGACY_SETTINGS_PATH, aethel_home, db_path
from .providers.local_llama import LocalLlama
from .providers.router import ProviderFactory, RoleRouter, make_provider_factory
from .runtime.engine import TaskEngine
from .runtime.store import TaskRepo
from .safety.approvals import ApprovalBroker
from .safety.changes import ChangeLog
from .safety.permissions import Permissions
from .settings import AppSettings, SettingsService
from .memory import embed as embed_module
from .memory.episodic import EpisodicIndex
from .memory.extract import FactExtractor
from .memory.facts import FactStore
from .memory.rsm import KnowledgeStore
from .store.db import Database
from .system1.service import System1
from .store.repos import ConversationRepo, MessageRepo
from .tools.browser import Browser, browser_spec
from .tools.desktop import Desktop, desktop_spec
from .tools.file_commander import FileCommander, file_commander_spec
from .tools.launcher import open_url_tool
from .tools.local_fs import fs_tools
from .tools.mcp_hub import McpHub, ServerSpec
from .tools.office import Office, office_spec
from .tools.registry import ToolRegistry
from .tools.shell import shell_tool
from .tools.web import SourceList, web_tools
from .usage import UsageLog

AUTO = object()


@dataclass
class Services:
    db: Database
    settings: SettingsService
    keys: KeyStore
    auth: AuthConfig
    conversations: ConversationRepo
    messages: MessageRepo
    local_llm: object | None
    router: RoleRouter
    provider_factory: ProviderFactory
    hub: EventHub
    chat: ChatService
    http_client: httpx.AsyncClient  # shared by every provider the default factory builds
    web_http: httpx.AsyncClient     # the web tools' own: no env proxies, no cookies shared with provider traffic
    browser: Browser                # Aethel's own background browser (Settings -> Browser drives it)
    permissions: Permissions
    changes: ChangeLog
    registry: ToolRegistry
    approvals: ApprovalBroker
    tasks: TaskRepo
    engine: TaskEngine
    mcp: McpHub
    system1: System1
    knowledge: KnowledgeStore
    facts: FactStore
    episodic: EpisodicIndex
    extractor: FactExtractor
    usage: UsageLog
    mcp_servers: list[ServerSpec]  # started by the app's lifespan

    def close(self) -> None:
        if self.local_llm is not None:
            self.local_llm.stop()
        self.db.close()


def _embed(texts: list[str]):
    """The embedder, looked up at call time (so tests can swap in a fake)."""
    return embed_module.embed(texts)


def build_services(*, provider_factory: ProviderFactory | None = None, local_llm=AUTO,
                   permissions_path: Path | None = None, mcp_servers: list[ServerSpec] | None = None) -> Services:
    db = Database(db_path())
    conversations, messages = ConversationRepo(db), MessageRepo(db)
    messages.reconcile_interrupted()  # replies cut off by a previous kill/crash
    tasks = TaskRepo(db)
    tasks.reconcile_interrupted()     # tasks cut off by a previous kill/crash become 'paused'
    settings = SettingsService(db)
    settings.import_legacy(LEGACY_SETTINGS_PATH)
    keys = KeyStore()
    local = LocalLlama(lambda: settings.get().local_llm) if local_llm is AUTO else local_llm
    http_client = DefaultAsyncHttpxClient()
    browser = Browser(thumbnails=lambda: settings.get().replay_thumbnails)
    # Not the providers' client: an environment proxy would resolve names itself, behind the SSRF check's back, and
    # pages' cookies must never ride along with a call to a model provider. Redirects are followed by hand.
    web_http = httpx.AsyncClient(trust_env=False, follow_redirects=False)
    factory = provider_factory or make_provider_factory(http_client)
    usage = UsageLog(db)
    router = RoleRouter(settings=settings, keys=keys, local=local, factory=factory, usage=usage)
    hub = EventHub()
    permissions = Permissions(permissions_path or aethel_home() / "permissions.yaml")
    changes = ChangeLog(db)
    registry = ToolRegistry()
    for tool in [*fs_tools(permissions, changes), shell_tool(permissions), *([open_url_tool()] if os.name == "nt" else [])]:
        registry.register(tool)
    # Always registered, offered only where the web is on (chat: effective_web; tasks: the engine). The numbered
    # sources belong to the turn or task asking, carried on its ToolContext.
    for tool in web_tools(lambda ctx: ctx.sources if ctx.sources is not None else SourceList(), web_http):
        registry.register(tool)
    approvals = ApprovalBroker(hub)
    system1 = System1(db, settings)
    knowledge = KnowledgeStore(aethel_home() / "knowledge", _embed)
    facts = FactStore(db, _embed)
    episodic = EpisodicIndex(db, _embed, aethel_home() / "episodic" / "index.tvim")
    extractor = FactExtractor(facts=facts, messages=messages, router=router, system1=system1, settings=settings,
                              hub=hub)
    engine = TaskEngine(tasks=tasks, messages=messages, conversations=conversations, router=router,
                        registry=registry, approvals=approvals, hub=hub, settings=settings, knowledge=knowledge,
                        system1=system1, facts=facts)
    chat = ChatService(conversations=conversations, messages=messages, router=router, settings=settings, hub=hub,
                       task_note=engine.note_for_chat, facts=facts, episodic=episodic, extractor=extractor,
                       web_tools=lambda: [registry.get(name) for name in registry.names("web")])
    return Services(
        db=db, settings=settings, keys=keys, auth=AuthConfig.from_env(), conversations=conversations,
        messages=messages, local_llm=local, router=router, provider_factory=factory, hub=hub, chat=chat,
        http_client=http_client, web_http=web_http, browser=browser, permissions=permissions, changes=changes, registry=registry,
        approvals=approvals, tasks=tasks, engine=engine, mcp=McpHub(registry), system1=system1,
        knowledge=knowledge, facts=facts, episodic=episodic, extractor=extractor, usage=usage,
        mcp_servers=default_mcp_servers(permissions, changes, router, settings, hub, local, browser) if mcp_servers is None else mcp_servers,
    )


def local_ports(local, settings: AppSettings) -> list[int]:
    """The ports of the model servers on this computer (the one Aethel runs, and a custom one that's local): the
    background browser is kept away from them, as it is from the backend itself."""
    ports = {local._port() if local is not None and hasattr(local, "_port") else 8080}
    url = urlsplit(settings.custom_base_url) if settings.custom_base_url else None
    if url is not None and url.hostname in ("127.0.0.1", "localhost", "::1") and url.port:
        ports.add(url.port)
    return sorted(ports)


def default_mcp_servers(permissions: Permissions, changes: ChangeLog, router: RoleRouter,
                        settings: SettingsService, hub: EventHub, local=None, browser: Browser | None = None) -> list[ServerSpec]:
    """Desktop, Office, Desktop Commander and the background browser. AETHEL_MCP=0 turns them all off."""
    if os.environ.get("AETHEL_MCP") == "0":
        return []
    s = settings.get()
    browser = browser or Browser(thumbnails=lambda: settings.get().replay_thumbnails)
    specs = [desktop_spec(Desktop(vision=router, thumbnails=lambda: settings.get().replay_thumbnails,
                                 on_pointer=_cursor(hub))), office_spec(Office(permissions, changes)),
             file_commander_spec(FileCommander(permissions, changes)),
             browser_spec(browser, int(os.environ.get("AETHEL_PORT", "8765")), local_ports(local, s), s.browser.show)]
    return [spec for spec in specs if spec is not None]


def _cursor(hub: EventHub):
    async def publish(task_id: str | None, x: int, y: int, label: str) -> None:
        await hub.publish(CursorIntent(task_id=task_id, x=x, y=y, label=label))
    return publish
