from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.embedding import SentenceTransformerEmbeddingProvider
from app.indexing import QdrantGateway
from app.models.document import DocumentVersion
from app.schemas.indexing import (
    DocumentIndexRequest,
    DocumentIndexRequestResponse,
    DocumentIndexStatusResponse,
    SemanticSearchRequest,
    SemanticSearchResponse,
    SemanticSearchResultResponse,
)
from app.services.document_index_dispatcher import CeleryDocumentIndexingDispatcher
from app.services.document_indexing import DocumentIndexingService
from app.services.exceptions import (
    DocumentIndexingQueueError,
    DocumentNotReadyForIndexError,
    DocumentVersionNotFoundError,
    HybridSearchUnavailableError,
    SemanticSearchUnavailableError,
)

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}", tags=["semantic-search"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_document_indexing_service(
    request: Request, session: SessionDependency
) -> DocumentIndexingService:
    settings = request.app.state.settings
    provider = SentenceTransformerEmbeddingProvider(
        settings.embedding_model_name,
        settings.embedding_dimension,
        settings.embedding_batch_size,
        settings.embedding_device,
    )
    gateway = QdrantGateway(
        request.app.state.qdrant_client.client,
        collection_name=settings.qdrant_collection_name,
        vector_name=settings.qdrant_dense_vector_name,
        sparse_vector_name=settings.qdrant_sparse_vector_name,
        bm25_model=settings.qdrant_bm25_model,
        bm25_tokenizer=settings.qdrant_bm25_tokenizer,
        bm25_language=settings.qdrant_bm25_language,
        dimension=settings.embedding_dimension,
        upsert_batch_size=settings.qdrant_upsert_batch_size,
        dense_prefetch_limit=settings.hybrid_dense_prefetch_limit,
        sparse_prefetch_limit=settings.hybrid_sparse_prefetch_limit,
    )
    return DocumentIndexingService(
        session,
        settings,
        provider,
        gateway,
        dispatcher=CeleryDocumentIndexingDispatcher(),
    )


IndexingServiceDependency = Annotated[
    DocumentIndexingService, Depends(get_document_indexing_service)
]


def index_status_response(version: DocumentVersion) -> DocumentIndexStatusResponse:
    return DocumentIndexStatusResponse(
        version_id=version.id,
        index_status=version.index_status,
        active_index_generation=version.active_index_generation,
        index_attempt_generation=version.index_attempt_generation,
        index_started_at=version.index_started_at,
        indexed_at=version.indexed_at,
        last_index_attempt_at=version.last_index_attempt_at,
        indexed_chunk_count=version.indexed_chunk_count,
        embedding_model=version.embedding_model,
        embedding_dimension=version.embedding_dimension,
        index_error_code=version.index_error_code,
        index_error_message=version.index_error_message,
    )


def raise_index_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, DocumentVersionNotFoundError):
        raise HTTPException(status_code=404, detail="Document version not found")
    if isinstance(exc, DocumentNotReadyForIndexError):
        raise HTTPException(status_code=409, detail="Document version is not ready for indexing")
    if isinstance(
        exc,
        (
            DocumentIndexingQueueError,
            SemanticSearchUnavailableError,
            HybridSearchUnavailableError,
        ),
    ):
        raise HTTPException(status_code=503, detail=str(exc))
    raise exc


@router.post(
    "/documents/{document_id}/versions/{version_id}/index",
    response_model=DocumentIndexRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_document_index(
    knowledge_base_id: UUID,
    document_id: UUID,
    version_id: UUID,
    body: DocumentIndexRequest,
    service: IndexingServiceDependency,
    response: Response,
) -> DocumentIndexRequestResponse:
    try:
        result = await service.request_index(
            knowledge_base_id, document_id, version_id, force=body.force
        )
    except Exception as exc:
        raise_index_http_error(exc)
    if not result.queued:
        response.status_code = status.HTTP_200_OK
    return DocumentIndexRequestResponse(
        queued=result.queued,
        version=index_status_response(result.version),
    )


@router.get(
    "/documents/{document_id}/versions/{version_id}/index-status",
    response_model=DocumentIndexStatusResponse,
)
async def get_document_index_status(
    knowledge_base_id: UUID,
    document_id: UUID,
    version_id: UUID,
    service: IndexingServiceDependency,
) -> DocumentIndexStatusResponse:
    try:
        version = await service.get_status(knowledge_base_id, document_id, version_id)
    except Exception as exc:
        raise_index_http_error(exc)
    return index_status_response(version)


@router.post("/search/semantic", response_model=SemanticSearchResponse)
async def semantic_search(
    knowledge_base_id: UUID,
    body: SemanticSearchRequest,
    service: IndexingServiceDependency,
) -> SemanticSearchResponse:
    try:
        results = await service.search(
            knowledge_base_id,
            query=body.query,
            limit=body.limit,
            language=body.language,
            document_id=body.document_id,
        )
    except Exception as exc:
        raise_index_http_error(exc)
    return SemanticSearchResponse(
        items=[SemanticSearchResultResponse.model_validate(result.__dict__) for result in results]
    )


@router.post(
    "/search/hybrid",
    response_model=SemanticSearchResponse,
    summary="Dense + BM25 RRF hybrid search",
    description="Returns Qdrant RRF ranking scores, not cosine similarity.",
)
async def hybrid_search(
    knowledge_base_id: UUID,
    body: SemanticSearchRequest,
    service: IndexingServiceDependency,
) -> SemanticSearchResponse:
    try:
        results = await service.hybrid_search(
            knowledge_base_id,
            query=body.query,
            limit=body.limit,
            language=body.language,
            document_id=body.document_id,
        )
    except Exception as exc:
        raise_index_http_error(exc)
    return SemanticSearchResponse(
        items=[SemanticSearchResultResponse.model_validate(result.__dict__) for result in results]
    )
