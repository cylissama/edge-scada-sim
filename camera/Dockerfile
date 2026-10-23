# Build from the repo root so the shared common/ module is in the context:
#   docker build -f camera/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY camera/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/telemetry.py camera/camera_sim.py ./

CMD ["python", "-u", "camera_sim.py"]
