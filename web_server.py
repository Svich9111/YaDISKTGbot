"""
HTTP web server for Render.com health checks.
Render requires a service that binds to $PORT and responds to HTTP requests.
"""
import time
from aiohttp import web
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
            "endpoints": ["/health", "/ready"],
        },
    )


def create_app() -> web.Application:
    """Create and configure the aiohttp application."""
    app = web.Application()
    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/ready", ready_handler)
    return app


async def run_web_server():
    """Start the web server on the configured port."""
    app = create_app()
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
