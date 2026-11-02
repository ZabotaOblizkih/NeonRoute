"""
FastAPI application for NeonRoute Mobile Server.
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from mobile_server.routes import system, sessions, chat, characters, art, media

app = FastAPI(
    title="NeonRoute Mobile Gateway",
    description="Headless Backend-for-Frontend (BFF) REST/SSE API for mobile clients",
    version="1.0.0",
)

# CORS Configuration for local network and VPN (Tailscale)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["Sessions"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(characters.router, prefix="/api/characters", tags=["Characters"])
app.include_router(art.router, prefix="/api/art", tags=["Art"])
app.include_router(media.router, prefix="/media", tags=["Media"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": "NeonRoute Mobile Gateway"}


# Mount static assets and serve root index.html
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(static_dir / "index.html")
