from unittest.mock import Mock

import pytest

from app import main
from app.core.config import Settings
from app.rag.graph import build_rag_graph


async def test_lifespan_creates_chat_model_and_compiles_graph_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = object()
    graph = build_rag_graph()
    model_factory = Mock(return_value=model)
    graph_factory = Mock(return_value=graph)
    monkeypatch.setattr(main, "create_chat_model", model_factory)
    monkeypatch.setattr(main, "build_rag_graph", graph_factory)
    settings = Settings(
        _env_file=None,
        app_env="test",
        llm_base_url="http://localhost:11434/v1",
        llm_model="test-model",
    )
    app = main.create_app(settings)

    async with app.router.lifespan_context(app):
        assert app.state.chat_model is model
        assert app.state.rag_graph is graph
        assert graph.checkpointer is None
        assert graph.store is None
        model_factory.assert_called_once_with(settings)
        graph_factory.assert_called_once_with()


async def test_lifespan_keeps_chat_model_none_when_rag_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = build_rag_graph()
    model_factory = Mock()
    graph_factory = Mock(return_value=graph)
    monkeypatch.setattr(main, "create_chat_model", model_factory)
    monkeypatch.setattr(main, "build_rag_graph", graph_factory)
    app = main.create_app(Settings(_env_file=None, app_env="test"))

    async with app.router.lifespan_context(app):
        assert app.state.chat_model is None
        assert app.state.rag_graph is graph

    model_factory.assert_not_called()
    graph_factory.assert_called_once_with()
