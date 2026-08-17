from unittest.mock import Mock

import pytest

from app.core import logging as logging_module


def test_file_logging_preserves_existing_application_loggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_config = Mock()
    monkeypatch.setattr(logging_module, "fileConfig", file_config)

    logging_module.configure_file_logging("alembic.ini")

    file_config.assert_called_once_with(
        "alembic.ini",
        disable_existing_loggers=False,
    )
