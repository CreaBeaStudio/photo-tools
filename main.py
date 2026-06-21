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

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image, ImageFilter
from rembg import remove, new_session

app = FastAPI(title="CreaBeaStudio Photo Tools")

# ── CORS ────────────────────────────────────────────────────────────────
# Locked to your own Next.js server's origin. Set ALLOWED_ORIGIN in
# Render's environment variables (e.g. "https://creabeastudio.com").
# Falls back to "*" only if unset, so local testing doesn't break —
# tighten this before going live.
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["POST"],
    allow_headers=["*"],
)

# rembg model session — created once at startup, reused across requests.
# "isnet-general-use" is a good general-purpose model for photos of
# people, pets, and objects. First request after a fresh deploy/cold
# start will be slower while the model file downloads (~170MB) and loads
# into memory; subsequent requests are fast.
_session = new_session("isnet-general-use")


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
        output_bytes = remove(input_bytes, session=_session)
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
        cutout_bytes = remove(input_bytes, session=_session)
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
