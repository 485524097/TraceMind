import logging
from logging.config import fileConfig

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def configure_file_logging(config_file_name: str) -> None:
    fileConfig(config_file_name, disable_existing_loggers=False)
