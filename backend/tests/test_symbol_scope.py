from typing import cast
from uuid import UUID, uuid4

import pytest

from app.indexing import PayloadPoint, PayloadScrollResult, QdrantGateway, VectorIndexError
from app.services.retrieval_query import PreparedRetrievalQuery
from app.services.symbol_scope import SymbolScopeResolver


class FakeGateway:
    def __init__(self, matches: dict[str, PayloadScrollResult] | None = None) -> None:
        self.matches = matches or {}
        self.ensure_calls = 0
        self.scroll_calls: list[dict[str, object]] = []
        self.error: Exception | None = None

    async def ensure_collection(self) -> None:
        self.ensure_calls += 1
        if self.error is not None:
            raise self.error

    async def scroll_symbol_matches(self, **kwargs: object) -> PayloadScrollResult:
        self.scroll_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.matches.get(str(kwargs["symbol_lookup_key"]), PayloadScrollResult([]))


def point(
    key: str,
    *,
    document_id: UUID | None = None,
    kind: str = "method",
    qualified_name: str = "demo.UserService.source",
    signature: str = "String source(String username)",
) -> PayloadPoint:
    return PayloadPoint(
        str(uuid4()),
        {
            "document_id": str(document_id or uuid4()),
            "symbol_lookup_keys": [key],
            "symbol_kind": kind,
            "symbol_qualified_name": qualified_name,
            "symbol_signature": signature,
        },
    )


async def resolve(
    gateway: FakeGateway,
    query: str,
    *,
    document_id: UUID | None = None,
) -> PreparedRetrievalQuery:
    return await SymbolScopeResolver(cast(QdrantGateway, gateway)).prepare(
        PreparedRetrievalQuery(
            original_query=query,
            semantic_query=query,
            scoped_document_id=document_id,
        ),
        knowledge_base_id=uuid4(),
        generations=[uuid4()],
        language="java",
    )


async def test_normal_query_has_no_qdrant_symbol_request() -> None:
    gateway = FakeGateway()
    prepared = await resolve(gateway, "解释这个服务为什么失败")
    assert prepared.symbol_scope_mode == "none"
    assert gateway.ensure_calls == 0
    assert gateway.scroll_calls == []


async def test_parameterized_method_freezes_exact_signature_key() -> None:
    key = "v1:method:demo.UserService#source(String)"
    gateway = FakeGateway({key: PayloadScrollResult([point(key)])})
    prepared = await resolve(gateway, "解释 demo.UserService#source(String) 为什么失败")
    assert prepared.symbol_scope_mode == "exact"
    assert prepared.scoped_symbol_lookup_key == key
    assert prepared.scoped_symbol_kind == "method"
    assert prepared.scoped_symbol_qualified_name == "demo.UserService.source"
    assert prepared.scoped_symbol_signature == "String source(String username)"
    assert prepared.semantic_query == "解释 为什么失败"
    assert [call["symbol_lookup_key"] for call in gateway.scroll_calls] == [key]


async def test_partially_qualified_nested_owner_can_be_validated() -> None:
    key = "v1:method:Outer.Nested#run"
    gateway = FakeGateway(
        {key: PayloadScrollResult([point(key, qualified_name="demo.Outer.Nested.run")])}
    )

    prepared = await resolve(gateway, "Outer.Nested#run")

    assert prepared.symbol_scope_mode == "exact"
    assert prepared.scoped_symbol_lookup_key == key
    assert prepared.scoped_symbol_qualified_name == "demo.Outer.Nested.run"


async def test_parameterized_method_does_not_degrade_to_member_key() -> None:
    member_key = "v1:method:UserService#source"
    gateway = FakeGateway({member_key: PayloadScrollResult([point(member_key)])})
    prepared = await resolve(gateway, "UserService#source(String)")
    assert prepared.symbol_scope_mode == "fallback"
    assert prepared.symbol_scope_reason == "not_found"
    assert prepared.semantic_query == "UserService#source(String)"
    assert all(call["symbol_lookup_key"] != member_key for call in gateway.scroll_calls)


async def test_overload_chunks_for_one_owner_are_one_method_family() -> None:
    key = "v1:method:UserService#source"
    document_id = uuid4()
    gateway = FakeGateway(
        {
            key: PayloadScrollResult(
                [
                    point(key, document_id=document_id, signature="source()"),
                    point(key, document_id=document_id, signature="source(String)"),
                    point(key, document_id=document_id, signature="source(String)"),
                ]
            )
        }
    )
    prepared = await resolve(gateway, "UserService#source")
    assert prepared.symbol_scope_mode == "exact"
    assert prepared.scoped_symbol_signature is None


