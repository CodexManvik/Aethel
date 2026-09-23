from dataclasses import dataclass

import httpx
from openai import DefaultAsyncHttpxClient

from .auth import AuthConfig
from .chat.service import ChatService
from .keys import KeyStore
from .paths import LEGACY_SETTINGS_PATH, db_path
from .providers.local_llama import LocalLlama
from .providers.router import ProviderFactory, RoleRouter, make_provider_factory
from .settings import SettingsService
from .store.db import Database
from .store.repos import ConversationRepo, MessageRepo

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
    chat: ChatService
    http_client: httpx.AsyncClient  # shared by every provider the default factory builds

    def close(self) -> None:
        if self.local_llm is not None:
            self.local_llm.stop()
        self.db.close()


def build_services(*, provider_factory: ProviderFactory | None = None, local_llm=AUTO) -> Services:
    db = Database(db_path())
    conversations, messages = ConversationRepo(db), MessageRepo(db)
    messages.reconcile_interrupted()  # replies cut off by a previous kill/crash
    settings = SettingsService(db)
    settings.import_legacy(LEGACY_SETTINGS_PATH)
    keys = KeyStore()
    local = LocalLlama(lambda: settings.get().local_llm) if local_llm is AUTO else local_llm
    http_client = DefaultAsyncHttpxClient()
    factory = provider_factory or make_provider_factory(http_client)
    router = RoleRouter(settings=settings, keys=keys, local=local, factory=factory)
    return Services(
        db=db,
        settings=settings,
        keys=keys,
        auth=AuthConfig.from_env(),
        conversations=conversations,
        messages=messages,
        local_llm=local,
        router=router,
        provider_factory=factory,
        chat=ChatService(conversations=conversations, messages=messages, router=router, settings=settings),
        http_client=http_client,
    )
