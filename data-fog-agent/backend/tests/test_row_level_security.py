import pytest
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from app.create_admin import create_admin
from app.db import SessionLocal
from app.models import Group, User


@pytest.fixture
def two_orgs():
    chaiwala = create_admin("Chaiwala Co.", "admin@chaiwala.co", "Chaiwala Admin", "password-1")
    other = create_admin("Other Co.", "admin@other.co", "Other Admin", "password-2")
    return chaiwala, other


def emails_visible_to(org_id):
    with SessionLocal(info={"org_id": org_id}) as db:
        return db.scalars(select(User.email)).all()


def test_each_org_only_sees_its_own_users(two_orgs):
    chaiwala, other = two_orgs

    assert emails_visible_to(chaiwala.org_id) == ["admin@chaiwala.co"]
    assert emails_visible_to(other.org_id) == ["admin@other.co"]


def test_session_without_org_sees_nothing(two_orgs):
    with SessionLocal() as db:
        assert db.scalars(select(User)).all() == []


def test_org_setting_does_not_leak_into_next_transaction(two_orgs):
    chaiwala, _ = two_orgs

    with SessionLocal(info={"org_id": chaiwala.org_id}) as db:
        assert len(db.scalars(select(User)).all()) == 1
        db.commit()

        # Same session, new transaction, org setting is still applied.
        assert len(db.scalars(select(User)).all()) == 1

    # A plain session on the same pooled connection must see nothing, and must not crash.
    with SessionLocal() as db:
        assert db.scalars(select(User)).all() == []


def test_cannot_insert_rows_for_another_org(two_orgs):
    chaiwala, other = two_orgs

    with SessionLocal(info={"org_id": chaiwala.org_id}) as db:
        db.add(Group(org_id=other.org_id, name="sales"))
        with pytest.raises(ProgrammingError, match="row-level security"):
            db.commit()
