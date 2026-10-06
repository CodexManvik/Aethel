"""Hand-written short descriptions for the verbose upstream tools (Windows-MCP 0.8.6, Desktop Commander 0.2.51).

Written by reading the upstream text and each tool's schema: every parameter keeps its meaning and its value
set. They're code, not generated at runtime, so a package upgrade can't change what the model is told
without a review (the tests fail if a tool is renamed or a parameter is dropped from its text).
Left out on purpose: tools whose upstream description is already one line (Office, fs_*, shell, open_url).
Desktop Commander's upstream text invites fetching URLs; Aethel hides that parameter, so these don't."""

SHORT: dict[str, str] = {
    "win_snapshot": (
        "Snapshot the screen as text: focused and open windows, interactive elements (role, name, centre "
        "coordinates) and scrollable areas. Call it first to see the desktop before acting. use_ui_tree=false is "
        "a faster snapshot without elements; use_dom=true lists a web page's elements instead of the browser's "
        "own UI; display=[0] or [0, 1] picks monitors (zero-based); region=[left, top, right, bottom] limits it "
        "to a rectangle in screen pixels, which saves tokens. use_annotation only changes screenshots, which you "
        "don't receive."),
    "win_app": (
        "Open and manage app windows. mode: 'launch' (start an app by its Start Menu name), "
        "'launch_executable' (run one executable path: executable, with optional args and cwd), 'switch' (focus "
        "the window called name) or 'resize' (the window called name, or the active one: window_size=[width, "
        "height], window_loc=[x, y])."),
    "win_click": (
        "Click at loc=[x, y], a point from win_snapshot. button: left (default), right or middle. clicks: "
        "0 = hover only, 1 = single click (default), 2 = double click."),
    "win_type": (
        "Type text. With loc=[x, y] it clicks that field first. clear=true replaces the field's existing text; "
        "caret_position: start, end or idle (default); press_enter=true submits after typing."),
    "win_scroll": (
        "Scroll at loc=[x, y] (default: where the mouse is). type: vertical (default) or horizontal; "
        "direction: up or down for vertical, left or right for horizontal; wheel_times: notches "
        "(1 is about 3-5 lines)."),
    "win_move": (
        "Move the mouse to loc=[x, y] (a hover). To drag, set drag=true (from where the mouse is now to loc) or "
        "give from_loc=[x, y] to start the drag from an explicit point in one call; duration is the movement "
        "time in seconds."),
    "win_shortcut": (
        "Press a key combination, joined with +, in shortcut: e.g. 'ctrl+c', 'ctrl+s', 'alt+tab', 'win+r', "
        "'win', 'enter'."),
    "win_wait": "Pause for duration seconds, e.g. for an app to load or a dialog to appear.",
    "win_wait_for": (
        "Wait until a condition holds, polling inside the tool rather than with repeated snapshots. condition: "
        "text_exists, active_window, element_exists, element_enabled or focused_element; give text and/or "
        "window_name to match. timeout in seconds (default 10), interval in seconds (default 0.25); "
        "use_dom=true checks a web page's text."),
    "win_multi_select": (
        "Select several items at once: locs=[[x, y], ...]. press_ctrl=true (default) holds ctrl so they stay "
        "selected; false just clicks each in turn."),
    "win_multi_edit": "Type into several fields in one call: locs=[[x, y, text], ...].",
    "win_clipboard": "Read or set the clipboard. mode: get (read it) or set (write text to it).",
    "win_displays": (
        "List the monitors: index, name, bounds, resolution, orientation, primary flag, DPI and scale."),
    "dc_read_file": (
        "Read a file. Text: offset is the first line (negative = the last N lines), length the most lines "
        "(default 1000). Excel: sheet (name, or index as a string) and range as FROM:TO, e.g. 'A1:D100'. PDF: "
        "text as markdown, offset and length count pages. DOCX: an outline by default; a non-zero offset (with "
        "length) returns raw XML, the text dc_edit_block edits. Images come back viewable. Absolute paths only. "
        "options: extra settings."),
    "dc_search": (
        "Start a background search under path and get a session ID; read results with dc_search_more, cancel "
        "with dc_search_stop. searchType: 'files' (default; pattern matches file names) or 'content' (pattern "
        "matches text inside files). pattern is a regex unless literalSearch=true, which you want for code "
        "containing . * + ? ^ $ { } [ ] | \\ ( ). filePattern limits the files, e.g. '*.js|*.ts'; ignoreCase "
        "(default true); includeHidden; maxResults; contextLines (default 5, content searches); timeout_ms; "
        "earlyTermination stops at an exact file-name match. If unsure which is meant, run both."),
    "dc_search_more": (
        "Results of a dc_search by sessionId, with the search's status. offset: the first result (negative = "
        "the last N); length: the most results (default 100)."),
    "dc_search_stop": "Stop a running dc_search (sessionId). Its results stay readable for 5 minutes.",
    "dc_file_info": (
        "Metadata of a file or folder: size, created and modified times, permissions, type; for text files the "
        "line count, last line and append position; for Excel files the sheets (name, rows, columns). "
        "Absolute paths only."),
    "dc_edit_block": (
        "Edit a file with small, focused changes. Text: replace old_string with new_string, with just enough "
        "context to be unique; expected_replacements (default 1) is how many matches you expect. Excel: range "
        "as 'Sheet1!A1:C10' and content as a 2D array. DOCX: the same find/replace, on the XML that "
        "dc_read_file returns with offset and length. options: extra settings."),
}
