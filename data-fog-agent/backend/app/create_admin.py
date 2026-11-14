"""Create an org and its first admin user.

Usage:
    uv run python -m app.create_admin --org "Chaiwala Co." --email admin@chaiwala.co --name "Admin"

The password is asked for at the prompt so it never ends up in shell history.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.auth import hash_password
from app.db import system_session
from app.models import Org, User


def create_admin(org_name: str, email: str, name: str, password: str) -> User:
    email = email.strip().lower()
    with system_session() as db:
        if db.scalar(select(User).where(User.email == email)):
            raise ValueError(f"A user with email {email} already exists")

        org = db.scalar(select(Org).where(Org.name == org_name))
        if org is None:
            org = Org(name=org_name)
            db.add(org)
            db.flush()

        user = User(
            org_id=org.id,
            email=email,
            name=name,
            role="admin",
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def main():
    parser = argparse.ArgumentParser(description="Create an org and its first admin user.")
    parser.add_argument("--org", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    if len(password) < 8:
        sys.exit("Password must be at least 8 characters.")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords don't match.")

    try:
        user = create_admin(args.org, args.email, args.name, password)
    except ValueError as error:
        sys.exit(str(error))
    print(f"Created admin {user.email} in org {args.org}")


if __name__ == "__main__":
    main()
