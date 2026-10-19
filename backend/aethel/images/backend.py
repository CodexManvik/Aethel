"""What every image backend offers, and the choice between them."""
from dataclasses import dataclass, field
from typing import Literal, Protocol

Size = Literal["portrait", "square", "landscape"]
ASPECT = {"portrait": "3:4", "square": "1:1", "landscape": "4:3"}


class ImageError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class ImageRequest:
    prompt: str
    references: list[bytes] = field(default_factory=list)  # JPEG bytes: the anchor first, then the sheet
    size: Size = "portrait"


@dataclass
class ImageResult:
    data: bytes
    mime: str
    backend: str
    model: str
    latency_ms: int


class ImageBackend(Protocol):
    name: str
    cloud: bool
    max_references: int

    async def available(self) -> tuple[bool, str]:
        """(ok, why not)."""
        ...

    async def generate(self, req: ImageRequest) -> ImageResult:
        """Raises ImageError."""
        ...


async def pick_backend(order: list[str], backends: dict[str, ImageBackend], private: bool) -> ImageBackend | None:
    """The first available backend in the settings' order. Private mode: only a backend that runs on this PC."""
    for name in order:
        backend = backends.get(name)
        if backend is None or (private and backend.cloud):
            continue
        ok, _ = await backend.available()
        if ok:
            return backend
    return None
