from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from app.indexing import PayloadPoint, QdrantGateway
from app.services.retrieval_query import PreparedRetrievalQuery, SymbolScopeReason
from app.services.symbol_query import SymbolQueryCandidate, parse_symbol_query
from app.symbols.java import (
    JavaSymbolKind,
    build_java_member_lookup_keys,
    build_java_type_lookup_keys,
)

_MEMBER_KINDS: tuple[JavaSymbolKind, ...] = (
    "method",
    "field",
    "enum_constant",
)


@dataclass(frozen=True)
class _CandidateKey:
    value: str
    kind: JavaSymbolKind


@dataclass(frozen=True)
class _SymbolIdentity:
    document_id: UUID
    kind: JavaSymbolKind
    qualified_name: str


@dataclass(frozen=True)
class _ValidatedMatch:
    key: _CandidateKey
    identity: _SymbolIdentity
    signature: str | None


class SymbolScopeResolver:
    def __init__(self, gateway: QdrantGateway) -> None:
        self.gateway = gateway

    async def prepare(
        self,
        prepared: PreparedRetrievalQuery,
        *,
        knowledge_base_id: UUID,
        generations: list[UUID],
        language: str | None,
    ) -> PreparedRetrievalQuery:
        candidate = parse_symbol_query(prepared.semantic_query)
        if candidate is None:
            return prepared
        fallback_query = candidate.fallback_query or prepared.semantic_query
        if not generations:
            return self._fallback(prepared, fallback_query, "not_found")

        await self.gateway.ensure_collection()
        matches: list[_ValidatedMatch] = []
        for candidate_key in _candidate_keys(candidate):
            result = await self.gateway.scroll_symbol_matches(
                knowledge_base_id=knowledge_base_id,
                generations=generations,
                symbol_lookup_key=candidate_key.value,
                language=language,
                document_id=prepared.scoped_document_id,
            )
            if result.truncated:
                return self._fallback(prepared, fallback_query, "unsupported")
            matches.extend(
                match
                for point in result.points
                if (match := _validated_match(point, candidate_key)) is not None
            )

        identities = {match.identity for match in matches}
        if not identities:
            return self._fallback(prepared, fallback_query, "not_found")
        if len(identities) != 1:
            return self._fallback(prepared, fallback_query, "ambiguous")

        identity = next(iter(identities))
        identity_matches = [match for match in matches if match.identity == identity]
        keys = {match.key for match in identity_matches}
        if len(keys) != 1:
            return self._fallback(prepared, fallback_query, "ambiguous")
        frozen_key = next(iter(keys))
        signatures = {match.signature for match in identity_matches if match.signature is not None}
        signature = (
            next(iter(signatures))
            if candidate.parameter_types is not None and len(signatures) == 1
            else None
        )
        semantic_query = candidate.scoped_query or fallback_query or prepared.original_query
        return replace(
            prepared,
            semantic_query=semantic_query,
            symbol_scope_mode="exact",
            symbol_scope_reason=None,
            scoped_symbol_lookup_key=frozen_key.value,
            scoped_symbol_kind=identity.kind,
            scoped_symbol_qualified_name=identity.qualified_name,
            scoped_symbol_signature=signature,
            symbol_fallback_query=fallback_query,
        )

    @staticmethod
    def _fallback(
        prepared: PreparedRetrievalQuery,
        fallback_query: str,
        reason: SymbolScopeReason,
    ) -> PreparedRetrievalQuery:
        return replace(
            prepared,
            semantic_query=fallback_query or prepared.original_query,
            symbol_scope_mode="fallback",
            symbol_scope_reason=reason,
            scoped_symbol_lookup_key=None,
            scoped_symbol_kind=None,
            scoped_symbol_qualified_name=None,
            scoped_symbol_signature=None,
            symbol_fallback_query=fallback_query,
        )


def _candidate_keys(candidate: SymbolQueryCandidate) -> list[_CandidateKey]:
    owner = candidate.owner_name
    owner_is_qualified = "." in owner
    selected: list[_CandidateKey] = []
    kinds: tuple[JavaSymbolKind, ...]
    if candidate.kind_hint is not None:
        kinds = (candidate.kind_hint,)
    else:
        kinds = _MEMBER_KINDS

    for kind in kinds:
        keys = build_java_member_lookup_keys(
            kind,
            owner,
            [candidate.member_name],
            parameter_types=candidate.parameter_types,
        )
        selected.extend(
            _CandidateKey(key, kind)
            for key in keys or []
            if _matches_requested_owner(key, owner, owner_is_qualified)
            and _matches_parameter_mode(key, candidate.parameter_types)
        )

    if candidate.syntax_mode == "qualified_dot" and candidate.parameter_types is None:
        qualified_type = f"{owner}.{candidate.member_name}"
        type_keys = build_java_type_lookup_keys(qualified_type, candidate.member_name)
        if type_keys:
            selected.append(_CandidateKey(type_keys[0], "type"))
    return _stable_candidate_keys(selected)


def _matches_requested_owner(key: str, owner: str, owner_is_qualified: bool) -> bool:
    key_owner = key.split(":", 2)[-1].split("#", 1)[0]
    requested = owner if owner_is_qualified else owner.rsplit(".", 1)[-1]
    return key_owner == requested


def _matches_parameter_mode(key: str, parameters: tuple[str, ...] | None) -> bool:
    return ("(" in key) if parameters is not None else ("(" not in key)


def _stable_candidate_keys(values: list[_CandidateKey]) -> list[_CandidateKey]:
    result: list[_CandidateKey] = []
    seen: set[_CandidateKey] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _validated_match(point: PayloadPoint, key: _CandidateKey) -> _ValidatedMatch | None:
    payload = point.payload
    lookup_keys = payload.get("symbol_lookup_keys")
    if (
        not isinstance(lookup_keys, list)
        or not lookup_keys
        or any(not isinstance(value, str) or not value for value in lookup_keys)
        or key.value not in lookup_keys
    ):
        return None
    kind = payload.get("symbol_kind")
    qualified_name = payload.get("symbol_qualified_name")
    if kind != key.kind or not isinstance(qualified_name, str) or not qualified_name:
        return None
    try:
        document_id = UUID(str(payload["document_id"]))
    except (KeyError, TypeError, ValueError):
        return None
    signature = payload.get("symbol_signature")
    return _ValidatedMatch(
        key,
        _SymbolIdentity(document_id, key.kind, qualified_name),
        signature if isinstance(signature, str) and signature else None,
    )
