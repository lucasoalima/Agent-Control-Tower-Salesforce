from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from control_tower.api.deps import (
    SESSION_COOKIE,
    AppClock,
    AppSettings,
    CorrelationId,
    CurrentOperator,
    DbSession,
    enforce_same_origin,
)
from control_tower.api.schemas import LoginRequest, OperatorOut
from control_tower.core.enums import AuditActorType
from control_tower.logging import get_logger
from control_tower.persistence.audit import record_event
from control_tower.persistence.models import Operator, OperatorSession
from control_tower.security.passwords import verify_password
from control_tower.security.throttle import LoginThrottle
from control_tower.security.tokens import hash_token, new_session_token

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def get_throttle(request: Request) -> LoginThrottle:
    return request.app.state.login_throttle  # type: ignore[no-any-return]


@router.post(
    "/login",
    response_model=OperatorOut,
    dependencies=[Depends(enforce_same_origin)],
    summary="Sign in as an operator",
)
def login(
    body: LoginRequest,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    clock: AppClock,
    throttle: Annotated[LoginThrottle, Depends(get_throttle)],
    correlation_id: CorrelationId,
) -> Operator:
    key = body.username.lower()
    if throttle.is_blocked(key):
        record_event(
            session,
            "auth.login_throttled",
            actor_type=AuditActorType.ANONYMOUS,
            actor_id=None,
            correlation_id=correlation_id,
            occurred_at=clock(),
        )
        session.commit()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again later."
        )

    operator = session.scalar(select(Operator).where(Operator.username == body.username))
    password_ok = verify_password(operator.password_hash if operator else None, body.password)
    if operator is None or not password_ok or not operator.is_active:
        throttle.record_failure(key)
        record_event(
            session,
            "auth.login_failed",
            actor_type=AuditActorType.ANONYMOUS,
            actor_id=None,
            correlation_id=correlation_id,
            occurred_at=clock(),
        )
        session.commit()
        log.warning("auth.login_failed", username=body.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password.")

    throttle.reset(key)
    now = clock()
    token = new_session_token()
    ttl = timedelta(minutes=settings.session_ttl_minutes)
    session.add(
        OperatorSession(
            operator_id=operator.id,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=now + ttl,
        )
    )
    record_event(
        session,
        "auth.login",
        actor_type=AuditActorType.OPERATOR,
        actor_id=operator.id,
        correlation_id=correlation_id,
        occurred_at=now,
    )
    session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    log.info("auth.login", operator_id=str(operator.id))
    return operator


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
def logout(
    request: Request,
    response: Response,
    operator: CurrentOperator,
    session: DbSession,
    clock: AppClock,
    correlation_id: CorrelationId,
) -> None:
    now = clock()
    request.state.operator_session.revoked_at = now
    record_event(
        session,
        "auth.logout",
        actor_type=AuditActorType.OPERATOR,
        actor_id=operator.id,
        correlation_id=correlation_id,
        occurred_at=now,
    )
    session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    log.info("auth.logout", operator_id=str(operator.id))


@router.get("/me", response_model=OperatorOut, summary="The signed-in operator")
def me(operator: CurrentOperator) -> Operator:
    return operator
