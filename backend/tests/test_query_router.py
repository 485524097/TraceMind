import pytest

from app.services.query_router import normalize_route_query, route_query


@pytest.mark.parametrize(
    "query",
    [
        "你好",
        " 你好！ ",
        "ＨＥＬＬＯ！",
        "Hi",
        "谢谢。",
        "thank you",
        "再见",
        "BYE!",
        "你是谁？",
        "介绍一下你自己",
    ],
)
def test_exact_social_queries_route_direct(query: str) -> None:
    assert route_query(query) == "direct"


@pytest.mark.parametrize(
    "query",
    [
        "资料里的 hello world 是什么意思",
        "你好，请总结文档",
        "谢谢这份资料解释一下 RRF",
        "src/app.py 里做了什么",
        "比较两份资料",
        "当前知识库主要讲什么？",
    ],
)
def test_any_non_whitelisted_or_knowledge_query_routes_rag(query: str) -> None:
    assert route_query(query) == "rag"


def test_normalization_is_stable_without_substring_matching() -> None:
    assert normalize_route_query("  Ｔｈａｎｋｓ！！！ ") == "thanks"
    assert route_query("say hello") == "rag"
