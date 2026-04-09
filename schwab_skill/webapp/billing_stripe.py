from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import stripe
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import User

_ACTIVE_STATUSES = frozenset({"trialing", "active"})


def billing_enforcement_enabled() -> bool:
    return os.getenv("SAAS_BILLING_ENFORCE", "").lower() in ("1", "true", "yes")


def user_has_paid_entitlement(user: User | None) -> bool:
    if not billing_enforcement_enabled():
        return True
    if user is None:
        return False
    status = (user.subscription_status or "").strip().lower()
    return status in _ACTIVE_STATUSES


def _stripe_secret_key() -> str:
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured.")
    return key


def _stripe_price_id() -> str:
    price = (os.getenv("STRIPE_PRICE_ID") or "").strip()
    if not price:
        raise RuntimeError("STRIPE_PRICE_ID is not configured.")
    return price


def configure_stripe() -> None:
    stripe.api_key = _stripe_secret_key()


def subscription_period_end_utc(subscription: dict[str, Any]) -> datetime | None:
    raw = subscription.get("current_period_end")
    if raw is None:
        return None
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def sync_user_from_subscription(db: Session, user: User, subscription: dict[str, Any]) -> None:
    user.stripe_subscription_id = str(subscription.get("id") or "") or None
    user.subscription_status = str(subscription.get("status") or "").strip().lower() or None
    user.subscription_current_period_end = subscription_period_end_utc(subscription)
    db.add(user)
    db.commit()
    db.refresh(user)


def _user_by_stripe_customer(db: Session, customer_id: str) -> User | None:
    cid = (customer_id or "").strip()
    if not cid:
        return None
    return db.query(User).filter(User.stripe_customer_id == cid).first()


def _user_by_id(db: Session, user_id: str) -> User | None:
    uid = (user_id or "").strip()
    if not uid:
        return None
    return db.query(User).filter(User.id == uid).first()


def try_claim_stripe_webhook_event(db: Session, event_id: str) -> bool:
    """Reserve this event id in the current transaction (flush, not commit).

    If processing fails later, rollback releases the id so Stripe retries work.
    Successful handlers call commit and persist this row with other updates.
    """
    from .models import StripeWebhookEvent

    db.add(StripeWebhookEvent(id=event_id))
    try:
        db.flush()
        return True
    except IntegrityError:
        db.rollback()
        return False


def handle_checkout_session_completed(db: Session, session: dict[str, Any]) -> None:
    if (session.get("mode") or "") != "subscription":
        return
    user_id = (session.get("client_reference_id") or "").strip()
    if not user_id:
        meta = session.get("metadata") or {}
        if isinstance(meta, dict):
            user_id = str(meta.get("user_id") or "").strip()
    user = _user_by_id(db, user_id)
    if not user:
        return

    cust = session.get("customer")
    if isinstance(cust, str) and cust:
        user.stripe_customer_id = cust
    elif isinstance(cust, dict) and cust.get("id"):
        user.stripe_customer_id = str(cust["id"])

    sub_ref = session.get("subscription")
    subscription: dict[str, Any] | None = None
    if isinstance(sub_ref, str) and sub_ref:
        configure_stripe()
        subscription = stripe.Subscription.retrieve(sub_ref)
    elif isinstance(sub_ref, dict):
        subscription = sub_ref

    if subscription:
        sync_user_from_subscription(db, user, subscription)
    else:
        db.add(user)
        db.commit()


def handle_subscription_event(db: Session, subscription: dict[str, Any], deleted: bool = False) -> None:
    customer_id = subscription.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")
    customer_id = str(customer_id or "").strip()
    user = _user_by_stripe_customer(db, customer_id)
    if not user:
        return
    if deleted or (subscription.get("status") == "canceled"):
        user.stripe_subscription_id = None
        user.subscription_status = "canceled"
        user.subscription_current_period_end = subscription_period_end_utc(subscription)
        db.add(user)
        db.commit()
        db.refresh(user)
        return
    sync_user_from_subscription(db, user, subscription)


def _event_type_and_object(event: Any) -> tuple[str, Any]:
    if isinstance(event, dict):
        etype = str(event.get("type") or "")
        data = event.get("data") or {}
        data_object = data.get("object") if isinstance(data, dict) else None
    else:
        etype = str(getattr(event, "type", "") or "")
        data_object = getattr(getattr(event, "data", None), "object", None)
    return etype, data_object


def stripe_event_type(event: Any) -> str:
    etype, _ = _event_type_and_object(event)
    return etype


def handle_stripe_event(db: Session, event: Any) -> None:
    etype, data_object = _event_type_and_object(event)
    if data_object is None:
        return

    if etype == "checkout.session.completed":
        handle_checkout_session_completed(db, data_object)
    elif etype in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.paused",
    ):
        handle_subscription_event(db, data_object, deleted=False)
    elif etype == "customer.subscription.deleted":
        handle_subscription_event(db, data_object, deleted=True)


def create_subscription_checkout_session(user: User, success_url: str, cancel_url: str) -> str:
    configure_stripe()
    price_id = _stripe_price_id()
    params: dict[str, Any] = {
        "mode": "subscription",
        "client_reference_id": user.id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items": [{"price": price_id, "quantity": 1}],
        "metadata": {"user_id": user.id},
        "subscription_data": {"metadata": {"user_id": user.id}},
    }
    if user.stripe_customer_id:
        params["customer"] = user.stripe_customer_id
    elif user.email:
        params["customer_email"] = user.email
    session = stripe.checkout.Session.create(**params)
    url = getattr(session, "url", None) or (session.get("url") if isinstance(session, dict) else None)
    if not url:
        raise RuntimeError("Stripe Checkout Session missing url.")
    return str(url)


def create_billing_portal_session(user: User, return_url: str) -> str:
    if not user.stripe_customer_id:
        raise RuntimeError("No Stripe customer on file.")
    configure_stripe()
    session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=return_url,
    )
    url = getattr(session, "url", None) or (session.get("url") if isinstance(session, dict) else None)
    if not url:
        raise RuntimeError("Stripe billing portal session missing url.")
    return str(url)


def stripe_event_id(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("id") or "")
    return str(getattr(event, "id", "") or "")
