import re
import unicodedata
from typing import Literal

RouteMode = Literal["direct", "rag"]

_DIRECT_QUERIES = frozenset(
    {
        "你好",
        "hello",
        "hi",
        "谢谢",
        "多谢",
        "thanks",
        "thank you",
        "再见",
        "bye",
        "你是谁",
        "介绍一下你自己",
    }
)
_TRAILING_PUNCTUATION = re.compile(r"[.!?,;:。！？，；：]+$")


def normalize_route_query(query: str) -> str:
    normalized = unicodedata.normalize("NFKC", query).strip().casefold()
    normalized = " ".join(normalized.split())
    return _TRAILING_PUNCTUATION.sub("", normalized).strip()


def route_query(query: str) -> RouteMode:
    return "direct" if normalize_route_query(query) in _DIRECT_QUERIES else "rag"
