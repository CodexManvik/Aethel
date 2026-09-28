"""Machine-checkable postconditions (spec §4.1 'verify'). A task only reaches
'done' when all of its checks pass."""
import html
import re
import zipfile
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, model_validator

from ..tools.paths import resolve_user_path


class Check(BaseModel):
    kind: Literal["file_exists", "file_contains", "min_words"]
    path: str
    text: str | None = None
    count: int | None = None

    @model_validator(mode="after")
    def _needs_fields(self) -> "Check":
        if self.kind == "file_contains" and not self.text:
            raise ValueError("file_contains needs text")
        if self.kind == "min_words" and (self.count is None or self.count < 1):
            raise ValueError("min_words needs a positive count")
        return self

    def describe(self) -> str:
        if self.kind == "file_exists":
            return f"{self.path} exists"
        if self.kind == "file_contains":
            return f'{self.path} mentions "{self.text}"'
        return f"{self.path} has at least {self.count} words"


@dataclass
class CheckResult:
    description: str
    passed: bool
    detail: str


_PARA_RE = re.compile(r"<w:p[ >].*?</w:p>", re.DOTALL)
_TEXT_RE = re.compile(r"<w:t(?: [^>]*)?>(.*?)</w:t>", re.DOTALL)


def _docx_text(p) -> str:
    """Paragraph text of a .docx; runs inside a paragraph are joined without a
    space, since Word often splits one word across runs."""
    try:
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return ""
    return "\n".join(html.unescape("".join(_TEXT_RE.findall(para))) for para in _PARA_RE.findall(xml))


def _read(path: str) -> str | None:
    p = resolve_user_path(path)  # the same resolution the fs tools use, so ~ and %VAR% match
    if p is None or not p.is_file():
        return None
    if p.suffix.lower() == ".docx":
        return _docx_text(p)
    return p.read_text(encoding="utf-8", errors="replace")


def run_check(check: Check) -> CheckResult:
    text = _read(check.path)
    if text is None:
        return CheckResult(check.describe(), False, "file not found")
    if check.kind == "file_exists":
        return CheckResult(check.describe(), True, "")
    if check.kind == "file_contains":
        found = check.text.lower() in text.lower()
        return CheckResult(check.describe(), found, "" if found else "text not found in file")
    words = len(text.split())
    return CheckResult(check.describe(), words >= check.count, f"{words} words")


def run_checks(checks: list[Check]) -> list[CheckResult]:
    return [run_check(c) for c in checks]
