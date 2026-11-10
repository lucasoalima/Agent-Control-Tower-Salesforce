from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from control_tower.api.deps import DbSession

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness: the process is up")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness: the database is reachable")
def readyz(session: DbSession) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database unavailable.") from exc
    return {"status": "ok"}
