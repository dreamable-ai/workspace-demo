"""Create the configured PostgreSQL database when it does not already exist."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from sqlalchemy.engine import make_url


def main() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    raw_url = os.getenv("GATEWAY_DATABASE_URL", "")
    if not raw_url:
        raise SystemExit("GATEWAY_DATABASE_URL is not configured")
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        raise SystemExit("GATEWAY_DATABASE_URL must use PostgreSQL")
    database = url.database or ""
    if not database:
        raise SystemExit("GATEWAY_DATABASE_URL must include a database name")

    connection = psycopg.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 5432,
        user=url.username or "postgres",
        password=url.password or "",
        dbname="postgres",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(
                        sql.Identifier(database)
                    )
                )
                print(f"PostgreSQL database {database!r} was created")
            else:
                print(f"PostgreSQL database {database!r} already exists")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
