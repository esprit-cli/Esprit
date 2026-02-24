"""
Esprit Backend - Orchestrator Service

FastAPI application for managing:
- Sandbox creation/destruction (AWS ECS)
- LLM proxy (users don't need API keys)
- Usage tracking and rate limiting
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings

settings = get_settings()

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Esprit Backend", environment=settings.environment)
    yield
    logger.info("Shutting down Esprit Backend")


app = FastAPI(
    title="Esprit Backend API",
    description="Orchestrator service for Esprit SaaS platform",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS configuration
# Allow specific origins + any Vercel preview deployments
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "https://esprit.dev",
]


def is_allowed_origin(origin: str) -> bool:
    """Check if origin is allowed (includes Vercel deployments)."""
    if origin in ALLOWED_ORIGINS:
        return True
    # Allow any Vercel deployment
    if origin and (".vercel.app" in origin or ".esprit.dev" in origin):
        return True
    return False


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class DynamicCORSMiddleware(BaseHTTPMiddleware):
    """CORS middleware that allows dynamic origin validation."""

    async def dispatch(self, request: Request, call_next):
        from starlette.responses import Response

        origin = request.headers.get("origin", "")

        # Handle preflight - return direct 200 response for OPTIONS
        if request.method == "OPTIONS":
            if is_allowed_origin(origin):
                return Response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Credentials": "true",
                        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
                        "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept",
                        "Access-Control-Max-Age": "86400",
                    }
                )
            # If origin not allowed, return 403
            return Response(status_code=403)

        response = await call_next(request)

        if is_allowed_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"

        return response


app.add_middleware(DynamicCORSMiddleware)

# Include API routes
app.include_router(router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Esprit Backend",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
