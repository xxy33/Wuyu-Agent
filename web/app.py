"""FastAPI main application."""
import uvicorn
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

from web.config import HOST, PORT
from web.routers import upload_router, detection_router, results_router

# Create FastAPI app
app = FastAPI(
    title="多领域遥感检测系统",
    description="Multi-domain Remote Sensing Detection System",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get base directory
BASE_DIR = Path(__file__).parent

# Custom StaticFiles to disable caching in development
class NoCacheStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        # Call parent to handle the request
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                # Add no-cache headers for development
                headers.append((b"cache-control", b"no-cache, no-store, must-revalidate"))
                headers.append((b"pragma", b"no-cache"))
                headers.append((b"expires", b"0"))
                message["headers"] = headers
            await send(message)

        await super().__call__(scope, receive, send_wrapper)

# Mount static files with no-cache headers
app.mount("/static", NoCacheStaticFiles(directory=BASE_DIR / "static"), name="static")

# Setup templates
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Include routers
app.include_router(upload_router)
app.include_router(detection_router)
app.include_router(results_router)


@app.get("/")
async def index(request: Request):
    """Render main page."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "version": datetime.now().timestamp()  # 添加时间戳避免缓存
        }
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "multi-domain-detection"}


if __name__ == "__main__":
    uvicorn.run(
        "web.app:app",
        host=HOST,
        port=PORT,
        reload=True
    )
