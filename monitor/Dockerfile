# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install '.[dev]'

COPY app /app/app

EXPOSE 8000
ENV DB_PATH=/data/server-monitor.sqlite

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
