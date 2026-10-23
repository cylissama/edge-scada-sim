# Build from the repo root so the shared common/ module is in the context:
#   docker build -f historian/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY historian/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/telemetry.py historian/historian.py ./

CMD ["python", "-u", "historian.py"]
