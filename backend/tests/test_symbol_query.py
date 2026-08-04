import json
import unicodedata
from pathlib import Path

import pytest

from app.services.symbol_query import parse_symbol_query
from app.symbols.java import normalize_java_parameter_types, normalize_java_type


def test_fixed_retrieval_queries_do_not_trigger_symbol_scope() -> None:
    dataset = (
        Path(__file__).parents[1]
        / "evals"
        / "retrieval"
        / "datasets"
        / "synthetic_retrieval_v1.jsonl"
    )
    cases = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    assert len(cases) == 24
    triggered = [
        (case.get("id"), case["query"])
        for case in cases
        if parse_symbol_query(case["query"]) is not None
    ]
    assert triggered == []


def test_hash_candidate_preserves_fallback_and_removes_only_scoped_symbol() -> None:
    candidate = parse_symbol_query("请解释 UserService#source(String username) 的行为")

    assert candidate is not None
    assert candidate.syntax_mode == "hash"
    assert candidate.owner_name == "UserService"
    assert candidate.member_name == "source"
    assert candidate.parameter_types == ("String",)
    assert candidate.kind_hint == "method"
    assert candidate.raw_candidate == "UserService#source(String username)"
    assert candidate.fallback_query == "请解释 UserService#source(String username) 的行为"
    assert candidate.scoped_query == "请解释 的行为"


def test_hash_parameters_support_nested_generics_arrays_varargs_and_ignore_names() -> None:
    candidate = parse_symbol_query(
        "demo.UserService#source(Map<String, ? extends User> users, String... names, int ids[])"
    )

    assert candidate is not None
    assert candidate.parameter_types == ("Map<String,? extends User>", "String[]", "int[]")


def test_qualified_dot_candidate_is_pending_validation() -> None:
    candidate = parse_symbol_query("定位 demo.UserService.source(String username)")

    assert candidate is not None
    assert candidate.syntax_mode == "qualified_dot"
    assert candidate.owner_name == "demo.UserService"
    assert candidate.member_name == "source"
    assert candidate.parameter_types == ("String",)
    assert candidate.scoped_query == "定位"


@pytest.mark.parametrize(
    "query",
    [
        "demo.UserService.source",
        "demo.UserService.source(String)",
        "Outer.Nested#run",
        "com.example.Outer.Nested#run(String)",
        "com.example.UserService",
    ],
)
def test_explicit_java_symbol_forms_produce_candidates(query: str) -> None:
    assert parse_symbol_query(query) is not None


@pytest.mark.parametrize(
    ("query", "owner", "member", "kind"),
    [
        ("UserService 的 source 方法", "UserService", "source", "method"),
        ("查看 UserService 构造函数", "UserService", "<init>", "constructor"),
        ("查看 用户服务 构造函数", "用户服务", "<init>", "constructor"),
    ],
)
def test_strict_natural_language_candidates(query: str, owner: str, member: str, kind: str) -> None:
    candidate = parse_symbol_query(query)

    assert candidate is not None
    assert candidate.syntax_mode == "natural_language"
    assert (candidate.owner_name, candidate.member_name, candidate.kind_hint) == (
        owner,
        member,
        kind,
    )


def test_unicode_candidate_and_type_normalization_use_nfc_without_casefold() -> None:
    decomposed_owner = unicodedata.normalize("NFD", "用户服务")
    decomposed_member = unicodedata.normalize("NFD", "查询")
    candidate = parse_symbol_query(
        f"{decomposed_owner}#{decomposed_member}(List < String > values)"
    )

    assert candidate is not None
    assert candidate.owner_name == "用户服务"
    assert candidate.member_name == "查询"
    assert candidate.parameter_types == ("List<String>",)
    assert normalize_java_type("UserService") == "UserService"
    assert normalize_java_type("userservice") == "userservice"


@pytest.mark.parametrize(
    "query",
    [
        "source",
        "source method",
        "这个方法怎么工作",
        "如何实现缓存",
        "https://demo.UserService.source",
        "[文档](demo.UserService.source)",
        "v1.2.3",
        "UserService#source(Map<String, User)",
        "demo.UserService.source(String[]",
        "embedding.model",
        "qdrant.url",
        "redis.host",
        "app.debug",
        "python.version",
        "v1.18.2",
        "example.com",
        "file.md",
        "foo.bar",
        "import com.example.UserService;",
        "package com.example;",
    ],
)
def test_ordinary_or_invalid_queries_do_not_produce_candidates(query: str) -> None:
    assert parse_symbol_query(query) is None


def test_parameter_normalization_rejects_unbalanced_syntax_and_is_stable() -> None:
    assert normalize_java_parameter_types("Map<String, User> values, String... names") == (
        "Map<String,User>",
        "String[]",
    )
    assert normalize_java_parameter_types("Map<String, User") is None
    query = "demo.UserService#source(Map<String, User> values)"
    assert parse_symbol_query(query) == parse_symbol_query(query)
