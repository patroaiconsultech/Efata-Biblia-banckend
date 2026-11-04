"""Durable sandbox-only reconciliation: trusted source, idempotency, no grants."""
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, insert, select, func, update

from bible_platform.billing.asaas_adapter import AsaasClient
from bible_platform.billing.reconciliation import (
    ReconciliationBlocked, reconcile_event_local_readonly,
)
from bible_platform.billing.schema import (
    metadata, checkouts, entitlements, webhook_events, reconciliation_attempts,
)
from bible_platform.config import get_settings


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BIBLE_ENVIRONMENT", "local")
    get_settings.cache_clear()
    path = tmp_path / "reconciliation.db"
    engine = create_engine("sqlite:///" + str(path))
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(checkouts).values(
            tenant_id="tenant-A", checkout_id="checkout_abc",
            user_id="alice", plan_code="caminho", amount_cents=1990,
            idempotency_key="key_001", status="pending",
            provider_ref="link_abc", provider_url="https://sandbox.invalid/abc",
            created_at=datetime.now(timezone.utc),
        ))
        conn.execute(insert(webhook_events).values(
            event_id="evt_abc", tenant_id="tenant-A", user_id="alice",
            checkout_id="checkout_abc", event_type="PAYMENT_RECEIVED",
            payment_id="pay_abc", provider_ref="link_abc",
            processing_status="pending_reconciliation",
            received_at=datetime.now(timezone.utc),
        ))
    yield engine, path
    engine.dispose()
    get_settings.cache_clear()


def payload(**overrides):
    p = dict(id="pay_abc", paymentLink="link_abc",
             externalReference="checkout_abc",
             value=19.90, billingType="PIX", status="RECEIVED",
             customer="SENSITIVE_NOT_PERSISTED")
    p.update(overrides)
    return p


def mock_client(*, status=200, result=None, responder=None):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v3/payments/pay_abc"
        assert request.headers["access_token"] == "synthetic-token-only"
        if responder is not None:
            return responder(request)
        return httpx.Response(status, json=result if result is not None else payload())
    return AsaasClient(api_key="synthetic-token-only",
                       transport=httpx.MockTransport(handler))


def count(conn, table):
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


def test_verified_paid_observation_is_durable_but_no_premium(local_db):
    engine, path = local_db
    result = reconcile_event_local_readonly(
        engine=engine, client=mock_client(), event_id="evt_abc"
    )
    assert result.processing_status == "observed_paid_hold"
    assert not result.entitlement_changed
    engine.dispose()

    # Process restart: a fresh engine sees the completed inbox and audit log.
    next_engine = create_engine("sqlite:///" + str(path))
    with next_engine.connect() as conn:
        event = conn.execute(select(webhook_events)).mappings().one()
        record = conn.execute(select(reconciliation_attempts)).mappings().one()
        assert event["processing_status"] == "observed_paid_hold"
        assert record["result"] == "observed_paid_hold"
        assert record["provider_status"] == "RECEIVED"
        assert count(conn, entitlements) == 0
    next_engine.dispose()
    assert b"SENSITIVE_NOT_PERSISTED" not in path.read_bytes()


def test_replay_does_not_repeat_provider_call_or_audit(local_db):
    engine, _ = local_db
    reconcile_event_local_readonly(engine=engine, client=mock_client(), event_id="evt_abc")
    calls = []
    def forbid(_):
        calls.append(True)
        raise AssertionError("GET must not repeat for completed event")
    second = reconcile_event_local_readonly(
        engine=engine, client=mock_client(responder=forbid), event_id="evt_abc"
    )
    assert second.replayed is True
    assert not calls
    with engine.connect() as conn:
        assert count(conn, reconciliation_attempts) == 1
        assert count(conn, entitlements) == 0


@pytest.mark.parametrize(("state","expected"), [
    ("PENDING", "observed_pending"),
    ("REFUNDED", "observed_reversal_hold"),
    ("CHARGEBACK_REQUESTED", "observed_reversal_hold"),
    ("STRANGE", "observed_manual_review"),
])
def test_provider_states_never_change_entitlements(local_db, state, expected):
    engine, _ = local_db
    result = reconcile_event_local_readonly(
        engine=engine, client=mock_client(result=payload(status=state)),
        event_id="evt_abc"
    )
    assert result.processing_status == expected
    assert result.entitlement_changed is False
    with engine.connect() as conn:
        assert count(conn, entitlements) == 0


