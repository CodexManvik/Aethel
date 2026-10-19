"""Is this the same person? YuNet finds the face and its five landmarks; the face is aligned to ArcFace's 112×112
template by a similarity transform; ArcFace gives a 512-d embedding; the cosine against the anchor portrait's
embedding is the identity score (spec §10.3). Both models are ONNX on the CPU."""
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .models import is_installed, model_path

MIN_FACE_SCORE = 0.8
TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],  # eyes, nose, mouth corners
                     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)       # (image-left first)
SIDE = 112


@dataclass
class Face:
    box: tuple[int, int, int, int]  # x, y, w, h
    landmarks: np.ndarray           # 5×2: the eye, eye, nose, mouth corner, mouth corner, image-left first
    score: float


def decode(data: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def align(image: np.ndarray, face: Face) -> np.ndarray:
    """The face warped onto the 112×112 template (BGR)."""
    matrix, _ = cv2.estimateAffinePartial2D(face.landmarks.astype(np.float32), TEMPLATE, method=cv2.LMEDS)
    if matrix is None:
        raise ValueError("the landmarks don't fit a face")
    return cv2.warpAffine(image, matrix, (SIDE, SIDE), borderValue=0.0)


class IdentityCheck:
    def __init__(self, models_dir: Path | None = None, *, detector=None, session=None):
        self.models_dir = models_dir
        self._detector = detector   # tests pass fakes; otherwise loaded on first use
        self._session = session
        self._lock = threading.Lock()

    def ready(self) -> tuple[bool, str]:
        if self._detector is not None and self._session is not None:
            return True, ""
        missing = [n for n in ("yunet", "arcface") if not is_installed(n, self.models_dir)]
        if missing:
            return False, "The face models aren't downloaded yet (about 250 MB)."
        return True, ""

    def _load(self):
        with self._lock:
            if self._detector is None:
                self._detector = cv2.FaceDetectorYN.create(str(model_path("yunet", self.models_dir)), "", (320, 320),
                                                           MIN_FACE_SCORE)
            if self._session is None:
                import onnxruntime as ort
                self._session = ort.InferenceSession(str(model_path("arcface", self.models_dir)),
                                                     providers=["CPUExecutionProvider"])

    def detect(self, image: np.ndarray) -> list[Face]:
        self._load()
        h, w = image.shape[:2]
        with self._lock:  # FaceDetectorYN holds its input size: one image at a time
            self._detector.setInputSize((w, h))
            _, found = self._detector.detect(image)
        faces = []
        for row in found if found is not None else []:
            if float(row[-1]) >= MIN_FACE_SCORE:
                faces.append(Face(tuple(int(v) for v in row[:4]), np.asarray(row[4:14], dtype=np.float32).reshape(5, 2),
                                  float(row[-1])))
        return faces

    def embed(self, image: np.ndarray, face: Face) -> np.ndarray:
        """L2-normalised. ArcFace (ONNX Model Zoo) takes RGB, 0–255, NCHW."""
        self._load()
        crop = cv2.cvtColor(align(image, face), cv2.COLOR_BGR2RGB).astype(np.float32)
        feed = {self._session.get_inputs()[0].name: np.transpose(crop, (2, 0, 1))[None]}
        vec = np.asarray(self._session.run(None, feed)[0], dtype=np.float32).ravel()
        return vec / max(float(np.linalg.norm(vec)), 1e-9)

    def embedding_of(self, data: bytes) -> np.ndarray | None:
        """The largest face's embedding, or None when there's no face (scenery, the back of a head)."""
        image = decode(data)
        if image is None:
            return None
        faces = self.detect(image)
        if not faces:
            return None
        return self.embed(image, max(faces, key=lambda f: f.box[2] * f.box[3]))

    def compare(self, candidate: bytes, anchor_embedding: np.ndarray) -> float | None:
        vec = self.embedding_of(candidate)
        return None if vec is None else float(np.dot(vec, anchor_embedding))
