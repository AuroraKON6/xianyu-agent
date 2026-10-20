"""Token authentication middleware (taskbook R03).

Resolves every incoming request to a :class:`~xianyu_agent.security.
policy.Principal` before business handlers run:

* ``Authorization: Bearer <token>`` is validated against the token
  registry (unknown -> 401, expired -> 401, wrong audience -> 403);
* a request carrying the Pi job-token header resolves to the
  read-only job session principal (never an operator);
* a token-less loopback request resolves to the local operator ONLY
  when local-operator mode is enabled and strict mode is off — any
  other token-less request is refused with 401.

Token records come from the ``XIANYU_AUTH_TOKENS`` env var (JSON list)
and/or ``<data_dir>/security/tokens.json``. Records carry either the
plaintext ``token`` (hashed in memory) or a pre-hashed
``token_sha256``; never log either.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Pattern

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .policy import (
    AuthDecisionError,
    AuthPolicy,
    JOB_SESSION,
    LOCAL_OPERATOR,
    Principal,
)

logger = logging.getLogger(__name__)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
JOB_TOKEN_HEADER = "X-Job-Token"


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TokenRegistry:
    """Immutable set of valid token records, validated at load time."""

    def __init__(self, records: Iterable[Mapping[str, Any]], *, clock: Optional[Callable[[], float]] = None) -> None:
        self._clock = clock or time.time
        self._policy = AuthPolicy()
        self._by_hash: Dict[str, Dict[str, Any]] = {}
        for record in records:
            self._policy.validate_token_record(record)
            token_hash = str(record.get("token_sha256") or "").strip()
            if not token_hash and str(record.get("token") or "").strip():
                token_hash = _sha256_hex(str(record["token"]))
            if not token_hash:
                raise AuthDecisionError(400, "token record needs token or token_sha256")
            if token_hash in self._by_hash:
                raise AuthDecisionError(400, "duplicate token hash in registry")
            self._by_hash[token_hash] = dict(record)

    @classmethod
    def from_sources(
        cls,
        *,
        env_json: str = "",
        file_path: Optional[Path] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> "TokenRegistry":
        records: List[Mapping[str, Any]] = []
        if env_json and env_json.strip():
            parsed = json.loads(env_json)
            if not isinstance(parsed, list):
                raise AuthDecisionError(400, "XIANYU_AUTH_TOKENS must be a JSON list")
            records.extend(parsed)
        if file_path is not None and Path(file_path).is_file():
            loaded = json.loads(Path(file_path).read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                raise AuthDecisionError(400, "token file must contain a JSON list")
            records.extend(loaded)
        return cls(records, clock=clock)

    def __len__(self) -> int:
        return len(self._by_hash)

    def authenticate(self, raw_token: str, *, audience: str = "http") -> Principal:
        record = self._by_hash.get(_sha256_hex(raw_token))
        if record is None:
            raise AuthDecisionError(401, "unknown or malformed token")
        expires_at = record.get("expires_at")
        if expires_at is not None:
            try:
                if self._clock() >= float(expires_at):
                    raise AuthDecisionError(401, "token expired")
            except (TypeError, ValueError):
                raise AuthDecisionError(400, "invalid expires_at in token record")
        record_audience = str(record.get("audience") or "http")
        if record_audience != audience:
            raise AuthDecisionError(
                403,
                "token audience %r does not match entry %r" % (record_audience, audience),
            )
        return Principal(
            kind=str(record["kind"]),
            name=str(record["name"]),
            scopes=frozenset(str(s) for s in (record.get("scopes") or ())),
            account_ids=frozenset(str(a) for a in (record.get("account_ids") or ())),
            audience=record_audience,
            source="token",
            expires_at=float(expires_at) if expires_at is not None else None,
        )


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve a principal per request; attach it to request.state."""

    def __init__(
        self,
        app,
        *,
        registry: TokenRegistry,
        audience: str = "http",
        local_operator_enabled: bool = True,
        strict: bool = False,
        exempt_paths: Iterable[str] = ("/health",),
        internal_auth_rules: Iterable[str] = (),
    ) -> None:
        super().__init__(app)
        self.registry = registry
        self.audience = audience
        self.local_operator_enabled = local_operator_enabled
        self.strict = strict
        self.exempt_paths = tuple(exempt_paths)
        # Legacy routers that enforce their own bearer/sha256 operator
        # auth (usage reconciliation, intake use-confirmation). Requests
        # to these paths bypass registry auth and carry a scope-less
        # internal principal; their own gates stay authoritative until
        # the versioned API (R17) unifies them.
        self.internal_auth_rules: tuple = tuple(
            re.compile(pattern) for pattern in internal_auth_rules
        )

    def _refuse(self, status_code: int, reason: str) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": reason})

    async def dispatch(self, request: Request, call_next):
        # app.state.auth_registry wins when present so deployment/tests can
        # rotate tokens without rebuilding the middleware stack
        registry = getattr(request.app.state, "auth_registry", None) or self.registry
        try:
            principal = self._resolve_principal(request, registry)
        except AuthDecisionError as exc:
            return self._refuse(exc.status_code, exc.reason)
        request.state.principal = principal
        return await call_next(request)

    def _resolve_principal(self, request: Request, registry: TokenRegistry) -> Principal:
        path = request.url.path
        if any(rule.match(path) for rule in self.internal_auth_rules):
            # the matched router authenticates with its own operator
            # credential; this principal holds no scopes so any new-style
            # dependency still refuses it
            return INTERNAL_LEGACY
        auth_header = (request.headers.get("authorization") or "").strip()
        job_token = (request.headers.get(JOB_TOKEN_HEADER) or "").strip()

        if auth_header:
            scheme, _, raw_token = auth_header.partition(" ")
            if scheme.lower() != "bearer" or not raw_token.strip():
                raise AuthDecisionError(401, "authorization header must carry a bearer token")
            # job-audience tokens are never valid on the HTTP entry and vice versa
            return registry.authenticate(raw_token.strip(), audience=self.audience)
        if job_token:
            # Pi job-tool session: the read-only job gate middleware polices
            # paths/methods; the auth layer only ensures it is never an
            # operator principal.
            return JOB_SESSION

        host = request.client.host if request.client else ""
        loopback = host.lower() in LOOPBACK_HOSTS
        if path in self.exempt_paths:
            # health stays observable, but never as an operator principal:
            # unauthenticated callers get the anonymous read-only view.
            return LOCAL_OPERATOR if (loopback and self.local_operator_enabled and not self.strict) else _ANONYMOUS
        if loopback and self.local_operator_enabled and not self.strict:
            return LOCAL_OPERATOR
        raise AuthDecisionError(401, "missing credentials")


# Health-only, scope-less principal: can read /health, nothing else.
_ANONYMOUS = Principal(
    kind="worker",
    name="anonymous-health",
    scopes=frozenset(),
    account_ids=frozenset(),
    audience="http",
    source="local",
)

# Scope-less principal for legacy routers that authenticate with their
# own operator credentials (internal_auth_rules).
INTERNAL_LEGACY = Principal(
    kind="worker",
    name="legacy-router-auth",
    scopes=frozenset(),
    account_ids=frozenset(),
    audience="http",
    source="local",
)
