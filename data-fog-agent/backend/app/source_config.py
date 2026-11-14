"""Source connection settings are stored encrypted, because they hold passwords."""

import json

from cryptography.fernet import Fernet

from app import config

fernet = Fernet(config.CONFIG_ENCRYPTION_KEY)


def encrypt_config(settings: dict) -> str:
    return fernet.encrypt(json.dumps(settings).encode()).decode()


def decrypt_config(token: str) -> dict:
    return json.loads(fernet.decrypt(token.encode()))
