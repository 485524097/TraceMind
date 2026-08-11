import re
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from app.models.document import Document
from app.services.exceptions import InvalidDocumentNameError, UnsupportedDocumentTypeError
from app.storage.names import normalize_document_path

PathScopeMode = Literal["none", "exact"]

_LEADING_LOCATION_WORD = re.compile(r"^[\s,，:：;；\-—]*(?:中的|里的|内的|中|里|内)\s*")
_EDGE_PUNCTUATION = " \t\r\n,，:：;；!?！？-—"
_QUERY_BOUNDARIES = frozenset(" \t\r\n,!?;:，。！？；：\"'`<>|()[]{}")


@dataclass(frozen=True)
class PreparedRetrievalQuery:
    original_query: str
    semantic_query: str
    scoped_document_id: UUID | None
    path_scope_mode: PathScopeMode = "none"
    explicit_relative_path: str | None = None


class PathDocumentRepository(Protocol):
    async def get_document_by_normalized_path(
        self, knowledge_base_id: UUID, normalized_path: str
    ) -> Document | None: ...


class ExplicitDocumentPathResolver:
    def __init__(
        self,
        repository: PathDocumentRepository,
        allowed_extensions: set[str],
    ) -> None:
        self.repository = repository
        self.allowed_extensions = allowed_extensions

    async def prepare(
        self,
        knowledge_base_id: UUID,
        query: str,
        *,
        document_id: UUID | None,
    ) -> PreparedRetrievalQuery:
        if document_id is not None:
            return PreparedRetrievalQuery(
                original_query=query,
                semantic_query=query,
                scoped_document_id=document_id,
            )

        for candidate, start, end in self._path_candidates(query):
            try:
                safe_path = normalize_document_path(candidate, self.allowed_extensions)
            except (InvalidDocumentNameError, UnsupportedDocumentTypeError):
                continue
            document = await self.repository.get_document_by_normalized_path(
                knowledge_base_id, safe_path.normalized_path
            )
            if document is None:
                continue
            semantic_query = self._semantic_query(query, start, end)
            return PreparedRetrievalQuery(
                original_query=query,
                semantic_query=semantic_query or safe_path.display_name or query,
                scoped_document_id=document.id,
                path_scope_mode="exact",
                explicit_relative_path=safe_path.relative_path,
            )

        return PreparedRetrievalQuery(
            original_query=query,
            semantic_query=query,
            scoped_document_id=None,
        )

    def _path_candidates(self, query: str) -> list[tuple[str, int, int]]:
        extensions = sorted(self.allowed_extensions, key=len, reverse=True)
        if not extensions:
            return []
        extension_end = re.compile(
            rf"(?:{'|'.join(re.escape(item) for item in extensions)})"
            rf"(?=$|[\s,!?;:，。！？；：\"'`<>|()[\]{{}}])",
            re.IGNORECASE,
        )
        candidates: list[tuple[str, int, int]] = []
        seen: set[tuple[int, int]] = set()
        for extension_match in extension_end.finditer(query):
            end = extension_match.end()
            starts = [0]
            starts.extend(
                index + 1
                for index, character in enumerate(query[:end])
                if character in _QUERY_BOUNDARIES
            )
            for start in reversed(starts):
                candidate = query[start:end]
                if "/" not in candidate and "\\" not in candidate:
                    continue
                span = (start, end)
                if span not in seen:
                    candidates.append((candidate, start, end))
                    seen.add(span)
        return candidates

    @staticmethod
    def _semantic_query(query: str, start: int, end: int) -> str:
        before = query[:start].rstrip(_EDGE_PUNCTUATION)
        after = query[end:].lstrip(_EDGE_PUNCTUATION)
        after = _LEADING_LOCATION_WORD.sub("", after)
        return " ".join(part for part in (before, after) if part).strip()
