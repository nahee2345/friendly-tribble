"""Authenticated same-origin FastAPI application for Wealthy."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.concurrency import run_in_threadpool

from asset_assistant.domain import DomainValidationError

from .api_schemas import (
    CredentialBody,
    LoginBody,
    PasswordChangeBody,
    PushDeleteBody,
    PushSubscriptionBody,
    StrategyPreviewBody,
    StrategyTargetBody,
)
from .application import WebApplicationService
from .config import ServerSettings
from .database import Database
from .repositories import (
    AuthRepository,
    CredentialRepository,
    JobRepository,
    MarketRepository,
    ReportRepository,
    ScenarioRepository,
    SessionRepository,
    PushSubscriptionRepository,
    StrategyTargetRepository,
)
from .security import PasswordManager, SecretVault, hash_token, issue_session_tokens, token_matches
from .worker import SEOUL, SCHEDULES
from .static_app import mount_frontend


LOGGER = logging.getLogger("wealthy.security")
SESSION_COOKIE = "wealthy_session"
CSRF_COOKIE = "wealthy_csrf"
_PROVIDER = re.compile(r"(?:bls|sec|opendart|ecos|kis)\Z")
_ENVIRONMENT = re.compile(r"(?:default|paper|live)\Z")
_REQUEST_BODY_LIMIT_BYTES = 2 * 1024 * 1024
_LOGIN_PAIR_FAILURE_LIMIT = 5
_LOGIN_CLIENT_FAILURE_LIMIT = 10
_LOGIN_WINDOW = timedelta(minutes=15)
_LOGIN_RETENTION = timedelta(hours=24)
_CREDENTIAL_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    ("bls", "default"): frozenset({"registration_key"}),
    ("sec", "default"): frozenset({"user_agent"}),
    ("opendart", "default"): frozenset({"api_key"}),
    ("ecos", "default"): frozenset({"api_key"}),
    ("kis", "paper"): frozenset({"app_key", "app_secret"}),
    ("kis", "live"): frozenset({"app_key", "app_secret"}),
}


@dataclass(frozen=True)
class Actor:
    owner_id: str
    username: str
    token_hash: str
    csrf_hash: str


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Reject oversized fixed-length and streamed requests before parsing."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except (TypeError, ValueError):
                declared_length = -1
            if declared_length > self.max_bytes:
                await self._reject(send)
                return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if not response_started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        payload = (
            b'{"detail":{"code":"REQUEST_TOO_LARGE",'
            b'"message":"Request size limit exceeded."}}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _fingerprint(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8", "replace"), hashlib.sha256).hexdigest()


def _same_origin(request: Request, public_origin: str | None = None) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        # Non-browser admin/test clients do not always send Origin.  Browser
        # state changes still require the session-bound CSRF token below.
        return request.headers.get("sec-fetch-site", "same-origin") in {"same-origin", "none"}
    expected = public_origin or f"{request.url.scheme}://{request.headers.get('host', '')}"
    return hmac.compare_digest(origin.rstrip("/"), expected.rstrip("/"))


def create_app(
    settings: ServerSettings,
    *,
    database: Database | None = None,
    clock=lambda: datetime.now(UTC),
    local_mode: bool = False,
    credential_repository: Any | None = None,
    report_repository: Any | None = None,
    job_repository: Any | None = None,
    market_repository: Any | None = None,
    local_owner_guard: Callable[[str | None], None] | None = None,
    market_information_service: Any | None = None,
    market_information_auto_start: bool = False,
    frontend_dir: Path | None = None,
) -> FastAPI:
    """Build an app with injectable persistence and clock for deterministic tests."""

    db = database or Database(settings.database_url)
    if local_mode:
        db.create_local_schema()
    else:
        db.create_schema()
    password_manager = PasswordManager()
    dummy_password_hash = password_manager.hash(secrets.token_urlsafe(32))
    vault = SecretVault({"v1": settings.master_key}, active_key_version="v1")
    auth = AuthRepository(db.session_factory)
    sessions = SessionRepository(db.session_factory)
    scenarios = ScenarioRepository(db.session_factory)
    if local_mode and any(
        item is None
        for item in (
            credential_repository,
            report_repository,
            job_repository,
            market_repository,
            local_owner_guard,
        )
    ):
        raise DomainValidationError("로컬 저장소 어댑터 설정이 필요합니다.")
    reports = report_repository or ReportRepository(db.session_factory)
    targets = StrategyTargetRepository(db.session_factory)
    credentials = credential_repository or CredentialRepository(db.session_factory, vault)
    push_subscriptions = (
        None if local_mode else PushSubscriptionRepository(db.session_factory, vault)
    )
    jobs = job_repository or JobRepository(db.session_factory)
    market = market_repository or MarketRepository(db.session_factory)
    service = WebApplicationService(
        scenarios=scenarios,
        reports=reports,
        strategy_targets=targets,
        credentials=credentials,
        jobs=jobs,
        market=market,
        clock=clock,
    )

    app = FastAPI(
        title="Wealthy private API",
        version="0.3.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=_REQUEST_BODY_LIMIT_BYTES)
    app.state.database = db
    app.state.settings = settings
    app.state.local_mode = local_mode
    app.state.repositories = {
        "auth": auth,
        "sessions": sessions,
        "credentials": credentials,
        "jobs": jobs,
    }
    if push_subscriptions is not None:
        app.state.repositories["push_subscriptions"] = push_subscriptions
    app.state.service = service
    app.state.market_information_service = market_information_service

    if market_information_service is not None and market_information_auto_start:
        # FastAPI 0.135 exposes lifecycle handler registration on the router.
        # Keeping this behind the explicit flag leaves network-free fixtures
        # idle while the real local ``serve`` path starts and stops the loop.
        app.router.add_event_handler(
            "startup", market_information_service.start_background
        )
        app.router.add_event_handler(
            "shutdown", market_information_service.stop_background
        )

    def require_local_owner(owner_id: str | None = None) -> None:
        if not local_mode:
            return
        if local_owner_guard is None:  # pragma: no cover - guarded above
            raise DomainValidationError("로컬 사용자 상태를 확인할 수 없습니다.")
        local_owner_guard(owner_id)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "입력 형식을 확인해 주세요. 입력값은 오류에 표시하지 않습니다.",
                }
            },
        )

    @app.exception_handler(DomainValidationError)
    async def domain_error(request: Request, exc: DomainValidationError):
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_INPUT", "message": str(exc)}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if not isinstance(detail, dict) or set(detail) != {"code", "message"}:
            detail = {"code": "REQUEST_FAILED", "message": "요청을 완료할 수 없습니다."}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        LOGGER.error("request_failed event=UNEXPECTED path_class=api", exc_info=False)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "작업을 완료할 수 없습니다. 잠시 후 다시 확인해 주세요.",
                }
            },
        )

    def current_actor(request: Request) -> Actor:
        require_local_owner()
        raw_token = request.cookies.get(SESSION_COOKIE, "")
        if not raw_token:
            raise _error(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
        token_hash = hash_token(raw_token)
        web_session = sessions.authenticate(token_hash, now=clock())
        if web_session is None:
            raise _error(401, "AUTH_REQUIRED", "세션이 만료되었거나 해제되었습니다.")
        user = auth.get_user_by_id(web_session.owner_id)
        if user is None or not user.is_active:
            raise _error(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
        require_local_owner(user.id)
        return Actor(user.id, user.username, token_hash, web_session.csrf_hash)

    def mutation_actor(
        request: Request,
        actor: Actor = Depends(current_actor),
        x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> Actor:
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if (
            not _same_origin(request, settings.public_origin)
            or not cookie
            or not x_csrf_token
            or not hmac.compare_digest(cookie, x_csrf_token)
            or not token_matches(x_csrf_token, actor.csrf_hash)
        ):
            raise _error(403, "CSRF_REJECTED", "요청 검증에 실패했습니다.")
        return actor

    # Isolated Stage 1 virtual cash ledger. No broker permissions and no live orders.
    if local_mode:
        from .stage1_paper import Stage1Ledger
        from pydantic import BaseModel, Field

        class VirtualFXBody(BaseModel):
            krw: int = Field(gt=0, le=10_000_000)
            usd_krw: str
            request_id: str

        stage1 = Stage1Ledger(Path(db.engine.url.database).parent / "stage1-paper.sqlite3")

        from .stage1_portfolios import Portfolios
        portfolio_store = Portfolios(Path(db.engine.url.database).parent / "stage1-paper.sqlite3")

        class NewPortfolioBody(BaseModel):
            name: str = Field(min_length=1, max_length=40)
            kind: str

        from .stage1_discovery import PreferenceStore, shortlist
        preference_store = PreferenceStore(Path(db.engine.url.database).parent / "stage1-paper.sqlite3")
        from .stage1_bootstrap import initialize_paper_accounts
        if market_information_auto_start:
            from .stage1_worker import PaperWorker
            paper_worker = PaperWorker(db, stage1, portfolio_store, preference_store)
            app.state.paper_worker = paper_worker
            app.router.add_event_handler("startup", paper_worker.start)
            app.router.add_event_handler("shutdown", paper_worker.stop)


        class AllocationBody(BaseModel):
            us_weight: int = Field(ge=0, le=100)
            etf_weight: int = Field(ge=0, le=100)

        class FundingAllocationBody(BaseModel):
            us_pct: int = Field(ge=0, le=100)
            kr_pct: int = Field(ge=0, le=100)
            isa_pct: int = Field(ge=0, le=100)

        @app.get("/api/v1/stage1/funding-allocation")
        def stage1_get_funding_allocation(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            from .stage1_bootstrap import get_funding_allocation
            return get_funding_allocation(actor.owner_id, portfolio_store)

        @app.post("/api/v1/stage1/funding-allocation")
        def stage1_set_funding_allocation(body: FundingAllocationBody,
                                          actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
            from .stage1_bootstrap import set_funding_allocation
            try:
                return set_funding_allocation(actor.owner_id, portfolio_store,
                                              body.us_pct, body.kr_pct, body.isa_pct)
            except ValueError:
                raise _error(400, "INVALID_FUNDING_ALLOCATION", "배분 비율의 합계가 100%여야 합니다.") from None

        @app.get("/api/v1/stage1/preferences")
        def stage1_preferences(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            return preference_store.get(actor.owner_id)

        @app.post("/api/v1/stage1/preferences")
        def stage1_update_preferences(body: AllocationBody, actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
            return preference_store.save(actor.owner_id, body.us_weight, body.etf_weight)

        from .stage1_strategy_policy import StrategyPolicy, DEFAULT_WEIGHTS
        stage1_strategy_policy = StrategyPolicy(portfolio_store)

        class StrategyWeightsBody(BaseModel):
            weights: dict[str, int]

        @app.get("/api/v1/stage1/strategy-weights")
        def stage1_strategy_weights(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            return stage1_strategy_policy.get(actor.owner_id)

        @app.post("/api/v1/stage1/strategy-weights")
        def stage1_set_strategy_weights(body: StrategyWeightsBody,
                                        actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
            try:
                return stage1_strategy_policy.save(actor.owner_id, body.weights)
            except ValueError:
                raise _error(400, "INVALID_STRATEGY_WEIGHTS",
                             "8개 투자전략의 정수 비율을 합계 100%로 입력하세요.") from None

        @app.get("/api/v1/stage1/decisions")
        def stage1_decisions(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            from .stage1_decisions import proposal
            initialize_paper_accounts(actor.owner_id, stage1, portfolio_store)
            return proposal(actor.owner_id, portfolio_store, preference_store, stage1_strategy_policy)

        @app.get("/api/v1/stage1/research/{symbol}")
        def stage1_research(symbol: str, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            from .stage1_analytics import fetch_daily_history, research_summary
            from .stage1_discovery import CANDIDATES
            # Bounded, maintained set of symbols; avoids an arbitrary symbol scraping proxy.
            allowed = {row[0] for row in CANDIDATES}
            if symbol not in allowed:
                raise _error(400, "RESEARCH_SYMBOL_NOT_ALLOWED", "분석 대상 목록에서 선택해 주세요.")
            return research_summary(fetch_daily_history(symbol), fees_bps=10)

        @app.get("/api/v1/stage1/candidates")
        def stage1_candidates(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            return shortlist(preference_store.get(actor.owner_id))

        @app.get("/api/v1/stage1/portfolios")
        def stage1_portfolios(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            return initialize_paper_accounts(actor.owner_id, stage1, portfolio_store)

        @app.post("/api/v1/stage1/portfolios")
        def stage1_create_portfolio(body: NewPortfolioBody, actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
            try:
                return portfolio_store.create(actor.owner_id, body.name, body.kind)
            except ValueError:
                raise _error(400, "INVALID_PORTFOLIO", "가상계좌 이름 또는 유형을 확인해 주세요.") from None

        @app.get("/api/v1/stage1/portfolios/{portfolio_id}/orders")
        def stage1_orders(portfolio_id: str, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            try:
                return portfolio_store.orders(actor.owner_id, portfolio_id)
            except ValueError:
                raise _error(404, "PORTFOLIO_NOT_FOUND", "가상계좌를 찾을 수 없습니다.") from None

        class TransferBody(BaseModel):
            source: str
            destination: str
            amount_krw: int = Field(gt=0, le=100_000_000)
            request_id: str

        @app.post("/api/v1/stage1/transfer")
        def stage1_transfer(body: TransferBody, actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
            try:
                return portfolio_store.transfer(actor.owner_id, body.source, body.destination, body.amount_krw, body.request_id)
            except ValueError:
                raise _error(400, "INVALID_TRANSFER", "가상 계좌·잔액 또는 요청 정보를 확인해 주세요.") from None

        class VirtualOrderBody(BaseModel):
            portfolio: str
            symbol: str
            side: str
            quantity: int = Field(gt=0, le=100_000)
            request_id: str

        @app.get("/api/v1/stage1/quote/{symbol}")
        def stage1_quote(symbol: str, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            from .stage1_quotes import verified_quote
            return verified_quote(symbol)

        @app.post("/api/v1/stage1/order")
        def stage1_order(body: VirtualOrderBody, actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
            from .stage1_quotes import verified_quote
            try:
                quote=verified_quote(body.symbol)
                return portfolio_store.trade(actor.owner_id, body.portfolio, body.symbol, body.side, body.quantity, body.request_id, quote)
            except (ValueError, KeyError, ArithmeticError):
                raise _error(400, "PAPER_ORDER_REJECTED", "시세·계좌·잔고 또는 주문 정보를 확인해 주세요.") from None

        @app.get("/api/v1/stage1/news/{region}")
        def stage1_news(region: str, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            from .stage1_research import news_headlines
            if region not in ("KR", "US"):
                raise _error(400, "INVALID_REGION", "지원하지 않는 시장입니다.")
            return news_headlines(region)

        @app.get("/api/v1/stage1/fx")
        def stage1_fx(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            from .stage1_research import current_fx
            return current_fx()

        @app.get("/api/v1/stage1/account-eligibility")
        def stage1_account_eligibility(instrument: str, market: str, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            from .stage1_research import account_eligibility
            return account_eligibility(instrument, market)

        @app.post("/api/v1/stage1/auto-run")
        def stage1_auto_run(actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
            """A single explicitly requested simulated ETF investment; no brokerage."""
            from .stage1_autorun import paper_run_once
            try:
                return paper_run_once(actor.owner_id, stage1, portfolio_store, preference_store)
            except (ValueError, KeyError, ArithmeticError):
                raise _error(400, "PAPER_AUTO_REJECTED", "가상 자동주문 검증에 실패했습니다.") from None

        @app.get("/api/v1/stage1/daily-reports")
        def stage1_daily_reports(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            from .stage1_diary import list_daily_paper_reports
            return list_daily_paper_reports(actor.owner_id, portfolio_store, limit=30)

        @app.get("/api/v1/stage1/performance")
        def stage1_performance(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            from .stage1_valuation import portfolio_report
            from .stage1_bootstrap import initialize_paper_accounts
            initialize_paper_accounts(actor.owner_id, stage1, portfolio_store)
            try:
                return portfolio_report(actor.owner_id, portfolio_store, stage1)
            except (ValueError, KeyError, ArithmeticError):
                raise _error(422, "VALUATION_INCOMPLETE",
                             "시세 또는 거래 원장을 평가할 수 없습니다.") from None

        @app.get("/api/v1/stage1/auto-status")
        def stage1_auto_status(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            worker = getattr(app.state, "paper_worker", None)
            return {
                "enabled": bool(worker and worker.thread and worker.thread.is_alive()),
                "interval_minutes": 60,
                "last_result": worker.last_result if worker else {"status": "disabled"},
                "last_checked_at": worker.last_checked_at if worker else None,
                "live_broker_connected": False,
            }

        @app.get("/api/v1/stage1/account")
        def stage1_account(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
            state = stage1.state(actor.owner_id)
            state["auto_trading_enabled"] = bool(
                getattr(app.state, "paper_worker", None)
                and app.state.paper_worker.thread is not None
                and app.state.paper_worker.thread.is_alive()
            )
            return state

        @app.post("/api/v1/stage1/exchange")
        def stage1_exchange(body: VirtualFXBody, actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
            try:
                return stage1.exchange(actor.owner_id, body.krw, body.usd_krw, body.request_id)
            except ValueError:
                raise _error(400, "INVALID_VIRTUAL_EXCHANGE", "가상 환전 입력이나 잔액을 확인해 주세요.") from None

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/session")
    def session_status(request: Request) -> dict[str, Any]:
        try:
            actor = current_actor(request)
        except HTTPException:
            return {"authenticated": False, "user": None}
        return {
            "authenticated": True,
            "user": {"id": actor.owner_id, "username": actor.username},
        }

    @app.post("/api/v1/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
        if not _same_origin(request, settings.public_origin):
            raise _error(403, "ORIGIN_REJECTED", "요청 출처를 확인할 수 없습니다.")
        require_local_owner()
        now = clock()
        normalized_name = body.username.strip().casefold()
        client_value = request.client.host if request.client is not None else "unknown"
        username_fp = _fingerprint(settings.master_key, normalized_name)
        client_fp = _fingerprint(settings.master_key, client_value)
        attempt_id = auth.claim_login_attempt(
            username_fingerprint=username_fp,
            client_fingerprint=client_fp,
            attempted_at=now,
            since=now - _LOGIN_WINDOW,
            prune_before=now - _LOGIN_RETENTION,
            max_pair_failures=_LOGIN_PAIR_FAILURE_LIMIT,
            max_client_failures=_LOGIN_CLIENT_FAILURE_LIMIT,
        )
        if attempt_id is None:
            raise _error(429, "LOGIN_RATE_LIMITED", "잠시 후 다시 시도해 주세요.")
        try:
            user = auth.get_user_by_username(normalized_name)
        except DomainValidationError:
            user = None
        password_accepted = password_manager.verify(
            user.password_hash if user is not None and user.is_active else dummy_password_hash,
            body.password,
        )
        accepted = bool(user is not None and user.is_active and password_accepted)
        if not accepted or user is None:
            raise _error(401, "LOGIN_FAILED", "사용자 이름 또는 비밀번호를 확인해 주세요.")
        require_local_owner(user.id)
        auth.mark_login_attempt_succeeded(attempt_id, owner_id=user.id)
        tokens = issue_session_tokens()
        sessions.create(
            user.id,
            token_hash=tokens.session_token_hash,
            csrf_hash=tokens.csrf_token_hash,
            created_at=now,
            expires_at=now + timedelta(hours=settings.session_hours),
        )
        response.set_cookie(
            SESSION_COOKIE,
            tokens.session_token,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            max_age=settings.session_hours * 3600,
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            tokens.csrf_token,
            httponly=False,
            secure=settings.secure_cookies,
            samesite="strict",
            max_age=settings.session_hours * 3600,
            path="/",
        )
        return {"authenticated": True, "user": {"id": user.id, "username": user.username}}

    @app.post("/api/v1/logout")
    def logout(
        response: Response,
        actor: Actor = Depends(mutation_actor),
    ) -> dict[str, bool]:
        sessions.revoke(actor.owner_id, actor.token_hash, revoked_at=clock())
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/v1/password")
    def change_password(
        body: PasswordChangeBody,
        response: Response,
        actor: Actor = Depends(mutation_actor),
    ) -> dict[str, bool]:
        user = auth.get_user_by_id(actor.owner_id)
        if user is None or not password_manager.verify(user.password_hash, body.current_password):
            raise _error(400, "PASSWORD_CHANGE_FAILED", "현재 비밀번호를 확인해 주세요.")
        changed_at = clock()
        auth.change_password(
            actor.owner_id,
            password_manager.hash(body.new_password),
            changed_at=changed_at,
        )
        sessions.revoke_all(actor.owner_id, revoked_at=changed_at)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/v1/status")
    def status(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        now = clock().astimezone(SEOUL)

        def next_occurrence(hour_name: str) -> str:
            candidate = datetime.combine(now.date(), SCHEDULES[hour_name], tzinfo=SEOUL)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.isoformat()

        return {
            "mode": (
                "LOCAL_WEB_READ_ONLY_ANALYSIS"
                if local_mode
                else "PRIVATE_WEB_READ_ONLY_ANALYSIS"
            ),
            "orders": "NOT_IMPLEMENTED",
            "orders_enabled": False,
            "account_access": "NOT_IMPLEMENTED",
            "scheduler": (
                "LOCAL_SYSTEMD_STATUS_REQUIRES_CHECK"
                if local_mode
                else "SERVER_WORKER_AVAILABLE_NOT_PROVEN_RUNNING"
            ),
            "timezone": "Asia/Seoul",
            "next_collection_at": next_occurrence("collect"),
            "next_report_at": next_occurrence("report"),
            "registration_enabled": False,
            "pwa_push_delivery": "DISABLED" if local_mode else "NOT_IMPLEMENTED",
            "browser_notifications": "PAGE_OPEN_ONLY" if local_mode else "NOT_IMPLEMENTED",
            "database": "CONNECTED_LOCAL_SQLITE" if local_mode else "CONNECTED",
            "providers": service.provider_statuses(actor.owner_id),
        }

    @app.get("/api/v1/dashboard")
    def dashboard(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return service.dashboard(actor.owner_id)

    @app.get("/api/v1/portfolio")
    def portfolio(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return service.portfolio_view(actor.owner_id)

    @app.get("/api/v1/notifications")
    def notifications(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return {
            "items": service.notifications_view(actor.owner_id),
            "browser_notifications": "PAGE_OPEN_ONLY",
        }

    @app.post("/api/v1/scenarios/sample")
    def sample(actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
        stored = service.import_sample(actor.owner_id)
        return {
            "scenario_id": stored.scenario_id,
            "report_id": stored.report_id,
            "dashboard": stored.dashboard,
        }

    @app.post("/api/v1/scenarios/upload")
    async def upload_scenario(
        file: UploadFile = File(...),
        actor: Actor = Depends(mutation_actor),
    ) -> dict[str, Any]:
        try:
            source = await file.read(settings.upload_limit_bytes + 1)
        finally:
            await file.close()
        if len(source) > settings.upload_limit_bytes:
            raise _error(413, "UPLOAD_TOO_LARGE", "업로드 크기 제한을 넘었습니다.")
        stored = service.import_scenario(actor.owner_id, source)
        return {
            "scenario_id": stored.scenario_id,
            "report_id": stored.report_id,
            "dashboard": stored.dashboard,
        }

    @app.post("/api/v1/reports/generate")
    def generate_report(actor: Actor = Depends(mutation_actor)) -> dict[str, Any]:
        return service.generate_report(actor.owner_id)

    @app.get("/api/v1/reports")
    def report_list(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return {"items": service.reports_view(actor.owner_id)}

    @app.get("/api/v1/reports/{report_id}")
    def report_detail(report_id: int, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        report = service.report(actor.owner_id, report_id)
        if report is None:
            raise _error(404, "NOT_FOUND", "보고서를 찾을 수 없습니다.")
        return report

    @app.get("/api/v1/strategy-targets/current")
    def current_target(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return {"target": service.current_strategy(actor.owner_id)}

    @app.get("/api/v1/strategy-targets/history")
    def target_history(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return {"items": service.strategy_history(actor.owner_id)}

    @app.post("/api/v1/strategy-targets/preview")
    def preview_target(
        body: StrategyPreviewBody,
        actor: Actor = Depends(current_actor),
    ) -> dict[str, Any]:
        dashboard_data = service.dashboard(actor.owner_id)
        total_assets_krw = (
            dashboard_data.get("investable_total_krw")
            if dashboard_data.get("asset_total_status") == "KNOWN"
            else None
        )
        return service.strategy_preview(body.weights, total_assets_krw=total_assets_krw)

    @app.post("/api/v1/strategy-targets")
    def save_target(
        body: StrategyTargetBody,
        actor: Actor = Depends(mutation_actor),
    ) -> dict[str, Any]:
        return service.save_strategy(actor.owner_id, version=body.version, weights=body.weights)

    @app.get("/api/v1/strategy-catalog")
    def catalog(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return service.strategy_catalog_view()

    def checked_credential_identity(provider: str, environment: str) -> frozenset[str]:
        if _PROVIDER.fullmatch(provider) is None or _ENVIRONMENT.fullmatch(environment) is None:
            raise _error(404, "NOT_FOUND", "지원되는 공급자 설정이 아닙니다.")
        fields = _CREDENTIAL_FIELDS.get((provider, environment))
        if fields is None:
            raise _error(404, "NOT_FOUND", "지원되는 공급자 설정이 아닙니다.")
        return fields

    @app.get("/api/v1/credentials")
    def credential_statuses(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for (provider, environment), fields in _CREDENTIAL_FIELDS.items():
            statuses = [
                credentials.status(
                    actor.owner_id,
                    provider=provider,
                    environment=environment,
                    secret_name=field,
                )
                for field in sorted(fields)
            ]
            items.append(
                {
                    "provider": provider,
                    "environment": environment,
                    "configured_fields": tuple(item.secret_name for item in statuses if item.configured),
                    "required_fields": tuple(sorted(fields)),
                    "configured": all(item.configured for item in statuses),
                    "updated_at": max(
                        (item.updated_at for item in statuses if item.updated_at is not None),
                        default=None,
                    ),
                }
            )
        storage_status_method = getattr(credentials, "storage_status", None)
        storage_status = (
            storage_status_method()
            if storage_status_method is not None
            else {
                "available": True,
                "confirmed": True,
                "state": "AVAILABLE_CONFIRMED",
                "backend": "SERVER_ENCRYPTED_DATABASE",
                "plaintext_fallback": False,
                "checked_at": None,
                "safe_recheck_allowed": False,
            }
        )
        return {
            "items": [
                {**item, "updated_at": item["updated_at"].isoformat() if item["updated_at"] else None}
                for item in items
            ],
            "notice": "원문 비밀은 조회 API로 반환하지 않습니다.",
            "storage": storage_status,
        }

    @app.put("/api/v1/credentials/{provider}/{environment}")
    def put_credentials(
        provider: str,
        environment: str,
        body: CredentialBody,
        actor: Actor = Depends(mutation_actor),
    ) -> dict[str, Any]:
        fields = checked_credential_identity(provider, environment)
        if set(body.values) != set(fields):
            raise _error(400, "INVALID_CREDENTIAL_FIELDS", "필수 인증정보 항목을 확인해 주세요.")
        for field in sorted(fields):
            credentials.upsert(
                actor.owner_id,
                provider=provider,
                environment=environment,
                secret_name=field,
                plaintext=body.values[field],
                now=clock(),
            )
        return {
            "provider": provider,
            "environment": environment,
            "configured": True,
            "connection_test": "NOT_RUN",
        }

    @app.delete("/api/v1/credentials/{provider}/{environment}")
    def delete_credentials(
        provider: str,
        environment: str,
        actor: Actor = Depends(mutation_actor),
    ) -> dict[str, Any]:
        fields = checked_credential_identity(provider, environment)
        for field in fields:
            credentials.delete(
                actor.owner_id,
                provider=provider,
                environment=environment,
                secret_name=field,
            )
        return {"provider": provider, "environment": environment, "configured": False}

    @app.get("/api/v1/jobs")
    def job_list(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        return {"items": service.jobs_view(actor.owner_id)}

    def require_market_information():  # type: ignore[no-untyped-def]
        if market_information_service is None:
            raise _error(
                503,
                "MARKET_INFORMATION_UNAVAILABLE",
                "시장정보 저장소를 사용할 수 없습니다.",
            )
        return market_information_service

    @app.get("/api/v1/market-information")
    def market_information_list(
        q: str = Query(default="", max_length=100),
        category: str = Query(default="ALL", max_length=20),
        related: str = Query(default="ALL", max_length=20),
        provider: str = Query(default="", max_length=50),
        sort: str = Query(default="PUBLISHED_DESC", max_length=30),
        cursor: str = Query(default="", max_length=500),
        limit: int = Query(default=30, ge=1, le=100),
        snapshot: str = Query(default="", max_length=80),
        actor: Actor = Depends(current_actor),
    ) -> dict[str, Any]:
        market_service = require_market_information()
        result = market_service.list_items(
            q=q,
            category=category,
            related=related,
            provider=provider,
            sort=sort,
            cursor=cursor,
            limit=limit,
            snapshot=snapshot,
        )
        # A holding marker is allowed only for verified MANUAL_ACTUAL ledger
        # identifiers.  SAMPLE/paper/missing realms remain UNKNOWN.
        portfolio_data = service.portfolio_view(actor.owner_id)
        held: set[str] = set()
        if portfolio_data.get("data_scope") == "MANUAL_ACTUAL":
            for position in portfolio_data.get("positions", ()):
                if not isinstance(position, dict):
                    continue
                identifier = position.get("instrument_id")
                if isinstance(identifier, str) and identifier:
                    held.add(identifier.upper())
        for item in result.get("items", ()):
            symbols = {
                str(value).upper()
                for value in item.get("related_symbols", ())
                if isinstance(value, str)
            }
            item["holding_status"] = "HELD" if symbols & held else "UNKNOWN"
        return result

    @app.get("/api/v1/market-information/status")
    def market_information_status(
        actor: Actor = Depends(current_actor),
    ) -> dict[str, Any]:
        del actor
        return require_market_information().status()

    @app.get("/api/v1/market-information/retention")
    def market_information_retention(
        status: str = Query(default="EXPIRED", max_length=30),
        cursor: str = Query(default="", max_length=500),
        limit: int = Query(default=20, ge=1, le=100),
        snapshot: str = Query(default="", max_length=80),
        actor: Actor = Depends(current_actor),
    ) -> dict[str, Any]:
        del actor
        return require_market_information().retention_preview(
            status=status,
            cursor=cursor,
            limit=limit,
            snapshot=snapshot,
        )

    @app.post("/api/v1/market-information/refresh")
    async def market_information_refresh(
        actor: Actor = Depends(mutation_actor),
    ) -> dict[str, Any]:
        del actor
        return await run_in_threadpool(require_market_information().refresh, manual=True)

    @app.get("/api/v1/push/public-key")
    def push_public_key(actor: Actor = Depends(current_actor)) -> dict[str, Any]:
        if local_mode:
            return {
                "configured": False,
                "public_key": None,
                "status": "DISABLED_LOCAL_ONLY",
                "notice": "외부 Web Push는 비활성입니다. 페이지가 열린 동안의 알림만 지원합니다.",
            }
        return {
            "configured": settings.vapid_public_key is not None,
            "public_key": settings.vapid_public_key,
            "status": (
                "SUBSCRIPTION_AVAILABLE_DELIVERY_NOT_IMPLEMENTED"
                if settings.vapid_public_key is not None
                else "VAPID_NOT_CONFIGURED"
            ),
            "notice": "구독 저장과 실제 기기 발송·수신 검증은 별도 상태입니다.",
        }

    def checked_push_endpoint(endpoint: str) -> str:
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            port = parsed.port
        except (TypeError, ValueError):
            raise _error(400, "INVALID_PUSH_SUBSCRIPTION", "알림 구독 형식을 확인해 주세요.") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or parsed.fragment
        ):
            raise _error(400, "INVALID_PUSH_SUBSCRIPTION", "알림 구독 형식을 확인해 주세요.")
        return endpoint

    @app.post("/api/v1/push/subscriptions")
    def add_push_subscription(
        body: PushSubscriptionBody,
        actor: Actor = Depends(mutation_actor),
    ) -> dict[str, Any]:
        if local_mode or push_subscriptions is None:
            raise _error(410, "PUSH_DISABLED", "외부 Web Push는 비활성입니다.")
        if settings.vapid_public_key is None or settings.vapid_private_key is None:
            raise _error(503, "VAPID_NOT_CONFIGURED", "서버 Web Push 키 설정이 필요합니다.")
        endpoint_hash = push_subscriptions.upsert(
            actor.owner_id,
            endpoint=checked_push_endpoint(body.endpoint),
            keys=body.keys,
            device_label=body.device_label,
            created_at=clock(),
        )
        return {
            "endpoint_hash": endpoint_hash,
            "delivery_status": "NOT_TESTED",
        }

    @app.delete("/api/v1/push/subscriptions")
    def delete_push_subscription(
        body: PushDeleteBody,
        actor: Actor = Depends(mutation_actor),
    ) -> dict[str, bool]:
        if local_mode or push_subscriptions is None:
            raise _error(410, "PUSH_DISABLED", "외부 Web Push는 비활성입니다.")
        removed = push_subscriptions.delete_by_endpoint(
            actor.owner_id,
            endpoint=checked_push_endpoint(body.endpoint),
        )
        return {"removed": removed}

    if frontend_dir is not None:
        mount_frontend(app, frontend_dir)
    return app
