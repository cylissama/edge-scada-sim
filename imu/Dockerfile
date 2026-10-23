# Build from the repo root so the shared common/ module is in the context:
#   docker build -f imu/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY imu/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/telemetry.py imu/imu_sim.py ./

CMD ["python", "-u", "imu_sim.py"]
