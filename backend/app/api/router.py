from fastapi import APIRouter

from app.api.routes.archives import router as archives_router
from app.api.routes.consistency_audits import router as consistency_audits_router
from app.api.routes.consistency_repairs import router as consistency_repairs_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.documents import router as documents_router
from app.api.routes.health import router as health_router
from app.api.routes.indexing import router as indexing_router
from app.api.routes.knowledge_bases import router as knowledge_bases_router
from app.api.routes.knowledge_entries import router as knowledge_entries_router
from app.api.routes.knowledge_map import router as knowledge_map_router
from app.api.routes.rag import router as rag_router
from app.api.routes.rebuilds import router as rebuilds_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(archives_router)
api_router.include_router(conversations_router)
api_router.include_router(consistency_audits_router)
api_router.include_router(consistency_repairs_router)
api_router.include_router(documents_router)
api_router.include_router(indexing_router)
api_router.include_router(knowledge_bases_router)
api_router.include_router(knowledge_entries_router)
api_router.include_router(knowledge_map_router)
api_router.include_router(rag_router)
api_router.include_router(rebuilds_router)
