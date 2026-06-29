from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
import inspect
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.admin_panel.auth import (
    AdminAuthError,
    AdminIdentity,
    authenticate_admin_session,
    authorize_bootstrap_login,
    authorize_telegram_login,
    parse_admin_telegram_ids,
)
from services.admin_panel.constants import (
    KILL_SWITCH_GLOBAL_KEY,
    KILL_SWITCH_USER_PREFIX,
    NOTIFY_STREAM,
    PAYMENTS_STREAM,
)
from services.admin_panel.events import (
    build_notify_admin_precheck_payload,
    build_notify_user_payment_approved_payload,
    build_notify_user_payment_rejected_payload,
    build_payment_approved_payload,
    build_payment_rejected_payload,
)
from services.admin_panel.health import check_health as check_admin_panel
from services.admin_panel.precheck import precheck_payment
from services.admin_panel.repository import OverviewMetrics
from services.admin_panel.sessions import AdminSessionStore
from services.core_engine.health import check_health as check_core_engine
from services.demo_engine.health import check_health as check_demo_engine
from services.signal_ingestor.health import check_health as check_signal_ingestor
from services.telegram_bot.health import check_health as check_telegram_bot


_ADMIN_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_ADMIN_DIR / "templates"))
_TEMPLATES.env.filters["utc"] = lambda value: _format_utc(value)
_TEMPLATES.env.filters["money"] = lambda value: _format_decimal(value, places=2)
_TEMPLATES.env.filters["price"] = lambda value: _format_decimal(value, places=8)
_TEMPLATES.env.filters["duration"] = lambda value: _format_duration(value)
_TEMPLATES.env.filters["short_txid"] = lambda value: _short_txid(value)
_TEMPLATES.env.globals["payment_explorer_url"] = lambda payment: (
    _payment_explorer_url(payment)
)


