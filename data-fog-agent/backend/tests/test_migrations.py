from alembic import command
from tests.conftest import ALEMBIC_CONFIG


def test_migrations_downgrade_and_upgrade_cleanly():
    command.downgrade(ALEMBIC_CONFIG, "base")
    command.upgrade(ALEMBIC_CONFIG, "head")
