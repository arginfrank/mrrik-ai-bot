from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import html
import inspect
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from services.admin_panel.auth import (
    AdminAuthError,
    AdminIdentity,
    authenticate_admin_request,
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
from services.admin_panel.precheck import precheck_payment


def create_admin_router(
    *,
    repository_factory: Any,
    publisher: Any,
    redis_client: Any,
    config: Any,
) -> APIRouter:
    """Create admin panel routes."""
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "admin_panel"}

    @router.get("/", response_class=HTMLResponse)
    async def overview(request: Request) -> HTMLResponse:
        _authenticate(request, config)
        body = (
            "<h1>MRRIK AI bot administration</h1>"
            '<nav><a href="/payments">Payments</a> | '
            '<a href="/users">Users</a> | '
            '<a href="/signals/anomalies">Signal anomalies</a></nav>'
        )
        return HTMLResponse(_page("Admin overview", body))

    @router.get("/payments", response_class=HTMLResponse)
    async def payments(request: Request) -> HTMLResponse:
        _authenticate(request, config)
        with _repository_context(repository_factory) as repository:
            queue = repository.list_payment_queue()
        rows = []
        for payment in queue:
            explorer = ""
            if payment.explorer_url:
                safe_url = html.escape(payment.explorer_url, quote=True)
                explorer = f'<a href="{safe_url}">Explorer</a>'
            rows.append(
                "<tr>"
                f"<td>{_safe(payment.id)}</td>"
                f"<td>{_safe(payment.user_id)}</td>"
                f"<td>{_safe(payment.plan_id)}</td>"
                f"<td>{_safe(payment.network)}</td>"
                f"<td>{_safe(payment.amount_expected)}</td>"
                f"<td>{_safe(payment.txid)}</td>"
                f"<td>{_safe(payment.precheck_result)}</td>"
                f"<td>{explorer}</td>"
                "</tr>"
            )
        table = (
            "<h1>Submitted payments</h1>"
            "<table><thead><tr><th>ID</th><th>User</th><th>Plan</th>"
            "<th>Network</th><th>Amount</th><th>TXID</th><th>Precheck</th>"
            f"<th>Explorer</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )
        return HTMLResponse(_page("Payment queue", table))

    @router.post("/payments/{payment_id}/precheck")
    async def payment_precheck(payment_id: int, request: Request) -> dict[str, Any]:
        identity = _authenticate(request, config)
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
    async def approve_payment(payment_id: int, request: Request) -> dict[str, Any]:
        identity = _authenticate(request, config)
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
        reason: str | None = None,
    ) -> dict[str, Any]:
        identity = _authenticate(request, config)
        rejection_reason = await _rejection_reason(request, reason)
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
    async def users(request: Request) -> HTMLResponse:
        _authenticate(request, config)
        with _repository_context(repository_factory) as repository:
            user_rows = repository.list_users()
        rows = [
            "<tr>"
            f"<td>{_safe(user.id)}</td>"
            f"<td>{_safe(user.telegram_id)}</td>"
            f"<td>{_safe(user.username)}</td>"
            f"<td>{_safe(user.language)}</td>"
            f"<td>{_safe(user.is_blocked)}</td>"
            "</tr>"
            for user in user_rows
        ]
        table = (
            "<h1>Users</h1><table><thead><tr><th>ID</th><th>Telegram ID</th>"
            f"<th>Username</th><th>Language</th><th>Blocked</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
        return HTMLResponse(_page("Users", table))

    @router.get("/signals/anomalies", response_class=HTMLResponse)
    async def signal_anomalies(request: Request) -> HTMLResponse:
        _authenticate(request, config)
        with _repository_context(repository_factory) as repository:
            signals = repository.list_signal_anomalies()
        rows = [
            "<tr>"
            f"<td>{_safe(signal.id)}</td>"
            f"<td>{_safe(signal.symbol)}</td>"
            f"<td>{_safe(signal.status)}</td>"
            f"<td>{_safe(signal.reject_reason)}</td>"
            f"<td>{_safe(signal.sanitizer_notes)}</td>"
            "</tr>"
            for signal in signals
        ]
        table = (
            "<h1>Signal anomalies</h1><table><thead><tr><th>ID</th><th>Symbol</th>"
            f"<th>Status</th><th>Reject reason</th><th>Sanitizer notes</th></tr>"
            f"</thead><tbody>{''.join(rows)}</tbody></table>"
        )
        return HTMLResponse(_page("Signal anomalies", table))

    @router.post("/kill-switch/global/{state}")
    async def global_kill_switch(state: str, request: Request) -> dict[str, Any]:
        identity = _authenticate(request, config)
        enabled = _parse_kill_switch_state(state)
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
    ) -> dict[str, Any]:
        identity = _authenticate(request, config)
        enabled = _parse_kill_switch_state(state)
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
    application.include_router(
        create_admin_router(
            repository_factory=repository_factory,
            publisher=publisher,
            redis_client=redis_client,
            config=config,
        )
    )
    return application


def _authenticate(request: Request, config: Any) -> AdminIdentity:
    configured_ids = _config_value(config, "admin_telegram_ids", ())
    admin_telegram_ids = (
        parse_admin_telegram_ids(configured_ids)
        if isinstance(configured_ids, str)
        else tuple(configured_ids)
    )
    try:
        return authenticate_admin_request(
            headers=dict(request.headers),
            scope=request.scope,
            admin_telegram_ids=admin_telegram_ids,
            ip_allowlist=tuple(_config_value(config, "ip_allowlist", ())),
        )
    except AdminAuthError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


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


def _parse_kill_switch_state(state: str) -> bool:
    if state == "on":
        return True
    if state == "off":
        return False
    raise HTTPException(status_code=400, detail="kill switch state must be on or off")


def _audit_if_supported(repository: Any, **values: Any) -> None:
    writer = getattr(repository, "write_audit_log", None)
    if callable(writer):
        writer(**values)


async def _rejection_reason(request: Request, query_reason: str | None) -> str:
    if query_reason and query_reason.strip():
        return query_reason.strip()
    try:
        body = await request.json()
    except ValueError:
        body = None
    if isinstance(body, Mapping):
        body_reason = body.get("reason")
        if isinstance(body_reason, str) and body_reason.strip():
            return body_reason.strip()
    return "TXID did not pass payment verification"


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title></head><body>{body}</body></html>"
    )


def _safe(value: Any) -> str:
    return html.escape("" if value is None else str(value))
