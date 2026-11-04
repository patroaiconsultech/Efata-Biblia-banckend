"""Read-only, sandbox-only Asaas charge verification.

A verification observation is NOT authorization to activate, extend, or revoke
premium entitlements. No database access, webhook processing, or payment
creation occurs in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

import httpx

from .asaas_adapter import AsaasClient, ProviderError

_PROVIDER_ID = re.compile(r"^[A-Za-z0-9_.:-]{3,160}$")

_REVERSAL_STATES = frozenset({
    "REFUNDED", "PARTIALLY_REFUNDED", "REFUND_IN_PROGRESS",
    "CHARGEBACK_REQUESTED", "CHARGEBACK_DISPUTE",
    "AWAITING_CHARGEBACK_REVERSAL", "RECEIVED_IN_CASH_UNDONE",
})


@dataclass(frozen=True)
class PaymentObservation:
    payment_id: str
    provider_status: str
    assessment: str
    # Deliberately always false: no financial state transitions in this RC.
    entitlement_change_permitted: bool = False


def _validated_id(value: str) -> str:
    if not isinstance(value, str) or _PROVIDER_ID.fullmatch(value) is None:
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_ID_INVALID", False)
    return value


def _cents(value: object) -> int:
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or amount <= 0
                or amount > Decimal("10000")
                or amount.as_tuple().exponent < -2):
            raise InvalidOperation
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise InvalidOperation
        return int(cents)
    except (TypeError, ValueError, InvalidOperation):
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_AMOUNT_INVALID", False)


def verify_payment_readonly(
    client: AsaasClient,
    *,
    payment_id: str,
    checkout_id: str,
    provider_ref: str,
    amount_cents: int,
) -> PaymentObservation:
    """Verify charge facts using a separately authenticated provider GET.

    The caller supplies expected values only from its Bible-owned, trusted
    checkout record. Webhook/browser fields must never be used as expectations.
    The result cannot be used as a payment grant or refund decision.
    """
    payment_id = _validated_id(payment_id)
    checkout_id = _validated_id(checkout_id)
    provider_ref = _validated_id(provider_ref)
    if type(amount_cents) is not int or amount_cents <= 0 or amount_cents > 1000000:
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_AMOUNT_INVALID", False)
    # AsaasClient refuses live environment on construction.
    try:
        with httpx.Client(transport=client.transport, timeout=client.timeout,
                          follow_redirects=False) as transport:
            response = transport.get(
                client.base_url + "/payments/" + payment_id,
                headers=client._headers(),
            )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_UNAVAILABLE") from exc

    if response.status_code != 200:
        raise ProviderError(
            "BIBLE_BILLING_RECONCILIATION_PROVIDER_ERROR",
            response.status_code >= 500 or response.status_code in {408, 429},
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_INVALID_RESPONSE") from exc
    if not isinstance(payload, dict):
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_INVALID_RESPONSE")
    if payload.get("id") != payment_id:
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_PAYMENT_MISMATCH", False)
    if payload.get("paymentLink") != provider_ref:
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_LINK_MISMATCH", False)
    if payload.get("externalReference") != checkout_id:
        # Real Asaas responses may omit externalReference: do not invent one.
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_REFERENCE_MISMATCH", False)
    if _cents(payload.get("value")) != amount_cents:
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_AMOUNT_MISMATCH", False)

    status = payload.get("status")
    if not isinstance(status, str) or not status or len(status) > 100:
        raise ProviderError("BIBLE_BILLING_RECONCILIATION_STATUS_INVALID", False)
    if status in _REVERSAL_STATES:
        assessment = "reversal_or_dispute_hold"
    elif status in {"CONFIRMED", "RECEIVED"}:
        # RECEIVED_IN_CASH is a manual settlement flag, not Asaas settlement.
        assessment = ("manual_review" if payload.get("billingType") == "RECEIVED_IN_CASH"
                      else "paid_observed_unapproved")
    elif status in {"PENDING", "OVERDUE", "AUTHORIZED", "AWAITING_RISK_ANALYSIS"}:
        assessment = "payment_pending"
    else:
        assessment = "manual_review"
    return PaymentObservation(payment_id=payment_id, provider_status=status,
                              assessment=assessment)
