"""v1 라우터 조립."""

from fastapi import APIRouter

from app.api.v1 import (
    assistant,
    communities,
    contacts,
    incidents,
    meta,
    plans,
    predictions,
    public,
    situation,
    tasks,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(meta.router)
api_router.include_router(situation.router)
api_router.include_router(predictions.router)
api_router.include_router(incidents.router)
api_router.include_router(plans.router)
api_router.include_router(contacts.router)
api_router.include_router(tasks.router)
api_router.include_router(communities.router)
api_router.include_router(assistant.router)
api_router.include_router(public.router)
