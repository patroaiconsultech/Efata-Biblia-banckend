"""Provider verification is read-only and cannot activate paid access."""
from __future__ import annotations

import httpx
import pytest

from bible_platform.billing.asaas_adapter import AsaasClient, ProviderError, SANDBOX_URL
from bible_platform.billing.provider_verification import verify_payment_readonly

EXPECTED = {"payment_id": "pay_123", "checkout_id": "checkout_abc",
            "provider_ref": "link_abc", "amount_cents": 1990}


def provider_payload(**override):
    p = {"id": "pay_123", "status": "RECEIVED",
         "paymentLink": "link_abc", "externalReference": "checkout_abc",
         "value": 19.90, "billingType": "PIX", "customer": "private_not_stored"}
    p.update(override)
    return p


def client_for(payload, *, status=200):
    def responder(request):
        assert request.method == "GET"
        assert str(request.url) == SANDBOX_URL + "/payments/pay_123"
        assert request.headers["access_token"] == "synthetic-secret"
        return httpx.Response(status, json=payload)
    return AsaasClient(api_key="synthetic-secret",
                       transport=httpx.MockTransport(responder))


@pytest.mark.parametrize(("status", "expected"), [
    ("RECEIVED", "paid_observed_unapproved"),
    ("CONFIRMED", "paid_observed_unapproved"),
    ("PENDING", "payment_pending"),
    ("OVERDUE", "payment_pending"),
    ("REFUNDED", "reversal_or_dispute_hold"),
    ("PARTIALLY_REFUNDED", "reversal_or_dispute_hold"),
    ("CHARGEBACK_REQUESTED", "reversal_or_dispute_hold"),
    ("UNEXPECTED_NEW_STATUS", "manual_review"),
])
def test_status_is_observation_never_entitlement(status, expected):
    result = verify_payment_readonly(
        client_for(provider_payload(status=status)), **EXPECTED)
    assert result.assessment == expected
    assert result.entitlement_change_permitted is False


@pytest.mark.parametrize(("changes", "code"), [
    ({"id": "pay_other"}, "BIBLE_BILLING_RECONCILIATION_PAYMENT_MISMATCH"),
    ({"paymentLink": "link_other"}, "BIBLE_BILLING_RECONCILIATION_LINK_MISMATCH"),
    ({"paymentLink": None}, "BIBLE_BILLING_RECONCILIATION_LINK_MISMATCH"),
    ({"externalReference": "other"}, "BIBLE_BILLING_RECONCILIATION_REFERENCE_MISMATCH"),
    ({"externalReference": None}, "BIBLE_BILLING_RECONCILIATION_REFERENCE_MISMATCH"),
    ({"value": 10.0}, "BIBLE_BILLING_RECONCILIATION_AMOUNT_MISMATCH"),
    ({"value": "nan"}, "BIBLE_BILLING_RECONCILIATION_AMOUNT_INVALID"),
    ({"status": None}, "BIBLE_BILLING_RECONCILIATION_STATUS_INVALID"),
])
def test_payment_mismatch_fails_closed(changes, code):
    with pytest.raises(ProviderError) as exc:
        verify_payment_readonly(client_for(provider_payload(**changes)), **EXPECTED)
    assert exc.value.code == code


def test_cash_receipt_not_treated_as_provider_settlement():
    result = verify_payment_readonly(
        client_for(provider_payload(billingType="RECEIVED_IN_CASH")), **EXPECTED)
    assert result.assessment == "manual_review"
    assert result.entitlement_change_permitted is False


def test_http_failure_cannot_approve():
    with pytest.raises(ProviderError) as exc:
        verify_payment_readonly(client_for({"errors": []}, status=503), **EXPECTED)
    assert exc.value.outcome_unknown is True


def test_rejects_unsafe_identifier_without_request():
    def fail_if_requested(_):
        raise AssertionError("request must not be dispatched")
    client = AsaasClient(api_key="synthetic-secret",
                         transport=httpx.MockTransport(fail_if_requested))
    with pytest.raises(ProviderError):
        verify_payment_readonly(client, **{**EXPECTED, "payment_id": "../../leak"})


def test_live_environment_is_disabled():
    with pytest.raises(ProviderError) as exc:
        AsaasClient(api_key="synthetic-secret", environment="production")
    assert exc.value.code == "BIBLE_BILLING_LIVE_NOT_APPROVED"


def test_mock_transport_only_no_real_payments():
    observations = []
    def responder(request):
        observations.append(request)
        return httpx.Response(200, json=provider_payload())
    client = AsaasClient(api_key="synthetic-secret",
                         transport=httpx.MockTransport(responder))
    for _ in range(2):
        result = verify_payment_readonly(client, **EXPECTED)
        assert result.entitlement_change_permitted is False
    assert len(observations) == 2
    assert all(r.method == "GET" and r.url.host == "api-sandbox.asaas.com"
               for r in observations)
