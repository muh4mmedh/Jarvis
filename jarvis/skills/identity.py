"""Identity — knowing who is actually sitting at the machine."""
from __future__ import annotations

import asyncio

from .. import faceid
from ..bridge import NoClient, bridge
from ..registry import SAFE, SENSITIVE, tool


async def _frame(purpose: str) -> str:
    """Ask the HUD for a camera frame."""
    result = await bridge.request("camera_frame", {"purpose": purpose}, timeout=40)
    image = (result or {}).get("image", "")
    if not image:
        raise RuntimeError("The camera returned an empty frame.")
    return image


@tool(
    description=(
        "Check whether face recognition is set up: whether the models are present, "
        "whether a face is enrolled, and who it belongs to. Use before enrolling or "
        "when the user asks about face unlock."
    ),
    category="identity",
    risk=SAFE,
)
def face_status() -> dict:
    st = faceid.status()
    st["note"] = (
        "A webcam face match is a presence check, not a security boundary — it can "
        "be fooled by a photograph. The permission layer remains the real protection."
    )
    return st


@tool(
    description=(
        "Enrol the user's face so you can recognise them later. Takes several photos "
        "from the webcam a moment apart. Use when the user asks you to learn their "
        "face, set up face unlock, or recognise them. Downloads the recognition "
        "models on first use (about 40 MB, one time)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "What to call this person, e.g. their name."},
            "samples": {"type": "integer", "description": "How many photos to take. Default 5."},
        },
    },
    category="identity",
    risk=SENSITIVE,
    confirm_hint="Takes several photos with your webcam and stores a mathematical "
                 "signature of your face locally. No images are kept.",
)
async def enrol_face(name: str = "", samples: int = 5) -> dict:
    if not faceid.models_present():
        try:
            await asyncio.to_thread(faceid.download_models)
        except faceid.FaceError as exc:
            return {"error": str(exc)}

    n = max(1, min(int(samples or 5), 8))
    captured, failures = 0, []

    for i in range(n):
        try:
            image = await _frame(f"enrolment photo {i + 1} of {n}")
            info = await asyncio.to_thread(faceid.add_sample, image, name)
            captured = info["samples"]
        except (NoClient, RuntimeError, faceid.FaceError) as exc:
            failures.append(str(exc))
        if i < n - 1:
            await asyncio.sleep(1.1)      # let the user shift slightly between shots

    if not captured:
        return {"error": "Could not enrol a face.", "details": failures[:3]}

    return {
        "ok": True,
        "name": name,
        "samples_stored": captured,
        "failed_captures": len(failures),
        "message": f"Face enrolled with {captured} samples.",
        "caveat": "This is a presence check, not a security boundary — a photograph "
                  "can defeat it.",
    }


@tool(
    description=(
        "Look through the webcam and confirm whether the person in front of it is the "
        "enrolled user. Use when asked 'do you recognise me', 'who am I', or to check "
        "identity before something sensitive."
    ),
    category="identity",
    risk=SAFE,
)
async def verify_face() -> dict:
    if not faceid.enrolled():
        return {"error": "No face is enrolled. Ask me to enrol your face first.",
                "enrolled": False}
    try:
        image = await _frame("identity check")
    except NoClient as exc:
        return {"error": str(exc)}
    except (TimeoutError, RuntimeError) as exc:
        return {"error": f"Camera unavailable: {exc}"}

    try:
        return await asyncio.to_thread(faceid.verify, image)
    except faceid.FaceError as exc:
        return {"error": str(exc), "match": False}


@tool(
    description="Delete the enrolled face signature from this machine.",
    category="identity",
    risk=SENSITIVE,
    confirm_hint="Permanently removes the stored face signature.",
)
def forget_face() -> dict:
    removed = faceid.forget()
    return {"ok": removed,
            "message": "Face signature deleted." if removed else "Nothing was enrolled."}
