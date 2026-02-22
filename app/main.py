import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from dotenv import load_dotenv

from app.config import BASE_DIR, ALLOWED_API_KEYS, CONSOLE_TOKEN

load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if not ALLOWED_API_KEYS:
        logger.warning("SECURITY WARNING: OPENAI_KEYS is not set - API is accessible without authentication!")
    if not CONSOLE_TOKEN:
        logger.warning("SECURITY WARNING: CONSOLE_TOKEN is not set - console endpoints will reject all requests")

    from db import init_db
    await init_db()

    from app.http_client import init_http_client, recycle_http_client
    await init_http_client()
    asyncio.create_task(recycle_http_client())

    from account_pool import get_pool
    pool = get_pool()
    from app.services.accounts import list_enabled_accounts
    accounts = await list_enabled_accounts()
    await pool.reload(accounts)

    yield

    # Shutdown
    from db import close_db
    await close_db()

    from app.http_client import close_http_client
    await close_http_client()

app = FastAPI(title="v2 OpenAI-compatible Server (Amazon Q Backend)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.error(f"Validation error on {request.method} {request.url.path}")
    logger.error(f"Validation errors: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": "Invalid request"}
    )

from app.routes import messages, models
app.include_router(messages.router)
app.include_router(models.router)
