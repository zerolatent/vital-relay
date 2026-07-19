# Database migrations

Alembic migrations target PostgreSQL only. Set `VITAL_RELAY_DATABASE_URL` or
pass an explicit URL through the database CLI; migrations never fall back to
SQLite or the in-memory adapters.
