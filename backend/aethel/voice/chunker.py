"""Turns a streamed reply into sentences to speak, as soon as each is complete.

The sentences are the same however the text is cut into deltas: a boundary is only decided once the character after
it has arrived, and a too-long sentence is split from its first `max_chars` characters alone."""
ENDERS = ".!?…"
CLOSERS = "\"')]}”’»"
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "approx.",
                 "no.", "fig.", "inc.", "ltd."}


def _is_abbreviation(token: str) -> bool:
    token = token.lstrip("\"'([{“‘")
    if token.lower() in ABBREVIATIONS:
        return True
    return len(token) == 2 and token[0].isalpha() and token[1] == "." and token[0].isupper()  # an initial: "J."


def _boundary(buf: str) -> tuple[int, int] | None:
    """(where the first complete sentence ends, how much of the buffer it consumes), or None if it isn't decided yet."""
    i, n = 0, len(buf)
    while i < n:
        ch = buf[i]
        if ch == "\n":
            return i, i + 1
        if ch in ENDERS:
            if i + 1 >= n:
                return None
            if buf[i + 1] in ENDERS:
                i += 1  # "?!" or "...": the run isn't over
                continue
            j = i + 1
            while j < n and buf[j] in CLOSERS:
                j += 1
            if j >= n:
                return None
            if buf[j].isspace():
                start = buf.rfind(" ", 0, i) + 1
                start = max(start, buf.rfind("\n", 0, i) + 1)
                run_is_single = i == 0 or buf[i - 1] not in ENDERS
                if not (ch == "." and run_is_single and _is_abbreviation(buf[start:i + 1])):
                    return j, j + 1
            i = j
            continue
        i += 1
    return None


class SentenceChunker:
    def __init__(self, min_chars: int = 12, max_chars: int = 220):
        self.min_chars, self.max_chars = min_chars, max_chars
        self._buf = ""
        self._pending = ""  # a sentence too short to speak alone, waiting for the next

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        return self._drain(final=False)

    def flush(self) -> list[str]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[str]:
        out: list[str] = []
        while True:
            found = _boundary(self._buf)
            if found is not None and found[0] <= self.max_chars:
                end, consumed = found
                sentence, self._buf = self._buf[:end], self._buf[consumed:]
            elif len(self._buf) > self.max_chars or (found is not None and found[0] > self.max_chars):
                sentence, self._buf = self._split_long()
            else:
                break
            self._emit(sentence, out)
        if final:
            self._emit(self._buf, out, last=True)
            self._buf = ""
        return out

    def _split_long(self) -> tuple[str, str]:
        head = self._buf[:self.max_chars]
        cut = max(head.rfind(","), head.rfind(";"))
        if cut < self.max_chars // 3:
            cut = head.rfind(" ")
        if cut <= 0:
            cut = self.max_chars - 1
        return self._buf[:cut + 1], self._buf[cut + 1:].lstrip()

    def _emit(self, sentence: str, out: list[str], last: bool = False) -> None:
        sentence = " ".join(f"{self._pending} {sentence}".split())
        self._pending = ""
        if not sentence:
            return
        if len(sentence) < self.min_chars and not last:
            self._pending = sentence
        else:
            out.append(sentence)
