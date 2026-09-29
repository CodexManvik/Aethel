import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from openai import DefaultAsyncHttpxClient

from .auth import AuthConfig
from .chat.service import ChatService
from .hub import EventHub
from .keys import KeyStore
from .paths import LEGACY_SETTINGS_PATH, aethel_home, db_path
from .providers.local_llama import LocalLlama
from .providers.router import ProviderFactory, RoleRouter, make_provider_factory
from .runtime.engine import TaskEngine
from .runtime.store import TaskRepo
from .safety.approvals import ApprovalBroker
from .safety.changes import ChangeLog
from .safety.permissions import Permissions
from .settings import SettingsService
from .store.db import Database
from .system1.service import System1
from .store.repos import ConversationRepo, MessageRepo
from .tools.desktop import Desktop, desktop_spec
from .tools.file_commander import FileCommander, file_commander_spec
from .tools.local_fs import fs_tools
from .tools.mcp_hub import McpHub, ServerSpec
from .tools.office import Office, office_spec
from .tools.registry import ToolRegistry
from .tools.shell import shell_tool

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
    permissions: Permissions
    changes: ChangeLog
    registry: ToolRegistry
    approvals: ApprovalBroker
    tasks: TaskRepo
    engine: TaskEngine
    mcp: McpHub
    system1: System1
    mcp_servers: list[ServerSpec]  # started by the app's lifespan

    def close(self) -> None:
        if self.local_llm is not None:
            self.local_llm.stop()
        self.db.close()


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
    factory = provider_factory or make_provider_factory(http_client)
    router = RoleRouter(settings=settings, keys=keys, local=local, factory=factory)
    hub = EventHub()
    permissions = Permissions(permissions_path or aethel_home() / "permissions.yaml")
    changes = ChangeLog(db)
    registry = ToolRegistry()
    for tool in [*fs_tools(permissions, changes), shell_tool(permissions)]:
        registry.register(tool)
    approvals = ApprovalBroker(hub)
    system1 = System1(db, settings)
    engine = TaskEngine(tasks=tasks, messages=messages, conversations=conversations, router=router,
                        registry=registry, approvals=approvals, hub=hub, settings=settings)
    chat = ChatService(conversations=conversations, messages=messages, router=router, settings=settings, hub=hub,
                       task_note=engine.note_for_chat)
    return Services(
        db=db, settings=settings, keys=keys, auth=AuthConfig.from_env(), conversations=conversations,
        messages=messages, local_llm=local, router=router, provider_factory=factory, hub=hub, chat=chat,
        http_client=http_client, permissions=permissions, changes=changes, registry=registry,
        approvals=approvals, tasks=tasks, engine=engine, mcp=McpHub(registry), system1=system1,
        mcp_servers=default_mcp_servers(permissions, changes, router) if mcp_servers is None else mcp_servers,
    )


def default_mcp_servers(permissions: Permissions, changes: ChangeLog, router: RoleRouter) -> list[ServerSpec]:
    """Desktop, Office and Desktop Commander. AETHEL_MCP=0 turns them all off."""
    if os.environ.get("AETHEL_MCP") == "0":
        return []
    specs = [desktop_spec(Desktop(vision=router)), office_spec(Office(permissions, changes)),
             file_commander_spec(FileCommander(permissions, changes))]
    return [spec for spec in specs if spec is not None]
