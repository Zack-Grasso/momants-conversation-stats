import os

# Allow tests to import app.database without a local Postgres/psycopg install.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("WEEKLY_DATABASE_URL", "sqlite+pysqlite:///:memory:")
