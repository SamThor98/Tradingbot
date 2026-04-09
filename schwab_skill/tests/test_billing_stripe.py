"""Stripe billing entitlement and webhook idempotency."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from webapp.billing_stripe import (
    try_claim_stripe_webhook_event,
    user_has_paid_entitlement,
)
from webapp.db import Base
from webapp.models import User


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory()


def test_user_has_paid_entitlement_enforcement_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SAAS_BILLING_ENFORCE", raising=False)
    u = User(id="u1", email="a@b.c", auth_provider="supabase")
    assert user_has_paid_entitlement(u) is True


def test_user_has_paid_entitlement_enforcement_on_no_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAAS_BILLING_ENFORCE", "1")
    u = User(id="u1", email="a@b.c", auth_provider="supabase", subscription_status=None)
    assert user_has_paid_entitlement(u) is False


def test_user_has_paid_entitlement_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAAS_BILLING_ENFORCE", "1")
    u = User(id="u1", email="a@b.c", auth_provider="supabase", subscription_status="active")
    assert user_has_paid_entitlement(u) is True


def test_user_has_paid_entitlement_trialing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAAS_BILLING_ENFORCE", "1")
    u = User(id="u1", email="a@b.c", auth_provider="supabase", subscription_status="trialing")
    assert user_has_paid_entitlement(u) is True


def test_try_claim_stripe_webhook_idempotent(db_session: Session) -> None:
    db = db_session
    assert try_claim_stripe_webhook_event(db, "evt_test_1") is True
    db.commit()
    assert try_claim_stripe_webhook_event(db, "evt_test_1") is False
