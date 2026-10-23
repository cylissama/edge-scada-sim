# Build from the repo root: docker build -f gateway/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/telemetry.py gateway/gateway.py ./

CMD ["python", "-u", "gateway.py"]
