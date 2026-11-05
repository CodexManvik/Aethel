"""Word, Excel and PowerPoint through COM, always visible (spec §4.2 `office`).

Runs as its own process (`python -m aethel.mcp_servers.office`) so a hung COM
call or the kill switch can't take the backend down. Paths arrive absolute
and already permission-checked by Aethel's adapter (tools/office.py)."""
import time

import pythoncom
import win32com.client
from mcp.server.fastmcp import FastMCP

pythoncom.CoInitialize()  # FastMCP runs sync tools on this (the main) thread
mcp = FastMCP("aethel-office")
TYPE_CHUNK = 12     # characters per keystroke burst, so typing is visible
TYPE_DELAY_S = 0.02
MAX_READ = 50_000
WD_STORY = 6
WD_FORMAT_PDF = 17
PP_LAYOUT_TEXT = 2
# Office defaults to "macros on" for files opened through automation, with no prompt. Opening a file is a read-tier
# action that needs no approval, so a .docm or .xlsm in Downloads would run its code unasked.
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3


def _app(progid: str):
    app = win32com.client.Dispatch(progid)
    app.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
    app.Visible = True
    return app


def _doc():
    word = _app("Word.Application")
    if word.Documents.Count == 0:
        raise ValueError("No Word document is open. Call word_new or word_open first.")
    return word.ActiveDocument


def _sheet(sheet: str | None):
    excel = _app("Excel.Application")
    if excel.Workbooks.Count == 0:
        raise ValueError("No workbook is open. Call excel_open first.")
    book = excel.ActiveWorkbook
    return book.Worksheets(sheet) if sheet else book.ActiveSheet


def _presentation():
    ppt = _app("PowerPoint.Application")
    if ppt.Presentations.Count == 0:
        raise ValueError("No presentation is open. Call ppt_new first.")
    return ppt.ActivePresentation


@mcp.tool()
def word_new() -> str:
    """Open a new, empty Word document."""
    _app("Word.Application").Documents.Add().Activate()
    return "Opened a new Word document."


@mcp.tool()
def word_open(path: str) -> str:
    """Open an existing Word document."""
    _app("Word.Application").Documents.Open(path).Activate()
    return f"Opened {path}."


@mcp.tool()
def word_type(text: str, style: str | None = None) -> str:
    """Type text at the end of the active Word document, visibly. Each line
    becomes a paragraph. style: a Word style for these paragraphs, e.g.
    'Title', 'Heading 1', 'Heading 2' or 'Normal'."""
    doc = _doc()
    sel = doc.Application.Selection
    sel.EndKey(WD_STORY)
    text = text.replace("\r\n", "\n").replace("\n", "\r")
    start = sel.Start
    for i in range(0, len(text), TYPE_CHUNK):
        sel.TypeText(text[i:i + TYPE_CHUNK])
        time.sleep(TYPE_DELAY_S)
    if style:
        end = sel.End - 1 if text.endswith("\r") else sel.End  # not the fresh paragraph after it
        doc.Range(start, max(start, end)).Style = style
        if text.endswith("\r"):
            sel.Style = "Normal"
    return f"Typed {len(text)} characters."


@mcp.tool()
def word_read() -> str:
    """The active Word document's text."""
    text = _doc().Content.Text.replace("\r", "\n")
    return text[:MAX_READ] + ("\n[truncated]" if len(text) > MAX_READ else "")


@mcp.tool()
def word_save(path: str) -> str:
    """Save the active Word document to path (.docx, or .pdf to export)."""
    doc = _doc()
    if path.lower().endswith(".pdf"):
        doc.SaveAs2(path, FileFormat=WD_FORMAT_PDF)
    else:
        doc.SaveAs2(path)
    return f"Saved {path}."


@mcp.tool()
def excel_open(path: str | None = None) -> str:
    """Open a workbook, or a new one when no path is given."""
    books = _app("Excel.Application").Workbooks
    book = books.Open(path) if path else books.Add()
    book.Activate()
    return f"Opened {path or 'a new workbook'}."


@mcp.tool()
def excel_read(range: str, sheet: str | None = None) -> str:
    """Cell values in a range like 'A1:C10', tab-separated."""
    values = _sheet(sheet).Range(range).Value
    rows = values if isinstance(values, tuple) else ((values,),)
    return "\n".join("\t".join("" if v is None else str(v) for v in row) for row in rows)


@mcp.tool()
def excel_write(range: str, values: list[list[str | float | int | None]], sheet: str | None = None) -> str:
    """Write rows of values starting at the top-left cell of range, e.g. 'A1'."""
    width = max((len(r) for r in values), default=0)
    if not width:
        return "Nothing to write."
    rows = [list(r) + [None] * (width - len(r)) for r in values]
    ws = _sheet(sheet)
    top = ws.Range(range)
    # Range(...).Cells(1, 1).Resize(...) misplaces the block under late-bound COM.
    ws.Range(ws.Cells(top.Row, top.Column), ws.Cells(top.Row + len(rows) - 1, top.Column + width - 1)).Value = rows
    return f"Wrote {len(rows)} rows."


@mcp.tool()
def excel_save(path: str) -> str:
    """Save the active workbook to path (.xlsx)."""
    excel = _app("Excel.Application")
    excel.DisplayAlerts = False  # overwriting was approved already; a "replace it?" box would hang this call
    try:
        excel.ActiveWorkbook.SaveAs(path)
    finally:
        excel.DisplayAlerts = True
    return f"Saved {path}."


@mcp.tool()
def ppt_new() -> str:
    """Open a new, empty presentation."""
    _app("PowerPoint.Application").Presentations.Add()
    return "Opened a new presentation."


@mcp.tool()
def ppt_add_slide(title: str, body: str = "") -> str:
    """Add a title-and-text slide at the end of the active presentation."""
    pres = _presentation()
    slide = pres.Slides.Add(pres.Slides.Count + 1, PP_LAYOUT_TEXT)
    slide.Shapes(1).TextFrame.TextRange.Text = title
    if body:
        slide.Shapes(2).TextFrame.TextRange.Text = body.replace("\n", "\r")
    return f"Added slide {slide.SlideIndex}."


@mcp.tool()
def ppt_save(path: str) -> str:
    """Save the active presentation to path (.pptx)."""
    _presentation().SaveAs(path)
    return f"Saved {path}."


if __name__ == "__main__":
    mcp.run()
