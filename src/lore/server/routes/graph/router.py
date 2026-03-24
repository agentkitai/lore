"""Creates and configures the main graph APIRouter, including all sub-routes."""

from __future__ import annotations

from fastapi import APIRouter

from . import entities, memories, stats, topics

router = APIRouter(prefix="/v1/ui", tags=["graph"])

router.include_router(memories.router)
router.include_router(entities.router)
router.include_router(topics.router)
router.include_router(stats.router)