def test_provider_mismatch_is_held_and_logged(local_db):
    engine, _ = local_db
    r = reconcile_event_local_readonly(
        engine=engine, client=mock_client(result=payload(value=7.11)),
        event_id="evt_abc"
    )
    assert r.processing_status == "held_provider_mismatch"
    with engine.connect() as conn:
        row = conn.execute(select(reconciliation_attempts)).mappings().one()
        assert row["error_code"] == "BIBLE_BILLING_RECONCILIATION_AMOUNT_MISMATCH"
        assert count(conn, entitlements) == 0


def test_provider_transient_failure_stays_pending_and_is_auditable(local_db):
    engine, _ = local_db
    failed = reconcile_event_local_readonly(
        engine=engine, client=mock_client(status=503, result={"errors":[]}),
        event_id="evt_abc"
    )
    assert failed.processing_status == "pending_reconciliation"
    with engine.connect() as conn:
        assert conn.execute(select(reconciliation_attempts.c.result)).scalar_one() == "retry_required"
    recovered = reconcile_event_local_readonly(
        engine=engine, client=mock_client(), event_id="evt_abc"
    )
    assert recovered.processing_status == "observed_paid_hold"
    with engine.connect() as conn:
        assert count(conn, reconciliation_attempts) == 2
        assert count(conn, entitlements) == 0


def test_missing_event_is_not_dispatched(local_db):
    engine, _ = local_db
    def forbid(_):
        raise AssertionError("no external GET allowed")
    with pytest.raises(ReconciliationBlocked, match="EVENT_NOT_FOUND"):
        reconcile_event_local_readonly(
            engine=engine, client=mock_client(responder=forbid),
            event_id="evt_unknown"
        )


def test_checkout_user_mismatch_refuses_provider_and_does_not_audit(local_db):
    engine, _ = local_db
    with engine.begin() as conn:
        conn.execute(update(webhook_events).values(user_id="mallory"))
    with pytest.raises(ReconciliationBlocked, match="CHECKOUT_MISMATCH"):
        reconcile_event_local_readonly(
            engine=engine, client=mock_client(), event_id="evt_abc"
        )
    with engine.connect() as conn:
        assert count(conn, reconciliation_attempts) == 0
        assert count(conn, entitlements) == 0


def test_checkout_snapshot_change_aborts_transaction(local_db):
    engine, _ = local_db
    def change_amount(request):
        with engine.begin() as conn:
            conn.execute(update(checkouts).values(amount_cents=999))
        return httpx.Response(200, json=payload())
    with pytest.raises(ReconciliationBlocked, match="SNAPSHOT_CHANGED"):
        reconcile_event_local_readonly(
            engine=engine, client=mock_client(responder=change_amount),
            event_id="evt_abc"
        )
    with engine.connect() as conn:
        assert count(conn, reconciliation_attempts) == 0
        assert conn.execute(select(webhook_events.c.processing_status)).scalar_one() == "pending_reconciliation"


def test_ledger_insert_failure_rolls_back_status(local_db):
    engine, _ = local_db
    reconciliation_attempts.drop(engine)
    with pytest.raises(ReconciliationBlocked, match="STORAGE_UNAVAILABLE"):
        reconcile_event_local_readonly(engine=engine, client=mock_client(), event_id="evt_abc")
    with engine.connect() as conn:
        assert conn.execute(select(webhook_events.c.processing_status)).scalar_one() == "pending_reconciliation"
        assert count(conn, entitlements) == 0


def test_no_live_or_network_transport(local_db):
    engine, _ = local_db
    client = AsaasClient(api_key="synthetic-token-only") # no MockTransport
    with pytest.raises(ReconciliationBlocked, match="MOCK_TRANSPORT_REQUIRED"):
        reconcile_event_local_readonly(engine=engine, client=client, event_id="evt_abc")


def test_staging_rejected_before_database_access(local_db, monkeypatch):
    from types import SimpleNamespace
    from bible_platform.billing import reconciliation
    engine, _ = local_db
    # The actual staging config also correctly refuses demo auth; test only
    # the reconciler's independent fail-closed guard here.
    monkeypatch.setattr(reconciliation, "get_settings",
                        lambda: SimpleNamespace(environment="staging"))
    with pytest.raises(ReconciliationBlocked, match="LOCAL_ONLY"):
        reconcile_event_local_readonly(
            engine=engine, client=mock_client(), event_id="evt_abc"
        )


def test_no_active_web_endpoint_wired_for_reconciliation():
    from bible_platform.main import app
    assert not any("reconcil" in route.path for route in app.routes)
