from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app import config

engine = create_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(engine)


@event.listens_for(SessionLocal, "after_begin")
def set_row_level_security_context(session, transaction, connection):
    # Row-level security policies read these settings. They are transaction-local,
    # so they have to be set again at the start of every transaction, not once per session.
    if session.info.get("bypass_rls"):
        connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
    elif "org_id" in session.info:
        connection.execute(
            text("SELECT set_config('app.org_id', :org_id, true)"),
            {"org_id": str(session.info["org_id"])},
        )


def get_db():
    """FastAPI dependency. The session sees no org rows until get_current_user sets org_id."""
    with SessionLocal() as db:
        yield db


def system_session() -> Session:
    """A session that sees every org. Only for login, CLI scripts and workers."""
    return SessionLocal(info={"bypass_rls": True})
