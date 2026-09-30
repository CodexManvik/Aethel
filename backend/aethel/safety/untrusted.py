"""Marking text the model must treat as data, not instructions (spec §4.3)."""
import re

_CLOSE_UNTRUSTED_RE = re.compile(r"</untrusted", re.IGNORECASE)


def wrap_untrusted(source: str, content: str) -> str:
    """Wrap content the model must treat as data, not instructions. Any
    closing tag inside the content is neutralised so it can't escape early,
    and the source name is quote-escaped defensively."""
    safe_source = source.replace('"', "&quot;")
    safe_content = _CLOSE_UNTRUSTED_RE.sub("&lt;/untrusted", content)
    return f'<untrusted source="{safe_source}">\n{safe_content}\n</untrusted>'
