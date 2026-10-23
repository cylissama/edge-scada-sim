# Build from the repo root: docker build -f plc/Dockerfile .
FROM python:3.12-slim

WORKDIR /app

COPY plc/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY plc/tags.py plc/profiles.py plc/server.py ./

EXPOSE 4840
CMD ["python", "-u", "server.py"]
