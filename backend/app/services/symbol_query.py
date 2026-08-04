from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.symbols.java import (
    JavaSymbolKind,
    normalize_java_identifier,
    normalize_java_parameter_types,
    normalize_java_qualified_name,
)

SymbolSyntaxMode = Literal["hash", "qualified_dot", "natural_language"]

# Stage 12A-2 must validate candidates with an independent Qdrant filter/count/scroll.
# Dense thresholds cannot prove symbol existence. A validated symbol with no Hybrid hits must
# use exact point scrolling, ordered by start_line/chunk_index; semantic ordering may only reuse
# the existing reranker. Retrieval thresholds, BM25, RRF, and Top K stay unchanged.

_IDENTIFIER = r"(?:[^\W\d]|[$_])[\w$]*"
_QUALIFIED = rf"{_IDENTIFIER}(?:\.{_IDENTIFIER})*"
_HASH = re.compile(rf"(?<![\w$./`])(?P<owner>{_QUALIFIED})#(?P<member>{_IDENTIFIER})")
_DOT = re.compile(rf"(?<![\w$./:`])(?P<qualified>{_IDENTIFIER}(?:\.{_IDENTIFIER}){{2,}})")
_NATURAL_METHOD = re.compile(
    rf"(?<![\w$])(?P<owner>{_QUALIFIED})\s*的\s*(?P<member>{_IDENTIFIER})\s*方法"
)
_NATURAL_CONSTRUCTOR = re.compile(
    rf"(?:查看|查找|定位|解释)?\s*(?P<owner>{_QUALIFIED})\s*(?:的\s*)?构造函数"
)
_NATURAL_INITIALIZER = re.compile(
    rf"(?:查看|查找|定位|解释)?\s*(?P<owner>{_QUALIFIED})\s*(?:的\s*)?"
    rf"(?P<initializer>静态初始化块|实例初始化块)"
)
_EDGE_PUNCTUATION = " \t\r\n,，:：;；!?！？-—"


@dataclass(frozen=True)
class SymbolQueryCandidate:
    syntax_mode: SymbolSyntaxMode
    owner_name: str
    member_name: str
    parameter_types: tuple[str, ...] | None
    kind_hint: JavaSymbolKind | None
    raw_candidate: str
    fallback_query: str
    scoped_query: str


def parse_symbol_query(query: str) -> SymbolQueryCandidate | None:
    fallback_query = query.strip()
    if not fallback_query:
        return None
    for parser in (
        _parse_hash,
        _parse_natural_method,
        _parse_natural_constructor,
        _parse_natural_initializer,
        _parse_dot,
    ):
        candidate = parser(fallback_query)
        if candidate is not None:
            return candidate
    return None


def _parse_hash(query: str) -> SymbolQueryCandidate | None:
    match = _HASH.search(query)
    if match is None:
        return None
    owner = normalize_java_qualified_name(match.group("owner"))
    member = normalize_java_identifier(match.group("member"))
    if owner is None or member is None:
        return None
    end, parameters, valid = _parameter_suffix(query, match.end())
    if not valid:
        return None
    return _candidate(
        "hash",
        owner,
        member,
        parameters,
        "method" if parameters is not None else None,
        query,
        match.start(),
        end,
    )


def _parse_dot(query: str) -> SymbolQueryCandidate | None:
    if "://" in query or "](" in query:
        return None
    match = _DOT.search(query)
    if match is None:
        return None
    if _is_java_declaration_context(query, match.start()):
        return None
    qualified = normalize_java_qualified_name(match.group("qualified"))
    if qualified is None or qualified.count(".") < 2:
        return None
    owner, member = qualified.rsplit(".", 1)
    # Dotted configuration keys and host-like names are common in technical questions.
    # A member reference has a type-like immediate owner; a top-level type reference has
    # a type-like terminal name. Qdrant validation still decides whether it really exists.
    if not owner.rsplit(".", 1)[-1][0].isupper() and not member[0].isupper():
        return None
    end, parameters, valid = _parameter_suffix(query, match.end())
    if not valid:
        return None
    return _candidate(
        "qualified_dot",
        owner,
        member,
        parameters,
        "method" if parameters is not None else None,
        query,
        match.start(),
        end,
    )


def _is_java_declaration_context(query: str, candidate_start: int) -> bool:
    prefix = query[:candidate_start]
    return re.search(r"(?:^|\s)(?:import|package)\s+(?:static\s+)?$", prefix) is not None


def _parse_natural_method(query: str) -> SymbolQueryCandidate | None:
    match = _NATURAL_METHOD.search(query)
    if match is None:
        return None
    owner = normalize_java_qualified_name(match.group("owner"))
    member = normalize_java_identifier(match.group("member"))
    if owner is None or member is None:
        return None
    return _candidate(
        "natural_language", owner, member, None, "method", query, match.start(), match.end()
    )


def _parse_natural_constructor(query: str) -> SymbolQueryCandidate | None:
    match = _NATURAL_CONSTRUCTOR.search(query)
    if match is None:
        return None
    owner = normalize_java_qualified_name(match.group("owner"))
    if owner is None:
        return None
    return _candidate(
        "natural_language",
        owner,
        "<init>",
        None,
        "constructor",
        query,
        match.start(),
        match.end(),
    )


def _parse_natural_initializer(query: str) -> SymbolQueryCandidate | None:
    match = _NATURAL_INITIALIZER.search(query)
    if match is None:
        return None
    owner = normalize_java_qualified_name(match.group("owner"))
    if owner is None:
        return None
    member = "<clinit>" if match.group("initializer") == "静态初始化块" else "<init-block>"
    return _candidate(
        "natural_language",
        owner,
        member,
        None,
        "initializer",
        query,
        match.start(),
        match.end(),
    )


def _parameter_suffix(query: str, start: int) -> tuple[int, tuple[str, ...] | None, bool]:
    if start >= len(query) or query[start] != "(":
        return start, None, True
    depth = 0
    for index in range(start, len(query)):
        character = query[index]
        if character == "(":
            depth += 1
            if depth > 1:
                return start, None, False
        elif character == ")":
            depth -= 1
            if depth == 0:
                raw = query[start + 1 : index]
                parameters = normalize_java_parameter_types(raw)
                return (index + 1, parameters, parameters is not None)
            if depth < 0:
                return start, None, False
    return start, None, False


def _candidate(
    syntax_mode: SymbolSyntaxMode,
    owner: str,
    member: str,
    parameters: tuple[str, ...] | None,
    kind_hint: JavaSymbolKind | None,
    query: str,
    start: int,
    end: int,
) -> SymbolQueryCandidate:
    raw_candidate = query[start:end]
    scoped_query = f"{query[:start]} {query[end:]}".strip(_EDGE_PUNCTUATION)
    scoped_query = re.sub(r"\s+", " ", scoped_query)
    return SymbolQueryCandidate(
        syntax_mode,
        owner,
        member,
        parameters,
        kind_hint,
        raw_candidate,
        query,
        scoped_query,
    )
