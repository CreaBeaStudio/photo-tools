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
from rembg import remove, new_session

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
# IMPORTANT: loading the model at module import time (before the server
# can bind to its port) risks the process being killed for exceeding
# memory on a constrained host (e.g. Render's free 512MB tier) before it
# ever finishes starting — which looks like an infinite "waking up"
# crash loop, since the platform just keeps restarting it at the same
# point. Loading lazily on first request lets the server start
# responding immediately; only the first real request pays the cost of
# loading the model.
#
# "u2netp" is the deliberately lightweight model in this family (~4.7MB
# vs ~170MB for isnet-general-use/u2net) — a much safer fit for a
# 512MB-RAM host. Swap to a heavier model later if you upgrade to a
# higher-memory paid tier and want better edge quality.
MODEL_NAME = os.environ.get("BG_MODEL_NAME", "u2netp")

_session = None
_session_lock = threading.Lock()


def get_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:  # re-check inside the lock
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