async def test_simple_owner_across_packages_is_ambiguous() -> None:
    key = "v1:method:UserService#source"
    gateway = FakeGateway(
        {
            key: PayloadScrollResult(
                [
                    point(key, qualified_name="demo.UserService.source"),
                    point(key, qualified_name="example.UserService.source"),
                ]
            )
        }
    )
    prepared = await resolve(gateway, "UserService#source")
    assert prepared.symbol_scope_mode == "fallback"
    assert prepared.symbol_scope_reason == "ambiguous"


async def test_document_scope_can_disambiguate_simple_owner() -> None:
    key = "v1:method:UserService#source"
    document_id = uuid4()
    gateway = FakeGateway({key: PayloadScrollResult([point(key, document_id=document_id)])})
    prepared = await resolve(gateway, "UserService#source", document_id=document_id)
    assert prepared.symbol_scope_mode == "exact"
    assert gateway.scroll_calls[0]["document_id"] == document_id


async def test_multiple_kinds_are_ambiguous() -> None:
    method_key = "v1:method:UserService#source"
    field_key = "v1:field:UserService#source"
    document_id = uuid4()
    gateway = FakeGateway(
        {
            method_key: PayloadScrollResult([point(method_key, document_id=document_id)]),
            field_key: PayloadScrollResult(
                [
                    point(
                        field_key,
                        document_id=document_id,
                        kind="field",
                        qualified_name="demo.UserService.source",
                    )
                ]
            ),
        }
    )
    prepared = await resolve(gateway, "UserService#source", document_id=document_id)
    assert prepared.symbol_scope_mode == "fallback"
    assert prepared.symbol_scope_reason == "ambiguous"


async def test_malformed_old_payload_is_not_an_exact_match() -> None:
    key = "v1:method:UserService#source"
    malformed = point(key)
    malformed.payload["symbol_lookup_keys"] = [key, 3]
    gateway = FakeGateway({key: PayloadScrollResult([malformed])})
    prepared = await resolve(gateway, "UserService#source")
    assert prepared.symbol_scope_mode == "fallback"
    assert prepared.symbol_scope_reason == "not_found"


async def test_scan_limit_uses_unsupported_fallback() -> None:
    key = "v1:method:UserService#source"
    gateway = FakeGateway({key: PayloadScrollResult([point(key)], truncated=True)})
    prepared = await resolve(gateway, "UserService#source")
    assert prepared.symbol_scope_mode == "fallback"
    assert prepared.symbol_scope_reason == "unsupported"


async def test_qdrant_failure_is_not_reported_as_not_found() -> None:
    gateway = FakeGateway()
    gateway.error = VectorIndexError("safe failure")
    with pytest.raises(VectorIndexError):
        await resolve(gateway, "UserService#source")


@pytest.mark.parametrize(
    ("query", "key", "kind", "qualified_name"),
    [
        (
            "查看 demo.UserService 构造函数",
            "v1:constructor:demo.UserService#<init>",
            "constructor",
            "demo.UserService.<init>",
        ),
        ("demo.Outer.Inner", "v1:type:demo.Outer.Inner", "type", "demo.Outer.Inner"),
        (
            "demo.UserService.timeout",
            "v1:field:demo.UserService#timeout",
            "field",
            "demo.UserService.timeout",
        ),
        (
            "demo.State.READY",
            "v1:enum_constant:demo.State#READY",
            "enum_constant",
            "demo.State.READY",
        ),
        (
            "查看 demo.UserService 的静态初始化块",
            "v1:initializer:demo.UserService#<clinit>",
            "initializer",
            "demo.UserService.<clinit>",
        ),
    ],
)
async def test_supported_symbol_kinds_resolve_exactly(
    query: str,
    key: str,
    kind: str,
    qualified_name: str,
) -> None:
    gateway = FakeGateway(
        {key: PayloadScrollResult([point(key, kind=kind, qualified_name=qualified_name)])}
    )
    prepared = await resolve(gateway, query)
    assert prepared.symbol_scope_mode == "exact"
    assert prepared.scoped_symbol_lookup_key == key
    assert prepared.scoped_symbol_kind == kind
