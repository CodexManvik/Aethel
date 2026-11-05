"""Machine-checkable postconditions (spec §4.1 'verify'). A task only reaches
'done' when all of its checks pass."""
import html
import re
import zipfile
from dataclasses import dataclass
from typing import Literal

import anyio
from pydantic import BaseModel, model_validator

from ..tools.paths import is_network_path, resolve_user_path


class Check(BaseModel):
    kind: Literal["file_exists", "file_contains", "min_words", "judge"]
    path: str
    text: str | None = None
    count: int | None = None

    @model_validator(mode="after")
    def _needs_fields(self) -> "Check":
        if self.kind in ("file_contains", "judge") and not self.text:
            raise ValueError(f"{self.kind} needs text")
        if self.kind == "min_words" and (self.count is None or self.count < 1):
            raise ValueError("min_words needs a positive count")
        resolved = resolve_user_path(self.path)
        if resolved is None or is_network_path(str(resolved)):  # not a URL, and not another computer: nothing asks first
            raise ValueError("checks are about files: path must be a local absolute path")
        return self

    def describe(self) -> str:
        if self.kind == "file_exists":
            return f"{self.path} exists"
        if self.kind == "file_contains":
            return f'{self.path} mentions "{self.text}"'
        if self.kind == "judge":
            return f"{self.path}: {self.text}"
        return f"{self.path} has at least {self.count} words"


@dataclass
class CheckResult:
    description: str
    passed: bool
    detail: str


MAX_READ_BYTES = 20_000_000  # what a check reads of a file: a text check doesn't need more
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


_CELL_RE = re.compile(r"<(?:a:t|t|v)(?: [^>]*)?>(.*?)</(?:a:t|t|v)>", re.DOTALL)
OFFICE_PARTS = {".xlsx": ("xl/sharedStrings", "xl/worksheets/"), ".pptx": ("ppt/slides/slide",)}


def _office_text(p, parts: tuple[str, ...]) -> str:
    """The text in a workbook's cells or a deck's slides, which are zipped XML like a .docx."""
    try:
        with zipfile.ZipFile(p) as z:
            xml = [z.read(n).decode("utf-8", errors="replace") for n in sorted(z.namelist())
                   if n.startswith(parts) and n.endswith(".xml")]
    except (zipfile.BadZipFile, KeyError, OSError):
        return ""
    return "\n".join(html.unescape(cell) for part in xml for cell in _CELL_RE.findall(part))


def _read(path: str) -> str | None:
    p = resolve_user_path(path)  # the same resolution the fs tools use, so ~ and %VAR% match
    if p is None or not p.is_file():
        return None
    suffix = p.suffix.lower()
    if suffix == ".docx":
        return _docx_text(p)
    if suffix in OFFICE_PARTS:
        return _office_text(p, OFFICE_PARTS[suffix])
    with open(p, "rb") as f:
        return f.read(MAX_READ_BYTES).decode("utf-8", errors="replace")


def run_check(check: Check) -> CheckResult:
    if check.kind == "file_exists":  # never read: it may be a 4 GB video
        p = resolve_user_path(check.path)
        found = p is not None and p.exists()
        return CheckResult(check.describe(), found, "" if found else "file not found")
    text = _read(check.path)
    if text is None:
        return CheckResult(check.describe(), False, "file not found")
    if check.kind == "judge":  # needs System 1: see run_checks_with
        return CheckResult(check.describe(), True, "not measured (System 1 unavailable)")
    if check.kind == "file_contains":
        found = check.text.lower() in text.lower()
        return CheckResult(check.describe(), found, "" if found else "text not found in file")
    words = len(text.split())
    return CheckResult(check.describe(), words >= check.count, f"{words} words")


def run_checks(checks: list[Check]) -> list[CheckResult]:
    return [run_check(c) for c in checks]


JUDGE_OPTIONS = {"yes": "the description fits the document", "no": "the description does not fit the document"}


def judge_question(claim: str) -> str:
    return f'Does this document fit the description "{claim}"?'  # measured best of 5 phrasings (eval_s1_judge.py)


async def run_checks_with(checks: list[Check], system1, threshold: float) -> list[CheckResult]:
    """Like run_checks, but "judge" checks (fuzzy claims about a file's content,
    spec §5.2) are answered by System 1: they fail only on a confident "no"
    (P(yes) below the threshold), since failing good work costs more than
    missing a bad result the other checks may still catch. Without System 1
    they pass as not measured."""
    results = []
    for check in checks:
        text = await anyio.to_thread.run_sync(_read, check.path) if check.kind == "judge" else None
        p = None
        if text is not None and system1 is not None:
            answer = await system1.choice({"document": text[:4000]}, judge_question(check.text), JUDGE_OPTIONS, "judge")
            p = answer[1]["yes"] if answer is not None else None
        if check.kind != "judge" or text is None or p is None:
            results.append(await anyio.to_thread.run_sync(run_check, check))
        else:
            results.append(CheckResult(check.describe(), p >= threshold, f"p={p:.2f}"))
    return results
