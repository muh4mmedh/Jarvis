"""Face recognition, on-device.

Two ONNX models from the OpenCV Zoo do the work: YuNet finds faces, SFace turns
one into a 128-dimension embedding. Enrolment stores a handful of embeddings of
your face; verification embeds a fresh camera frame and compares by cosine
similarity. Nothing leaves the machine and no image is ever stored.

HONEST LIMITS — read before relying on this:
  · A webcam face match is a *presence check*, not a security boundary. It can
    be defeated by a photograph or a video on a phone screen. There is no
    liveness detection and no anti-spoofing here.
  · Windows Hello with an infrared camera is the secure version of this. This
    machine has no IR camera, so that route is unavailable.
  · Treat it as "is Sir the one sitting here?" — a convenience that removes
    friction — and never as the only thing standing between an attacker and a
    destructive command. The permission layer and blocklist remain the real
    protection.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

from .config import cfg

log = logging.getLogger("jarvis.faceid")

MODELS = cfg.resolve("data/models")
PROFILE = cfg.resolve("data/faceid.json")

# OpenCV Zoo. Small, permissively licensed, and stable URLs.
DETECTOR_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
                "face_detection_yunet/face_detection_yunet_2023mar.onnx")
RECOGNISER_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
                  "face_recognition_sface/face_recognition_sface_2021dec.onnx")

DETECTOR_FILE = MODELS / "face_detection_yunet.onnx"
RECOGNISER_FILE = MODELS / "face_recognition_sface.onnx"

# SFace's own recommended threshold for cosine similarity.
COSINE_THRESHOLD = 0.363

# Detection is markedly more reliable once the frame is scaled down.
MAX_EDGE = 800

_detector = None
_recogniser = None


class FaceError(RuntimeError):
    pass


# ── model files ─────────────────────────────────────────────────────
def models_present() -> bool:
    return DETECTOR_FILE.exists() and RECOGNISER_FILE.exists()


def download_models(progress=None) -> None:
    """Fetch the two ONNX files. ~40 MB, once."""
    import httpx

    MODELS.mkdir(parents=True, exist_ok=True)
    for url, dest in ((DETECTOR_URL, DETECTOR_FILE), (RECOGNISER_URL, RECOGNISER_FILE)):
        if dest.exists():
            continue
        if progress:
            progress(f"downloading {dest.name}")
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=180) as r:
                r.raise_for_status()
                tmp = dest.with_suffix(".part")
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 16):
                        fh.write(chunk)
                tmp.replace(dest)
        except Exception as exc:
            dest.with_suffix(".part").unlink(missing_ok=True)
            raise FaceError(f"Could not download {dest.name}: {exc}")


def _engines():
    """Lazy-load the models; they cost memory and most sessions never need them."""
    global _detector, _recogniser
    if _detector is not None and _recogniser is not None:
        return _detector, _recogniser

    try:
        import cv2
    except ImportError:
        raise FaceError("Face recognition needs opencv-python — run: pip install opencv-python")

    if not models_present():
        raise FaceError("Face models are not downloaded yet.")

    # 0.6 rather than the 0.9 default: a webcam frame is noisier than the
    # benchmark images the default was tuned on, and a missed face here means
    # the user is silently not recognised.
    _detector = cv2.FaceDetectorYN.create(str(DETECTOR_FILE), "", (320, 320),
                                          score_threshold=0.6)
    _recogniser = cv2.FaceRecognizerSF.create(str(RECOGNISER_FILE), "")
    return _detector, _recogniser


# ── embeddings ──────────────────────────────────────────────────────
def _decode(image_b64: str):
    import cv2
    import numpy as np

    raw = base64.b64decode(image_b64)
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise FaceError("That frame could not be decoded as an image.")
    return img


def _embed(image_b64: str):
    """Return (embedding, face_box) for the largest face in the frame."""
    import numpy as np

    import cv2

    detector, recogniser = _engines()
    img = _decode(image_b64)

    # YuNet degrades badly on very large frames — a 2048px photo yields
    # phantom detections, while the same image at 800px yields exactly one.
    # Downscale first and do everything downstream on the scaled copy so the
    # detection boxes and the aligned crop stay in the same coordinate space.
    h, w = img.shape[:2]
    if max(h, w) > MAX_EDGE:
        scale = MAX_EDGE / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]

    detector.setInputSize((w, h))

    _, faces = detector.detect(img)
    if faces is None or len(faces) == 0:
        raise FaceError("No face found in the frame.")

    # Largest face wins — that is the person sitting at the machine.
    faces = sorted(faces, key=lambda f: -(f[2] * f[3]))
    face = faces[0]
    aligned = recogniser.alignCrop(img, face)
    feature = recogniser.feature(aligned)
    return np.asarray(feature).flatten().tolist(), {
        "x": int(face[0]), "y": int(face[1]),
        "w": int(face[2]), "h": int(face[3]),
        "faces_in_frame": len(faces),
    }


def _cosine(a, b) -> float:
    import numpy as np

    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(np.dot(va, vb) / denom) if denom else 0.0


# ── enrolled profile ────────────────────────────────────────────────
def load_profile() -> dict:
    if not PROFILE.exists():
        return {}
    try:
        return json.loads(PROFILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_profile(data: dict) -> None:
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(PROFILE)


def enrolled() -> bool:
    return bool(load_profile().get("embeddings"))


def status() -> dict:
    profile = load_profile()
    return {
        "models_downloaded": models_present(),
        "enrolled": bool(profile.get("embeddings")),
        "name": profile.get("name", ""),
        "samples": len(profile.get("embeddings", [])),
        "enrolled_at": profile.get("enrolled_at", ""),
        "threshold": profile.get("threshold", COSINE_THRESHOLD),
    }


def add_sample(image_b64: str, name: str = "") -> dict:
    """Add one frame to the enrolled profile."""
    embedding, box = _embed(image_b64)
    profile = load_profile()
    profile.setdefault("embeddings", []).append(embedding)
    profile["name"] = name or profile.get("name", "")
    profile["enrolled_at"] = time.strftime("%Y-%m-%d %H:%M")
    profile.setdefault("threshold", COSINE_THRESHOLD)
    # More than a handful adds nothing but false accepts.
    profile["embeddings"] = profile["embeddings"][-8:]
    _save_profile(profile)
    return {"samples": len(profile["embeddings"]), "face": box}


def forget() -> bool:
    if PROFILE.exists():
        PROFILE.unlink()
        return True
    return False


def verify(image_b64: str) -> dict:
    """Compare a frame against the enrolled profile."""
    profile = load_profile()
    stored = profile.get("embeddings") or []
    if not stored:
        raise FaceError("No face is enrolled yet.")

    embedding, box = _embed(image_b64)
    scores = [_cosine(embedding, ref) for ref in stored]
    best = max(scores)
    threshold = float(profile.get("threshold", COSINE_THRESHOLD))

    return {
        "match": best >= threshold,
        "score": round(best, 4),
        "threshold": threshold,
        "confidence": round(min(best / threshold, 1.0) * 100, 1) if threshold else 0.0,
        "name": profile.get("name", ""),
        "face": box,
        "samples_compared": len(stored),
    }
