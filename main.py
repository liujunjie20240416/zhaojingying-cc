import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# ── Load .env ──
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from ai.tracing import configure_langsmith_defaults

configure_langsmith_defaults()

# ── Django setup for ORM ──
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_settings")
import django

django.setup()

# ── FastAPI app ──
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.wsgi import WSGIMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount Django Admin via WSGI ──
from django.core.handlers.wsgi import WSGIHandler

app.mount("/admin", WSGIMiddleware(WSGIHandler()))

# ── Mount static / media files ──
assets_dir = PROJECT_ROOT / "static" / "frontend" / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

media_dir = PROJECT_ROOT / "media"
media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")

# Django admin static files
import django.contrib.admin as admin_module

admin_static = Path(admin_module.__file__).parent / "static" / "admin"
app.mount(
    "/static/admin",
    StaticFiles(directory=str(admin_static)),
    name="admin-static",
)

# ── API routes ──
from api.auth import router as auth_router
from api.user import router as user_router
from api.character import router as character_router
from api.friend import router as friend_router
from api.chat import router as chat_router
from api.message import router as message_router
from api.asr import router as asr_router
from api.homepage import router as homepage_router
from api.import_data import router as import_router
from api.memory import router as memory_router
from api.voice import router as voice_router

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(character_router)
app.include_router(friend_router)
app.include_router(chat_router)
app.include_router(message_router)
app.include_router(asr_router)
app.include_router(homepage_router)
app.include_router(import_router)
app.include_router(memory_router)
app.include_router(voice_router)

# ── SPA fallback ──
index_path = PROJECT_ROOT / "static" / "frontend" / "index.html"


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith(("api/", "admin/", "media/", "assets/", "static/")):
        return HTMLResponse(status_code=404)
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>Frontend not built</h1><p>Run: cd frontend && npm run build</p>",
        status_code=404,
    )
