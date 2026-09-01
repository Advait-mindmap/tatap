FROM python:3.12-slim AS backend

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
# `ci` honours the lockfile, so the image is built from the dependency tree that was tested.
RUN npm ci
COPY frontend .
RUN npm run build

FROM backend AS final
WORKDIR /app
COPY backend ./backend
# Must match STATIC_DIR in backend/app/main.py (backend/app/static). Copying one level up
# built an image whose UI the app could not find, and which therefore served the API-only
# placeholder instead of the planner.
COPY --from=frontend /app/frontend/dist ./backend/app/static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
