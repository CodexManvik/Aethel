from dataclasses import dataclass

from .auth import AuthConfig
from .keys import KeyStore
from .paths import LEGACY_SETTINGS_PATH, db_path
from .settings import SettingsService
from .store.db import Database
from .store.repos import ConversationRepo, MessageRepo


@dataclass
class Services:
    db: Database
    settings: SettingsService
    keys: KeyStore
    auth: AuthConfig
    conversations: ConversationRepo
    messages: MessageRepo

    def close(self) -> None:
        self.db.close()


def build_services() -> Services:
    db = Database(db_path())
    settings = SettingsService(db)
    settings.import_legacy(LEGACY_SETTINGS_PATH)
    return Services(
        db=db,
        settings=settings,
        keys=KeyStore(),
        auth=AuthConfig.from_env(),
        conversations=ConversationRepo(db),
        messages=MessageRepo(db),
    )
