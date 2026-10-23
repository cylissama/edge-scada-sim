#!/usr/bin/env bash
# Build images, load them into a local kind cluster, and apply the manifests in order.
# Usage: ./k8s/deploy.sh            deploy everything
#        ./k8s/deploy.sh teardown   delete the whole cluster
set -euo pipefail

CLUSTER=kafka-lab
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K="kubectl -n kafka-lab"

if [[ "${1:-}" == "teardown" ]]; then
  kind delete cluster --name "$CLUSTER"
  exit 0
fi

echo "==> 1/6 Create cluster (skipped if it exists)"
kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER"
kubectl config use-context "kind-$CLUSTER"

echo "==> 2/6 Build and load images"
docker build -t sensor-producer:1 "$ROOT/producer"
docker build -t imu-producer:1    "$ROOT/imu-producer"
docker build -t dashboard:1       "$ROOT/dashboard"
kind load docker-image sensor-producer:1 imu-producer:1 dashboard:1 --name "$CLUSTER"

echo "==> 3/6 Namespace + Kafka"
kubectl apply -f "$ROOT/k8s/00-namespace.yaml"
kubectl apply -f "$ROOT/k8s/10-kafka.yaml"
$K rollout status statefulset/kafka --timeout=300s

echo "==> 4/6 Create topics"
$K delete job topic-init --ignore-not-found
kubectl apply -f "$ROOT/k8s/20-topic-init-job.yaml"
$K wait --for=condition=complete job/topic-init --timeout=180s

echo "==> 5/6 Producers + dashboard"
kubectl apply -f "$ROOT/k8s/30-sensor-producers.yaml" \
              -f "$ROOT/k8s/40-imu-producer.yaml" \
              -f "$ROOT/k8s/50-dashboard.yaml"
$K rollout status deployment/dashboard --timeout=180s

echo "==> 6/6 Done. Open the dashboard with:"
echo "    kubectl -n kafka-lab port-forward svc/dashboard 8080:8080"
echo "    then visit http://localhost:8080"
