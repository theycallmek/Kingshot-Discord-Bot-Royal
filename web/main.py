"""
Main entry point for the FastAPI web application.

This module initializes the FastAPI application, includes the necessary routers
for handling authentication, serving pages, and providing API endpoints. It
also sets up a lifespan event to initialize the database.
"""

import contextlib

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from web.core.config import SECRET_KEY
from web.core.database import initialize_ocr_database, enable_wal_mode
from web.routers import auth, pages, api


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan event handler.

    Initializes the database by creating OCR tables if they don't exist
    and enables WAL mode for all database connections to allow for
    concurrent read/write access.
    """
    initialize_ocr_database()
    enable_wal_mode()
    yield


app = FastAPI(lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# Add session middleware
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Include routers
app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(api.router)
