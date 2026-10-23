# Kafka lab: sensors → broker → dashboard

Three simulated temperature sensors publish JSON readings to one Kafka topic
on a single KRaft broker. A dashboard container consumes the topic and shows
the latest reading per sensor in a table.

## Run it

```bash
docker compose up --build
```

Open http://localhost:8080. The table refreshes every second.

Stop everything with `Ctrl+C`, or `docker compose down -v` to also wipe the
broker's data.

## Layout

```
docker-compose.yml        broker, topic init, 3 producers, dashboard
producer/producer.py      one sensor; SENSOR_ID picks its identity
dashboard/bridge.py       consumer thread, keeps latest reading per sensor
dashboard/app.py          Flask: / (table) and /api/readings (JSON)
dashboard/templates/      the HTML page
```

## Message format

```json
{"sensor_id": "sensor-A", "seq": 42, "temperature_c": 21.37, "timestamp": "2026-10-23T15:12:01+00:00"}
```

The message key is `sensor-A`, `sensor-B`, or `sensor-C`. Kafka hashes the key to
choose a partition, so every reading from one sensor goes to the same partition
and stays in order.

## Things to try

These show the core Kafka ideas: partitions, offsets, consumer groups, and the
log.

**1. Watch the raw log.** Every message is kept, not just delivered once:

```bash
docker exec -it kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 --topic sensor-readings --from-beginning \
  --property print.key=true --property print.partition=true --property print.offset=true
```

Each line shows the key, partition, and offset. Notice that all `sensor-A`
messages share a partition.

**2. Inspect the topic.**

```bash
docker exec -it kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka:9092 --describe --topic sensor-readings
```

You should see 3 partitions, each with a leader and in-sync replicas.

**3. See consumer lag.** The dashboard is in consumer group `dashboard`. Kafka
tracks how far it has read in each partition:

```bash
docker exec -it kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka:9092 --describe --group dashboard
```

`CURRENT-OFFSET` is where the group has committed. `LOG-END-OFFSET` is the
newest message. `LAG` is the difference.

**4. Stop the dashboard, then restart it.** Producers keep writing while it's
down. When it returns it resumes from its committed offset and catches up,
which is the replay behavior that MQTT brokers don't offer:

```bash
docker compose stop dashboard
sleep 30
docker compose start dashboard
```

Run the command from step 3 right after restarting to watch the lag shrink.

**5. Add a second consumer to the same group.** Partitions are split between
group members, so each consumer handles only some sensors:

```bash
docker compose run -d --name dashboard-2 dashboard
docker exec -it kafka /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka:9092 --describe --group dashboard
```

Partition assignments will change. Note that the sensors rebalance too: with
three partitions and two consumers, one consumer gets two partitions.

**6. Scale the sensors.** Copy one of the `producer-N` blocks in
`docker-compose.yml` with a new `SENSOR_ID` and `BASE_TEMP`, then run
`docker compose up -d --build`. The dashboard picks up the new sensor
automatically.

## Configuration

| Variable | Where | Default | Meaning |
|---|---|---|---|
| `KAFKA_BOOTSTRAP` | producer, dashboard | `localhost:9092` | Broker address |
| `KAFKA_TOPIC` | producer, dashboard | `sensor-readings` | Topic name |
| `SENSOR_ID` | producer | `sensor-1` | Sensor identity and message key |
| `BASE_TEMP` | producer | `21.0` | Temperature the sensor drifts toward |
| `INTERVAL_SECONDS` | producer | `1.0` | Time between readings |
| `KAFKA_GROUP_ID` | dashboard | `dashboard` | Consumer group name |
| `PORT` | dashboard | `8080` | HTTP port inside the container |

## Notes

- The broker is reachable only inside the compose network (`kafka:9092`).
  That's why the producers and dashboard run in containers. To reach it from
  your host, add a second listener in `docker-compose.yml`.
- `apache/kafka:3.9.0` is pinned. Change the tag to try other versions.
# kafka-testing