def create_admin_router(
    *,
    repository_factory: Any,
    publisher: Any,
    redis_client: Any,
    config: Any,
    session_store: AdminSessionStore,
) -> APIRouter:
    """Create session-protected admin panel routes."""
    router = APIRouter()
    configured_ids = _admin_telegram_ids(config)

    async def require_admin(request: Request) -> AdminIdentity:
        try:
            return await authenticate_admin_session(
                request,
                session_store=session_store,
                admin_telegram_ids=configured_ids,
                ip_allowlist=tuple(_config_value(config, "ip_allowlist", ())),
                require_ip_allowlist=bool(
                    _config_value(config, "require_ip_allowlist", False)
                ),
            )
        except AdminAuthError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "admin_panel"}

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "auth_mode": _config_value(config, "auth_mode", "telegram_login"),
                "error": None,
            },
        )

    @router.post("/login", response_model=None)
    async def login(request: Request) -> HTMLResponse | RedirectResponse:
        values = await _request_values(request)
        auth_mode = _config_value(config, "auth_mode", "telegram_login")
        try:
            if auth_mode == "bootstrap_token":
                requested_id = _optional_int(values.get("telegram_id"))
                telegram_id = authorize_bootstrap_login(
                    values.get("token"),
                    bootstrap_token=str(
                        _config_value(config, "admin_bootstrap_token", "")
                    ),
                    admin_telegram_ids=configured_ids,
                    requested_telegram_id=requested_id,
                )
            elif auth_mode == "telegram_login":
                telegram_id = authorize_telegram_login(
                    values,
                    bot_token=str(_config_value(config, "telegram_bot_token", "")),
                    admin_telegram_ids=configured_ids,
                    max_age_sec=int(
                        _config_value(config, "telegram_auth_max_age_sec", 86_400)
                    ),
                )
            else:
                raise AdminAuthError("admin authentication mode is invalid")
            cookie_value = await session_store.create(telegram_id)
        except (AdminAuthError, RuntimeError, ValueError):
            return _TEMPLATES.TemplateResponse(
                request=request,
                name="login.html",
                context={"auth_mode": auth_mode, "error": "Login failed."},
                status_code=401,
            )

        response = RedirectResponse(url="/overview", status_code=303)
        response.set_cookie(
            key=session_store.cookie_name,
            value=cookie_value,
            max_age=session_store.ttl_sec,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return response

    @router.post("/logout", response_model=None)
    async def logout(
        request: Request,
        _identity: AdminIdentity = Depends(require_admin),
    ) -> RedirectResponse:
        await session_store.delete(request.cookies.get(session_store.cookie_name))
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(
            session_store.cookie_name,
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.get("/", response_model=None)
    async def root(
        _identity: AdminIdentity = Depends(require_admin),
    ) -> RedirectResponse:
        return RedirectResponse(url="/overview", status_code=307)

    @router.get("/overview", response_class=HTMLResponse)
    async def overview(
        request: Request,
        identity: AdminIdentity = Depends(require_admin),
    ) -> HTMLResponse:
        with _repository_context(repository_factory) as repository:
            metrics_reader = getattr(repository, "get_overview_metrics", None)
            metrics = (
                metrics_reader()
                if callable(metrics_reader)
                else OverviewMetrics(0, 0, 0, 0, 0, Decimal(0), Decimal(0))
            )
            event_reader = getattr(repository, "list_recent_audit_logs", None)
            events = event_reader(limit=10) if callable(event_reader) else []
        context = await _page_context(
            request=request,
            identity=identity,
            active_page="overview",
            redis_client=redis_client,
        )
        context.update({"metrics": metrics, "events": events})
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="overview.html",
            context=context,
        )

    @router.get("/payments", response_class=HTMLResponse)
    async def payments(
        request: Request,
        status: str = "submitted",
        identity: AdminIdentity = Depends(require_admin),
    ) -> HTMLResponse:
        if status not in {"submitted", "approved", "rejected", "all"}:
            raise HTTPException(status_code=400, detail="unsupported payment status")
        with _repository_context(repository_factory) as repository:
            reader = getattr(repository, "list_payments", None)
            if callable(reader):
                queue = reader(status=None if status == "all" else status)
            else:
                queue = repository.list_payment_queue() if status == "submitted" else []
        context = await _page_context(
            request=request,
            identity=identity,
            active_page="payments",
            redis_client=redis_client,
        )
        context.update({"payments": queue, "payment_status": status})
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="payments.html",
            context=context,
        )

    @router.post("/payments/{payment_id}/precheck")
    async def payment_precheck(
        payment_id: int,
        identity: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        with _repository_context(repository_factory) as repository:
            payment = repository.get_payment(payment_id)
            if payment is None:
                raise HTTPException(status_code=404, detail="payment not found")
            decision = precheck_payment(
                payment=payment,
                expected_wallets_by_network=_config_value(
                    config, "expected_wallets_by_network", {}
                ),
                token_contracts_by_network=_config_value(
                    config, "token_contracts_by_network", {}
                ),
                min_confirmations_by_network=_config_value(
                    config, "min_confirmations_by_network", {}
                ),
                explorer_clients_by_network=_config_value(
                    config, "explorer_clients_by_network", {}
                ),
            )
            payment = repository.update_payment_precheck(
                payment=payment,
                precheck_result=decision.result,
                amount_seen=decision.amount_seen,
                confirmations=decision.confirmations,
                explorer_url=decision.explorer_url,
            )
            _audit_if_supported(
                repository,
                actor=f"admin:{identity.telegram_id}",
                action="payment.precheck",
                entity="payment",
                entity_id=str(payment_id),
                meta={"result": decision.result, "reason": decision.reason},
            )
            notify_payload = build_notify_admin_precheck_payload(
                admin_telegram_id=identity.telegram_id,
                payment=payment,
                precheck_result=decision.result,
                reason=decision.reason,
            )
        await _publish(
            publisher,
            stream=NOTIFY_STREAM,
            event_type="notify.admin",
            payload=notify_payload,
        )
        return {
            "payment_id": payment_id,
            "result": decision.result,
            "reason": decision.reason,
            "amount_seen": (
                format(decision.amount_seen, "f") if decision.amount_seen is not None else None
            ),
            "confirmations": decision.confirmations,
            "explorer_url": decision.explorer_url,
        }

    @router.post("/payments/{payment_id}/approve")
    async def approve_payment(
        payment_id: int,
        identity: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        with _repository_context(repository_factory) as repository:
            payment = repository.get_payment(payment_id)
            if payment is None:
                raise HTTPException(status_code=404, detail="payment not found")
            try:
                payment, subscription = repository.approve_payment(
                    payment=payment,
                    admin_telegram_id=identity.telegram_id,
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            if payment.user_id is None:
                raise HTTPException(status_code=409, detail="payment user is missing")
            user = repository.get_user(payment.user_id)
            if user is None:
                raise HTTPException(status_code=409, detail="payment user was not found")
            payment_payload = build_payment_approved_payload(
                payment=payment,
                subscription=subscription,
            )
            notify_payload = build_notify_user_payment_approved_payload(
                user=user,
                subscription=subscription,
            )
        await _publish(
            publisher,
            stream=PAYMENTS_STREAM,
            event_type="payment.approved",
            payload=payment_payload,
        )
        await _publish(
            publisher,
            stream=NOTIFY_STREAM,
            event_type="notify.user",
            payload=notify_payload,
        )
        return {
            "payment_id": payment.id,
            "subscription_id": subscription.id,
            "status": payment.status,
        }

    @router.post("/payments/{payment_id}/reject")
    async def reject_payment(
        payment_id: int,
        request: Request,
        identity: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        rejection_reason = await _rejection_reason(request)
        with _repository_context(repository_factory) as repository:
            payment = repository.get_payment(payment_id)
            if payment is None:
                raise HTTPException(status_code=404, detail="payment not found")
            try:
                payment = repository.reject_payment(
                    payment=payment,
                    admin_telegram_id=identity.telegram_id,
                    reason=rejection_reason,
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            if payment.user_id is None:
                raise HTTPException(status_code=409, detail="payment user is missing")
            user = repository.get_user(payment.user_id)
            if user is None:
                raise HTTPException(status_code=409, detail="payment user was not found")
            payment_payload = build_payment_rejected_payload(
                payment=payment,
                reason=rejection_reason,
            )
            notify_payload = build_notify_user_payment_rejected_payload(
                user=user,
                reason=rejection_reason,
            )
        await _publish(
            publisher,
            stream=PAYMENTS_STREAM,
            event_type="payment.rejected",
            payload=payment_payload,
        )
        await _publish(
            publisher,
            stream=NOTIFY_STREAM,
            event_type="notify.user",
            payload=notify_payload,
        )
        return {"payment_id": payment.id, "status": payment.status}

    @router.get("/users", response_class=HTMLResponse)
    async def users(
        request: Request,
        identity: AdminIdentity = Depends(require_admin),
    ) -> HTMLResponse:
        with _repository_context(repository_factory) as repository:
            reader = getattr(repository, "list_user_summaries", None)
            user_rows = reader() if callable(reader) else repository.list_users()
        context = await _page_context(
            request=request,
            identity=identity,
            active_page="users",
            redis_client=redis_client,
        )
        context.update({"users": user_rows})
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="users.html",
            context=context,
        )

    @router.get("/users/{user_id}", response_class=HTMLResponse)
    async def user_detail(
        user_id: int,
        request: Request,
        identity: AdminIdentity = Depends(require_admin),
    ) -> HTMLResponse:
        with _repository_context(repository_factory) as repository:
            user = repository.get_user(user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
        paused = bool(await _redis_get(redis_client, f"{KILL_SWITCH_USER_PREFIX}{user_id}"))
        context = await _page_context(
            request=request,
            identity=identity,
            active_page="users",
            redis_client=redis_client,
        )
        context.update({"user": user, "user_paused": paused})
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="user_detail.html",
            context=context,
        )

    @router.post("/users/{user_id}/block/{state}")
    async def block_user(
        user_id: int,
        state: str,
        identity: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        blocked = _parse_boolean_state(state, label="block state")
        with _repository_context(repository_factory) as repository:
            user = repository.get_user(user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            repository.set_user_blocked(user=user, blocked=blocked)
            _audit_if_supported(
                repository,
                actor=f"admin:{identity.telegram_id}",
                action="user.block" if blocked else "user.unblock",
                entity="user",
                entity_id=str(user_id),
                meta={"blocked": blocked},
            )
        return {"user_id": user_id, "blocked": blocked}

    @router.get("/trades", response_class=HTMLResponse)
    async def trades(
        request: Request,
        tab: str = "live",
        user_id: int | None = None,
        symbol: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        identity: AdminIdentity = Depends(require_admin),
    ) -> HTMLResponse:
        if tab not in {"live", "history"}:
            raise HTTPException(status_code=400, detail="unsupported trade tab")
        start = datetime.combine(date_from, time.min, tzinfo=UTC) if date_from else None
        end = (
            datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
            if date_to
            else None
        )
        with _repository_context(repository_factory) as repository:
            trade_rows = repository.list_trades(
                history=tab == "history",
                user_id=user_id,
                symbol=symbol,
                date_from=start,
                date_to=end,
            )
            history_rows = repository.list_trades(
                history=True,
                user_id=user_id,
                symbol=symbol,
                date_from=start,
                date_to=end,
            )
        context = await _page_context(
            request=request,
            identity=identity,
            active_page="trades",
            redis_client=redis_client,
        )
        context.update(
            {
                "trades": trade_rows,
                "tab": tab,
                "filters": {
                    "user_id": user_id or "",
                    "symbol": symbol or "",
                    "date_from": date_from.isoformat() if date_from else "",
                    "date_to": date_to.isoformat() if date_to else "",
                },
                "pnl_points_json": json.dumps(_cumulative_pnl_points(history_rows)),
            }
        )
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="trades.html",
            context=context,
        )

    @router.get("/signals", response_class=HTMLResponse)
    @router.get("/signals/anomalies", response_class=HTMLResponse)
    async def signal_anomalies(
        request: Request,
        identity: AdminIdentity = Depends(require_admin),
    ) -> HTMLResponse:
        with _repository_context(repository_factory) as repository:
            signals = repository.list_signal_anomalies()
        context = await _page_context(
            request=request,
            identity=identity,
            active_page="signals",
            redis_client=redis_client,
        )
        context.update({"signals": signals})
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="signals.html",
            context=context,
        )

    @router.get("/system", response_class=HTMLResponse)
    async def system(
        request: Request,
        identity: AdminIdentity = Depends(require_admin),
    ) -> HTMLResponse:
        context = await _page_context(
            request=request,
            identity=identity,
            active_page="system",
            redis_client=redis_client,
        )
        context.update(
            {
                "testnet_enabled": bool(
                    _config_value(config, "testnet_enabled", False)
                ),
                "live_canary_enabled": bool(
                    _config_value(config, "live_canary_enabled", False)
                ),
                "live_trading_status": "Canary" if bool(
                    _config_value(config, "live_canary_enabled", False)
                ) else "Disabled",
            }
        )
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="system.html",
            context=context,
        )

    @router.post("/kill-switch/global/{state}")
    async def global_kill_switch(
        state: str,
        request: Request,
        identity: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        enabled = _parse_kill_switch_state(state)
        await _require_pause_confirmation(request, enabled)
        await _set_kill_switch(redis_client, KILL_SWITCH_GLOBAL_KEY, enabled)
        with _repository_context(repository_factory) as repository:
            _audit_if_supported(
                repository,
                actor=f"admin:{identity.telegram_id}",
                action="kill_switch.global",
                entity="kill_switch",
                entity_id="global",
                meta={"enabled": enabled},
            )
        return {"scope": "global", "enabled": enabled}

    @router.post("/kill-switch/user/{user_id}/{state}")
    async def user_kill_switch(
        user_id: int,
        state: str,
        request: Request,
        identity: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        enabled = _parse_kill_switch_state(state)
        await _require_pause_confirmation(request, enabled)
        key = f"{KILL_SWITCH_USER_PREFIX}{user_id}"
        await _set_kill_switch(redis_client, key, enabled)
        with _repository_context(repository_factory) as repository:
            _audit_if_supported(
                repository,
                actor=f"admin:{identity.telegram_id}",
                action="kill_switch.user",
                entity="user",
                entity_id=str(user_id),
                meta={"enabled": enabled},
            )
        return {"scope": "user", "user_id": user_id, "enabled": enabled}

    return router


def create_app(
    *,
    repository_factory: Any,
    publisher: Any,
    redis_client: Any,
    config: Any,
) -> FastAPI:
    """Create FastAPI admin panel app."""
    application = FastAPI(
        title="MRRIK AI bot admin panel",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    session_store = AdminSessionStore(
        redis_client,
        signing_secret=str(_config_value(config, "session_signing_secret", "")),
        ttl_sec=int(_config_value(config, "session_ttl_sec", 43_200)),
    )
    application.state.admin_session_store = session_store
    application.mount(
        "/static",
        StaticFiles(directory=str(_ADMIN_DIR / "static")),
        name="static",
    )
    application.include_router(
        create_admin_router(
            repository_factory=repository_factory,
            publisher=publisher,
            redis_client=redis_client,
            config=config,
            session_store=session_store,
        )
    )
    return application


@contextmanager
def _repository_context(repository_factory: Any) -> Iterator[Any]:
    candidate = repository_factory() if callable(repository_factory) else repository_factory
    if hasattr(candidate, "__enter__") and hasattr(candidate, "__exit__"):
        with candidate as repository:
            yield repository
    else:
        yield candidate


def _config_value(config: Any, key: str, default: Any) -> Any:
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


def _admin_telegram_ids(config: Any) -> tuple[int, ...]:
    configured_ids = _config_value(config, "admin_telegram_ids", ())
    return (
        parse_admin_telegram_ids(configured_ids)
        if isinstance(configured_ids, str)
        else tuple(configured_ids)
    )


async def _page_context(
    *,
    request: Request,
    identity: AdminIdentity,
    active_page: str,
    redis_client: Any,
) -> dict[str, Any]:
    return {
        "request": request,
        "identity": identity,
        "active_page": active_page,
        "service_health": await _five_service_health(),
        "global_paused": bool(await _redis_get(redis_client, KILL_SWITCH_GLOBAL_KEY)),
    }


async def _five_service_health() -> list[dict[str, Any]]:
    checks = await asyncio.gather(
        check_signal_ingestor(),
        check_core_engine(),
        check_demo_engine(),
        check_telegram_bot(),
        check_admin_panel(),
    )
    return [{"name": check.service, "ok": check.ok} for check in checks]


async def _publish(
    publisher: Any,
    *,
    stream: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    result = publisher.publish(stream=stream, event_type=event_type, payload=payload)
    if inspect.isawaitable(result):
        await result


async def _set_kill_switch(redis_client: Any, key: str, enabled: bool) -> None:
    result = redis_client.set(key, "1") if enabled else redis_client.delete(key)
    if inspect.isawaitable(result):
        await result


async def _redis_get(redis_client: Any, key: str) -> Any:
    getter = getattr(redis_client, "get", None)
    if not callable(getter):
        return None
    result = getter(key)
    return await result if inspect.isawaitable(result) else result


def _parse_kill_switch_state(state: str) -> bool:
    return _parse_boolean_state(state, label="kill switch state")


def _parse_boolean_state(state: str, *, label: str) -> bool:
    if state == "on":
        return True
    if state == "off":
        return False
    raise HTTPException(status_code=400, detail=f"{label} must be on or off")


async def _require_pause_confirmation(request: Request, enabled: bool) -> None:
    if not enabled:
        return
    values = await _request_values(request)
    confirmation = values.get("confirm") or request.headers.get("x-admin-confirm")
    if confirmation != "PAUSE":
        raise HTTPException(status_code=400, detail="pause confirmation is required")


def _audit_if_supported(repository: Any, **values: Any) -> None:
    writer = getattr(repository, "write_audit_log", None)
    if callable(writer):
        writer(**values)


async def _rejection_reason(request: Request) -> str:
    values = await _request_values(request)
    reason = values.get("reason")
    return reason.strip() if reason and reason.strip() else "TXID did not pass verification"


async def _request_values(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").casefold()
    if "application/json" in content_type:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if isinstance(body, Mapping):
            return {
                str(key): str(value)
                for key, value in body.items()
                if value is not None
            }
        return {}
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _format_utc(value: Any) -> str:
    if not isinstance(value, datetime):
        return "—"
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _format_decimal(value: Any, *, places: int) -> str:
    if value is None:
        return "—"
    number = Decimal(value)
    rendered = f"{number:.{places}f}"
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _format_duration(value: Any) -> str:
    if not isinstance(value, timedelta):
        return "—"
    total_seconds = max(0, int(value.total_seconds()))
    days, remainder = divmod(total_seconds, 86_400)
    hours, minutes = divmod(remainder // 60, 60)
    return f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"


def _short_txid(value: Any) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= 18 else f"{text[:10]}…{text[-6:]}"


def _payment_explorer_url(payment: Any) -> str | None:
    if payment.explorer_url:
        return str(payment.explorer_url)
    txid = str(payment.txid or "").strip()
    bases = {
        "TRC20": "https://tronscan.org/#/transaction/",
        "BEP20": "https://bscscan.com/tx/",
        "POLYGON": "https://polygonscan.com/tx/",
    }
    base = bases.get(str(payment.network))
    return f"{base}{txid}" if base and txid else None


def _cumulative_pnl_points(trades: list[Any]) -> list[dict[str, Any]]:
    total = Decimal(0)
    points: list[dict[str, Any]] = []
    for trade in sorted(
        trades,
        key=lambda item: item.closed_at or datetime.min.replace(tzinfo=UTC),
    ):
        total += trade.realized_pnl_usdt or Decimal(0)
        points.append(
            {
                "label": _format_utc(trade.closed_at),
                "value": float(total),
            }
        )
    return points
