# Build from the repo root: docker build -f api/Dockerfile .
# Stage 1 builds the React dashboard; stage 2 is the API, which serves it from /app/static.
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/telemetry.py api/app.py api/live.py api/kube.py ./
COPY --from=web /web/dist ./static

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
