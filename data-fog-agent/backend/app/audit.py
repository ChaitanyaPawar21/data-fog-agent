from sqlalchemy.orm import Session

from app.models import AuditLog, User


def audit(db: Session, user: User, actor: str, action: str, target=None, details=None):
    """Records who did what. The caller commits, so the entry lands with the work it describes."""
    db.add(
        AuditLog(
            org_id=user.org_id,
            user_id=user.id,
            actor=actor,
            action=action,
            target=target,
            details=details,
        )
    )
