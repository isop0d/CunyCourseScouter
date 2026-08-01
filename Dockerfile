FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY alembic.ini .
COPY alembic/ alembic/
COPY cuny_scouter/ cuny_scouter/

RUN pip install --no-cache-dir .
