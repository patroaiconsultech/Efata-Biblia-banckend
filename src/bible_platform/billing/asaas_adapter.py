"""Asaas sandbox adapter adapted from the supplied EFATÀ billing implementation.

No automatic retries of POST; unknown outcomes require externalReference reconciliation.
This module does not perform charges on import and is NOT enabled by default.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import httpx

SANDBOX_URL = "https://api-sandbox.asaas.com/v3"
LIVE_URL = "https://api.asaas.com/v3"

@dataclass(frozen=True)
class ProviderError(RuntimeError):
    code: str
    outcome_unknown: bool = True

class AsaasClient:
    def __init__(self, *, api_key: str, environment: str = "sandbox",
                 transport: httpx.BaseTransport | None = None, timeout: float = 10.0):
        if environment != "sandbox":
            # Explicitly disabled until legal, finance, reconciliation and production gates pass.
            raise ProviderError("BIBLE_BILLING_LIVE_NOT_APPROVED", False)
        if not api_key.strip():
            raise ProviderError("BIBLE_BILLING_API_KEY_REQUIRED", False)
        self.base_url = SANDBOX_URL
        self.api_key = api_key
        self.transport = transport
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "access_token": self.api_key,
                "User-Agent": "BIBLE-EFATA777/0.8"}

    def create_payment_link(self, *, checkout_id: str, plan: dict, success_url: str) -> dict[str, str]:
        if not success_url.startswith("https://"):
            raise ProviderError("BIBLE_BILLING_CALLBACK_HTTPS_REQUIRED", False)
        payload: dict[str, Any] = {
            "name": plan["name"], "description": plan["name"],
            "billingType": "UNDEFINED", "chargeType": "DETACHED",
            "value": float(plan["price_brl"]), "dueDateLimitDays": 7,
            "externalReference": checkout_id,
            "callback": {"successUrl": success_url, "autoRedirect": False}
        }
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                result = client.post(self.base_url + "/paymentLinks", headers=self._headers(), json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError("BIBLE_BILLING_PROVIDER_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("BIBLE_BILLING_PROVIDER_UNAVAILABLE") from exc
        if result.status_code >= 500:
            raise ProviderError("BIBLE_BILLING_PROVIDER_OUTCOME_UNKNOWN")
        if result.status_code >= 400:
            raise ProviderError("BIBLE_BILLING_PROVIDER_REJECTED", False)
        try:
            data = result.json()
        except ValueError as exc:
            raise ProviderError("BIBLE_BILLING_PROVIDER_INVALID_RESPONSE") from exc
        if not isinstance(data, dict) or not data.get("id") or not data.get("url"):
            raise ProviderError("BIBLE_BILLING_PROVIDER_INVALID_RESPONSE")
        return {"provider_id": str(data["id"]), "payment_url": str(data["url"])}

    def find_by_external_reference(self, checkout_id: str) -> dict[str, str] | None:
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                result = client.get(self.base_url + "/paymentLinks", headers=self._headers(),
                                    params={"externalReference": checkout_id, "limit": 10})
        except httpx.HTTPError as exc:
            raise ProviderError("BIBLE_BILLING_RECONCILIATION_UNAVAILABLE") from exc
        if result.status_code >= 400:
            raise ProviderError("BIBLE_BILLING_RECONCILIATION_FAILED")
        try:
            rows = result.json()["data"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("BIBLE_BILLING_RECONCILIATION_INVALID_RESPONSE") from exc
        if not isinstance(rows, list):
            raise ProviderError("BIBLE_BILLING_RECONCILIATION_INVALID_RESPONSE")
        matches = [p for p in rows if isinstance(p, dict) and str(p.get("externalReference") or "") == checkout_id]
        if len(matches) > 1:
            raise ProviderError("BIBLE_BILLING_RECONCILIATION_AMBIGUOUS")
        if not matches:
            return None
        if not matches[0].get("id") or not matches[0].get("url"):
            raise ProviderError("BIBLE_BILLING_RECONCILIATION_INVALID_RESPONSE")
        return {"provider_id": str(matches[0]["id"]), "payment_url": str(matches[0]["url"])}
