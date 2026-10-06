"""Chat answers that can search the web and cite what they found (Phase 3 spec §6)."""
from ..settings import AppSettings
from ..store.repos import Conversation


def effective_web(settings: AppSettings, conv: Conversation) -> bool:
    """Whether web tools may be offered in this conversation. Private mode wins over everything; otherwise the
    conversation's own switch, and where it has none, Settings → Allow web access."""
    return not settings.private_mode and (conv.web if conv.web is not None else settings.internet)
