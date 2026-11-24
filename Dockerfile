FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WAT_FRONTEND_DIST=/app/static \
    WAT_WORKBENCH_DB=/app/data/workbench.sqlite3

WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/wat_pilot/ ./wat_pilot/
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 watoperator \
    && install -d -m 0700 -o watoperator -g watoperator /app/data \
    && install -m 0600 -o watoperator -g watoperator /dev/null /app/data/.keep

COPY --from=frontend-build /build/frontend/dist/ /app/static/

USER watoperator
EXPOSE 8080

CMD ["uvicorn", "wat_pilot.api:app", "--host", "0.0.0.0", "--port", "8080"]
