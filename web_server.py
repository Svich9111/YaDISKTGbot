"""
HTTP web server for Render.com health checks.
Render requires a service that binds to $PORT and responds to HTTP requests.
"""
import time
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import Update
import config
from loguru import logger

# Global state tracking
_bot_running = False
_db_healthy = False
_last_health_check = 0


def set_bot_running(running: bool):
    global _bot_running
    _bot_running = running


def set_db_healthy(healthy: bool):
    global _db_healthy
    _db_healthy = healthy


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint for Render monitoring."""
    global _last_health_check
    _last_health_check = time.time()

    status = {
        "status": "ok",
        "bot_running": _bot_running,
        "db_healthy": _db_healthy,
    }

    # If bot is not running, return 503
    if not _bot_running:
        status["status"] = "degraded"
        return web.json_response(status, status=503)

    return web.json_response(status)


async def ready_handler(request: web.Request) -> web.Response:
    """Readiness probe - returns 200 when bot is fully initialized."""
    if _bot_running and _db_healthy:
        return web.json_response({"ready": True})
    return web.json_response({"ready": False}, status=503)


async def root_handler(request: web.Request) -> web.Response:
    """Root endpoint."""
    return web.json_response(
        {
            "service": "yandex-disk-tg-bot",
            "version": "1.0.0",
            "endpoints": ["/health", "/ready", "/webhook"],
        },
    )


async def webhook_handler(request: web.Request) -> web.Response:
    """Handle incoming Telegram updates via webhook."""
    bot: Bot = request.app["bot"]
    dp: Dispatcher = request.app["dp"]

    try:
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}")
        return web.Response(status=500)


def create_app(bot: Bot = None, dp: Dispatcher = None) -> web.Application:
    """Create and configure the aiohttp application."""
    app = web.Application()
    app["bot"] = bot
    app["dp"] = dp
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/ready", ready_handler)

    if config.WEBHOOK_URL:
        app.router.add_post(config.WEBHOOK_PATH, webhook_handler)
        logger.info(f"Webhook path registered: {config.WEBHOOK_PATH}")

    return app


async def run_web_server(bot: Bot = None, dp: Dispatcher = None):
    """Start the web server on the configured port."""
    app = create_app(bot, dp)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT)
    await site.start()
    logger.info(
        f"Web server started on http://{config.WEB_HOST}:{config.WEB_PORT}",
    )
    return runner


async def stop_web_server(runner):
    """Gracefully stop the web server."""
    if runner:
        await runner.cleanup()
        logger.info("Web server stopped")
