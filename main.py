# main.py
#
# Small FastAPI service providing two endpoints for CreaBeaStudio's
# photo editor: background removal and background blur. Both share the
# same underlying segmentation step (rembg) — blur just uses the mask
# differently than removal does.
#
# Deploy target: Render (free tier works fine — see deployment notes
# in README.md). Designed to be called ONLY from your Next.js API route
# (server-to-server), never directly from the browser, so CORS is
# locked down to that one trusted origin via an environment variable.

import io
import os
import threading

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image, ImageFilter

app = FastAPI(title="CreaBeaStudio Photo Tools")

# ── CORS ────────────────────────────────────────────────────────────────
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["POST"],
    allow_headers=["*"],
)

# ── MODEL SESSION — lazy-loaded, not loaded at import time ──────────────
# IMPORTANT: `rembg` itself (and the `onnxruntime` it pulls in) is now
# imported INSIDE get_session() below, not at the top of this file.
# Importing onnxruntime is slow on a CPU-starved host (Render's free
# tier gives just 0.1 CPU) — doing it at module level was eating into
# Render's startup port-scan window before uvicorn could even bind,
# causing "Port scan timeout reached, no open ports detected" even
# though the app would have started fine given more time. Keeping the
# top of this file light (just FastAPI/PIL) lets uvicorn bind almost
# immediately; the first real request pays the rembg/onnxruntime import
# cost once, lazily.
#
# "u2netp" is the deliberately lightweight model in this family (~4.7MB
# vs ~170MB for isnet-general-use/u2net) — a safer fit for 512MB RAM.
MODEL_NAME = os.environ.get("BG_MODEL_NAME", "u2netp")

_session = None
_session_lock = threading.Lock()


def get_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:  # re-check inside the lock
                from rembg import new_session  # deferred import — see note above
                _session = new_session(MODEL_NAME)
    return _session


@app.get("/health")
def health():
    """Lightweight endpoint to check the service is up (and to pre-warm
    it from your frontend before a user actually needs it, if you want)."""
    return {"status": "ok"}


@app.post("/remove-background")
async def remove_background(file: UploadFile = File(...)):
    """
    Returns the uploaded photo with its background removed — a
    transparent PNG with just the subject.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image.")

    input_bytes = await file.read()

    try:
        from rembg import remove  # deferred import — keeps startup fast
        output_bytes = remove(input_bytes, session=get_session())
    except Exception as e:
        raise HTTPException(500, f"Background removal failed: {e}")

    return Response(content=output_bytes, media_type="image/png")


@app.post("/blur-background")
async def blur_background(file: UploadFile = File(...), blur_strength: int = 18):
    """
    Returns the uploaded photo with the background blurred and the
    subject left sharp. `blur_strength` controls the Gaussian blur
    radius (higher = more blurred), default 18 is a moderate "portrait
    mode" style blur.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image.")

    input_bytes = await file.read()

    try:
        from rembg import remove  # deferred import — keeps startup fast
        # rembg's cutout PNG has the subject with full alpha and the
        # background fully transparent — the alpha channel IS the mask
        # we need, no separate mask-only call required.
        cutout_bytes = remove(input_bytes, session=get_session())
        cutout = Image.open(io.BytesIO(cutout_bytes)).convert("RGBA")
        mask = cutout.split()[3]  # alpha channel = subject mask

        original = Image.open(io.BytesIO(input_bytes)).convert("RGB")
        # rembg's output is sometimes resized slightly differently than
        # the input during processing — guard against any mismatch.
        if original.size != cutout.size:
            original = original.resize(cutout.size)

        blurred = original.filter(ImageFilter.GaussianBlur(blur_strength))

        # Sharp subject where mask is opaque, blurred elsewhere.
        result = Image.composite(original, blurred, mask)
    except Exception as e:
        raise HTTPException(500, f"Background blur failed: {e}")

    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=92)
    return Response(content=buf.getvalue(), media_type="image/jpeg")
