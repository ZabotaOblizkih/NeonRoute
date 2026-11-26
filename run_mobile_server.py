"""
Headless Mobile Gateway Server entry point for NeonRoute.
"""

import sys
import logging
import uvicorn

from mobile_server.config import settings


def setup_logging():
    from logging.handlers import TimedRotatingFileHandler
    from config import LOGS_DIR

    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(
            TimedRotatingFileHandler(
                LOGS_DIR / "neonroute.log",
                when="midnight",
                interval=1,
                backupCount=7,
                encoding="utf-8",
            )
        )
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        handlers=handlers,
    )


def main():
    setup_logging()
    logger = logging.getLogger("NeonRoute.MobileServer")
    logger.info("Starting NeonRoute Headless Mobile Server...")
    logger.info(f"Target address: http://{settings.HOST}:{settings.PORT}")

    try:
        uvicorn.run(
            "mobile_server.api:app",
            host=settings.HOST,
            port=settings.PORT,
            reload=False,
            workers=1,
            log_level="info",
        )
    except KeyboardInterrupt:
        logger.info("Mobile server stopped by user (KeyboardInterrupt).")
    except Exception as exc:
        logger.critical(f"Server encountered an unexpected error: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
