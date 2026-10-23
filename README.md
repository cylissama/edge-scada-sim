# edge-scada-sim

A simulated SCADA environment. Edge devices (OPC UA PLCs, IMUs, ArUco cameras)
send telemetry through Kafka on Kubernetes, with a MongoDB historian and a React dashboard.

See [`planning/implementation-plan.md`](planning/implementation-plan.md) for the phased plan.

## Run locally

Requires Docker, [kind](https://kind.sigs.k8s.io/), and kubectl.

```sh
./k8s/deploy.sh            # create the 3-node kind cluster, build images, deploy
./k8s/deploy.sh teardown   # delete the cluster
```

Change PLC tags: edit `k8s/config/plc-tags.json`, then `kubectl apply -k k8s`. The PLCs restart with the
new tags, and the gateway picks them up on reconnect.

Watch PLC tags (`plc-tags`), alarms/events (`scada-events`) or IMU data (`imu-readings`):

```sh
kubectl -n scada exec kafka-0 -- /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic imu-readings
```
