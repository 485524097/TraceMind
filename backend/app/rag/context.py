from dataclasses import dataclass

from app.schemas.rag import RagSource
from app.services.document_indexing import SemanticSearchResult
from app.services.rag_retrieval import KnowledgeSearchResult, RetrievalSearchResult


@dataclass(frozen=True)
class RagContext:
    sources: list[RagSource]


def build_rag_context(
    results: list[RetrievalSearchResult],
    max_chars: int,
) -> RagContext:
    sources: list[RagSource] = []
    seen: set[object] = set()
    used = 0
    for result in results:
        if result.chunk_id in seen:
            continue
        seen.add(result.chunk_id)
        if used + len(result.content) > max_chars:
            continue
        if isinstance(result, SemanticSearchResult):
            sources.append(
                RagSource(
                    source_id=f"S{len(sources) + 1}",
                    source_type="document",
                    **result.__dict__,
                )
            )
        elif isinstance(result, KnowledgeSearchResult):
            sources.append(
                RagSource(
                    source_id=f"S{len(sources) + 1}",
                    source_type="knowledge_entry",
                    **result.__dict__,
                )
            )
        used += len(result.content)
    return RagContext(sources)
