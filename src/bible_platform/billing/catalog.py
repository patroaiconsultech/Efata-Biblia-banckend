"""Server-authoritative, fail-closed pricing catalog for Bíblia Sagrada."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import json
import os
import re

_CODE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

class CatalogError(ValueError):
    pass

def plans() -> list[dict]:
    raw = os.getenv("BIBLE_BILLING_PLANS_JSON", "{}")
    try:
        items = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise CatalogError("BIBLE_BILLING_CATALOG_INVALID_JSON") from exc
    if not isinstance(items, dict):
        raise CatalogError("BIBLE_BILLING_CATALOG_NOT_OBJECT")
    output = []
    for code, plan in items.items():
        if not isinstance(code, str) or not _CODE.fullmatch(code) or not isinstance(plan, dict):
            raise CatalogError("BIBLE_BILLING_CATALOG_INVALID_PLAN")
        try:
            amount = Decimal(str(plan["price_brl"]))
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise CatalogError("BIBLE_BILLING_PRICE_INVALID") from exc
        if not amount.is_finite() or amount <= 0 or amount > Decimal("10000") or amount.as_tuple().exponent < -2:
            raise CatalogError("BIBLE_BILLING_PRICE_INVALID")
        name = str(plan.get("name") or "").strip()
        if not name or len(name) > 90:
            raise CatalogError("BIBLE_BILLING_PLAN_NAME_INVALID")
        days = plan.get("access_days", 30)
        if type(days) is not int or not 1 <= days <= 366:
            raise CatalogError("BIBLE_BILLING_ACCESS_DAYS_INVALID")
        raw_features = plan.get("features", [])
        if not isinstance(raw_features, list) or len(raw_features) > 20 or any(not isinstance(f, str) or len(f) > 180 for f in raw_features):
            raise CatalogError("BIBLE_BILLING_FEATURES_INVALID")
        output.append({
            "code": code, "name": name,
            "price_brl": str(amount.quantize(Decimal("0.01"))),
            "currency": "BRL", "access_days": days,
            "features": raw_features,
        })
    return output

def find_plan(code: str) -> dict | None:
    return next((p for p in plans() if p["code"] == code), None)
