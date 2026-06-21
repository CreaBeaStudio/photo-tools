# CreaBeaStudio Photo Tools — Background Removal Service

A small FastAPI service providing background removal and background
blur, powered by `rembg`. Deploy this as its OWN separate service on
Render — it does not live inside your Next.js project.

## Deploying to Render (free tier)

1. Push this folder (`main.py` + `requirements.txt`) to its own new
   GitHub repository — e.g. `creabeastudio-photo-tools`.
2. In Render: **New +** → **Web Service** → connect that repo.
3. Settings:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: Free (upgrade to Starter, $7/mo, later if you
     want to eliminate cold starts)
4. Add an environment variable:
   - `ALLOWED_ORIGIN` = `https://creabeastudio.com`
   (This locks the service down so only your own site can call it.)
5. Deploy. The first request after each cold start will be slow
   (~30-60 seconds) while the ONNX model downloads and loads into
   memory — this is normal and only happens after 15+ minutes of
   inactivity on the free tier.
6. Once live, note the service's URL (something like
   `https://creabeastudio-photo-tools.onrender.com`) — you'll need it
   in your Next.js project's environment variables as
   `BG_SERVICE_URL`.

## Endpoints

- `GET /health` — returns `{"status": "ok"}`. Useful for an uptime
  check, or to "pre-warm" the service from your frontend before a user
  actually clicks a button.
- `POST /remove-background` — form-data field `file` (the image).
  Returns a transparent PNG.
- `POST /blur-background` — form-data field `file` (the image),
  optional `blur_strength` (int, default 18). Returns a JPEG.

## Testing locally before deploying

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Then in another terminal:
```bash
curl -X POST http://localhost:8000/remove-background \
  -F "file=@/path/to/test-photo.jpg" \
  --output result.png
```

## Note on the underlying model

This uses rembg's `isnet-general-use` model — a solid general-purpose
choice for photos of people, pets, and objects (exactly your use
case). The model file (~170MB) downloads automatically on first
startup from rembg's GitHub releases. No further configuration needed.
