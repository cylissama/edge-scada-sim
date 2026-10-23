#!/usr/bin/env bash
# Build images, load them into a local kind cluster, and apply the manifests.
# Usage: ./k8s/deploy.sh            deploy everything
#        ./k8s/deploy.sh teardown   delete the whole cluster
set -euo pipefail

CLUSTER=edge-scada-sim
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K="kubectl -n scada"
# Every service image: built from the repo root with <svc>/Dockerfile.
SERVICES=(imu camera plc gateway)

if [[ "${1:-}" == "teardown" ]]; then
  kind delete cluster --name "$CLUSTER"
  exit 0
fi

echo "==> 1/5 Create cluster (skipped if it exists)"
kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --config "$ROOT/k8s/kind-config.yaml"
kubectl config use-context "kind-$CLUSTER"

echo "==> 2/5 Build and load images"
images=()
for svc in "${SERVICES[@]}"; do
  img="ghcr.io/cylissama/edge-scada-sim-$svc:dev"
  docker build -t "$img" -f "$ROOT/$svc/Dockerfile" "$ROOT"
  images+=("$img")
done
kind load docker-image "${images[@]}" --name "$CLUSTER"

echo "==> 3/5 Apply manifests"
# Jobs are immutable, so remove the old one before re-applying.
$K delete job topic-init --ignore-not-found 2>/dev/null || true
kubectl apply -k "$ROOT/k8s"

echo "==> 4/5 Wait for Kafka and topics"
$K rollout status statefulset/kafka --timeout=300s
$K wait --for=condition=complete job/topic-init --timeout=180s

echo "==> 5/5 Wait for edge services"
# Restart edge services so freshly loaded :dev images are picked up.
$K rollout restart deployment -l scada/controllable=true
$K wait --for=condition=available deployment -l scada/controllable=true --timeout=180s

echo "==> Done. Watch the data with:"
echo "    $K exec kafka-0 -- /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic imu-readings"
