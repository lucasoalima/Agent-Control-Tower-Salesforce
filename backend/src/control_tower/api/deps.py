"""Request dependencies: database session, wiring and the two kinds of caller.

There are two credential types and they are never interchangeable:

* **Operators** (humans) authenticate with a session cookie and can use the
  management API. They cannot submit tool requests.
* **Agents** authenticate with a bearer token and can only use the gateway API.
  They cannot read or change configuration, and (from Phase 2) cannot approve.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.clock import utcnow
from control_tower.core.operations import OperationCatalog
from control_tower.core.ports import Clock
from control_tower.persistence.models import Agent, Operator, OperatorSession
from control_tower.persistence.session import session_scope
from control_tower.platforms.registry import AdapterRegistry
from control_tower.security.tokens import AGENT_TOKEN_PREFIX, hash_token
from control_tower.settings import Settings

SESSION_COOKIE = "act_session"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

DbSession = Annotated[Session, Depends(session_scope)]


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_catalog(request: Request) -> OperationCatalog:
    return request.app.state.catalog  # type: ignore[no-any-return]


def get_adapters(request: Request) -> AdapterRegistry:
    return request.app.state.adapters  # type: ignore[no-any-return]


def get_clock(request: Request) -> Clock:
    return getattr(request.app.state, "clock", utcnow)


def get_correlation_id(request: Request) -> str:
    return request.state.correlation_id  # type: ignore[no-any-return]


AppSettings = Annotated[Settings, Depends(get_app_settings)]
Catalog = Annotated[OperationCatalog, Depends(get_catalog)]
Adapters = Annotated[AdapterRegistry, Depends(get_adapters)]
AppClock = Annotated[Clock, Depends(get_clock)]
CorrelationId = Annotated[str, Depends(get_correlation_id)]


def enforce_same_origin(request: Request, settings: AppSettings) -> None:
    """CSRF defence for cookie-authenticated requests.

    Browsers attach an ``Origin`` header to every cross-origin state-changing
    request. If one is present it must be an origin we serve the dashboard from.
    This complements the ``SameSite=Strict`` session cookie.
    """
    if request.method in _SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in settings.allowed_origin_set:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin not allowed.")


def require_operator(
    request: Request,
    session: DbSession,
    clock: AppClock,
    _: Annotated[None, Depends(enforce_same_origin)],
) -> Operator:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    row = session.scalar(
        select(OperatorSession).where(OperatorSession.token_hash == hash_token(token))
    )
    if (
        row is None
        or row.revoked_at is not None
        or row.expires_at <= clock()
        or not row.operator.is_active
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    request.state.operator_session = row
    return row.operator


def require_agent(request: Request, session: DbSession) -> Agent:
    """Authenticate an agent by bearer token.

    Disabled agents still authenticate, so that their requests are recorded and
    denied by policy rather than disappearing as anonymous 401s.
    """
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.startswith(AGENT_TOKEN_PREFIX):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "A valid agent token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    agent = session.scalar(select(Agent).where(Agent.token_hash == hash_token(token.strip())))
    if agent is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "A valid agent token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return agent


CurrentOperator = Annotated[Operator, Depends(require_operator)]
CurrentAgent = Annotated[Agent, Depends(require_agent)]
