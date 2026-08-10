from collections import defaultdict
from itertools import combinations
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.document import DocumentRepository
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.repositories.knowledge_entry import KnowledgeEntryRepository
from app.schemas.knowledge_map import KnowledgeMapEdge, KnowledgeMapNode, KnowledgeMapResponse
from app.services.exceptions import KnowledgeBaseNotFoundError


def _entry_node_id(entry_id: UUID) -> str:
    return f"entry:{entry_id}"


def _document_node_id(document_id: UUID) -> str:
    return f"document:{document_id}"


def _live_document_ids(entry: KnowledgeEntry, live_ids: set[UUID]) -> set[UUID]:
    result: set[UUID] = set()
    for source in entry.sources_snapshot:
        if not isinstance(source, dict):
            continue
        raw_id = source.get("document_id")
        try:
            document_id = UUID(str(raw_id))
        except (TypeError, ValueError, AttributeError):
            continue
        if document_id in live_ids:
            result.add(document_id)
    return result


class KnowledgeMapService:
    def __init__(
        self,
        session: AsyncSession,
        knowledge_bases: KnowledgeBaseRepository | None = None,
        documents: DocumentRepository | None = None,
        knowledge_entries: KnowledgeEntryRepository | None = None,
    ) -> None:
        self.knowledge_bases = knowledge_bases or KnowledgeBaseRepository(session)
        self.documents = documents or DocumentRepository(session)
        self.knowledge_entries = knowledge_entries or KnowledgeEntryRepository(session)

    async def get(self, knowledge_base_id: UUID) -> KnowledgeMapResponse:
        knowledge_base = await self.knowledge_bases.get_by_id(knowledge_base_id)
        if knowledge_base is None:
            raise KnowledgeBaseNotFoundError(knowledge_base_id)

        documents = await self.documents.list_all(knowledge_base_id)
        entries = await self.knowledge_entries.list_all(knowledge_base_id)
        live_document_ids = {document.id for document in documents}
        kb_node_id = f"kb:{knowledge_base.id}"

        nodes = [
            KnowledgeMapNode(
                id=kb_node_id,
                type="knowledge_base",
                entity_id=knowledge_base.id,
                label=knowledge_base.name,
                metadata={"entry_count": len(entries), "document_count": len(documents)},
            )
        ]
        edges: list[KnowledgeMapEdge] = []

        for document in documents:
            document_node_id = _document_node_id(document.id)
            nodes.append(
                KnowledgeMapNode(
                    id=document_node_id,
                    type="document",
                    entity_id=document.id,
                    label=document.name,
                    metadata={
                        "relative_path": document.relative_path,
                        "source_type": document.source_type,
                    },
                )
            )
            edges.append(
                KnowledgeMapEdge(
                    id=f"contains:{kb_node_id}:{document_node_id}",
                    type="contains",
                    source=kb_node_id,
                    target=document_node_id,
                )
            )

        entries_by_tag: dict[str, list[UUID]] = defaultdict(list)
        entries_by_document: dict[UUID, list[UUID]] = defaultdict(list)
        tag_counts: dict[str, int] = defaultdict(int)
        for entry in entries:
            entry_node_id = _entry_node_id(entry.id)
            nodes.append(
                KnowledgeMapNode(
                    id=entry_node_id,
                    type="knowledge_entry",
                    entity_id=entry.id,
                    label=entry.question,
                    metadata={
                        "validation_status": entry.validation_status,
                        "tags": entry.tags,
                        "updated_at": entry.updated_at.isoformat(),
                    },
                )
            )
            edges.append(
                KnowledgeMapEdge(
                    id=f"contains:{kb_node_id}:{entry_node_id}",
                    type="contains",
                    source=kb_node_id,
                    target=entry_node_id,
                )
            )
            for tag in sorted(set(entry.tags)):
                entries_by_tag[tag].append(entry.id)
                tag_counts[tag] += 1
                edges.append(
                    KnowledgeMapEdge(
                        id=f"tagged:{entry_node_id}:tag:{tag}",
                        type="tagged",
                        source=entry_node_id,
                        target=f"tag:{tag}",
                    )
                )
            for document_id in sorted(_live_document_ids(entry, live_document_ids)):
                entries_by_document[document_id].append(entry.id)
                document_node_id = _document_node_id(document_id)
                edges.append(
                    KnowledgeMapEdge(
                        id=f"cites:{entry_node_id}:{document_node_id}",
                        type="cites",
                        source=entry_node_id,
                        target=document_node_id,
                    )
                )

        for tag in sorted(entries_by_tag):
            nodes.append(
                KnowledgeMapNode(
                    id=f"tag:{tag}",
                    type="tag",
                    entity_id=None,
                    label=tag,
                    metadata={"tag": tag, "entry_count": tag_counts[tag]},
                )
            )

        related_reasons: dict[tuple[UUID, UUID], dict[str, set[str]]] = defaultdict(
            lambda: {"shared_tags": set(), "shared_document_ids": set()}
        )
        for tag, entry_ids in entries_by_tag.items():
            for left, right in combinations(sorted(set(entry_ids)), 2):
                related_reasons[(left, right)]["shared_tags"].add(tag)
        for document_id, entry_ids in entries_by_document.items():
            for left, right in combinations(sorted(set(entry_ids)), 2):
                related_reasons[(left, right)]["shared_document_ids"].add(str(document_id))

        for (left, right), reasons in sorted(related_reasons.items()):
            left_node_id = _entry_node_id(left)
            right_node_id = _entry_node_id(right)
            edges.append(
                KnowledgeMapEdge(
                    id=f"related:{left_node_id}:{right_node_id}",
                    type="related",
                    source=left_node_id,
                    target=right_node_id,
                    metadata={
                        "shared_tags": sorted(reasons["shared_tags"]),
                        "shared_document_ids": sorted(reasons["shared_document_ids"]),
                    },
                )
            )

        return KnowledgeMapResponse(nodes=nodes, edges=edges)
