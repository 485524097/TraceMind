from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.knowledge_bases import router as knowledge_bases_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(knowledge_bases_router)
