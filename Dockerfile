FROM python:3.12-slim

WORKDIR /app

RUN pip install --upgrade pip "setuptools>=68"

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY alembic.ini .
COPY alembic/ alembic/
COPY cuny_scouter/ cuny_scouter/
