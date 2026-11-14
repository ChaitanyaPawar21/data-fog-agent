from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app import config as app_config
from app.models import Base

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)

engine = create_engine(app_config.DATABASE_URL, poolclass=pool.NullPool)

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
