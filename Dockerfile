FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WAT_FRONTEND_DIST=/app/static

WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/wat_pilot/ ./wat_pilot/
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 watoperator

COPY --from=frontend-build /build/frontend/dist/ /app/static/

USER watoperator
EXPOSE 8080

CMD ["uvicorn", "wat_pilot.api:app", "--host", "0.0.0.0", "--port", "8080"]
