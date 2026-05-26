"""StackBot FastAPI entry point and ``stackbot`` console script.

``run()`` is what pip's ``[project.scripts] stackbot`` points at. It loads the
config, builds the FastAPI app with state attached, runs the preflight
sequence, and serves uvicorn programmatically.

Graceful shutdown drains outstanding background tasks for up to
``operations.shutdown_drain_timeout_seconds`` before exiting.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from stack_bot import redis_client as redis_helper
from stack_bot import startup
from stack_bot.config import BotConfig, load_config
from stack_bot.webhooks import ado as webhook_ado
from stack_core.ado.client import AdoClient

logger = logging.getLogger(__name__)


def build_app(config: BotConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        tasks = list(app.state.tasks)
        if not tasks:
            return
        timeout = config.operations.shutdown_drain_timeout_seconds
        logger.info("draining %d background tasks (timeout=%ds)", len(tasks), timeout)
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)
        except TimeoutError:
            logger.warning("background-task drain timed out")

    app = FastAPI(title="stackbot", lifespan=lifespan)
    app.state.config = config
    app.state.redis_client = redis_helper.connect(config.redis)
    app.state.tasks = set()
    app.include_router(webhook_ado.router)
    return app


def _logging_setup(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))


def run() -> None:
    """Console-script entry point."""
    parser = argparse.ArgumentParser(prog="stackbot")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Load and validate the config; exit 0 on success, non-zero on failure.",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except Exception as exc:
        print(f"config error: {exc}", flush=True)
        raise SystemExit(startup.EXIT_CONFIG_INVALID) from exc

    if args.check_config:
        print(f"config ok: {len(config.projects)} project(s)")
        return

    _logging_setup(config.logging.level)
    app = build_app(config)

    ado_client = AdoClient(config.ado.organization_url, config.ado.pat)
    try:
        startup.run_preflight(config, app.state.redis_client, ado_client)
    finally:
        ado_client.close()

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_config=None,
    )
