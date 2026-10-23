# edge-scada-sim — Implementation Plan

Source docs: `plan.md`, `plc-nodes.md`, `frontend.md`.
Starting point: kind cluster with 1 KRaft broker, 3 temp-sensor pods, 1 IMU pod, and a Flask
dashboard polling `/api/readings` for `sensor-readings` only.

## Decisions (locked)

| Topic | Decision |
|---|---|
| Device stop/start | One **Deployment per device**, dashboard scales it 0 ↔ 1 (pod really goes offline) |
| PLC protocol | **OPC UA** (`asyncua`), separate **edge gateway** subscribes and publishes to Kafka |
| PLC tags | Realistic tag model, JSON-configured, **read-only** from the dashboard |
| MongoDB | Documented data layout + **mongo-express** as the pgAdmin-style query UI |
| Cameras | Simulated ArUco poses that follow a **continuous path**, not random noise |
| Scale | **Message rate + reliability**, with low memory use (budget ≤ 3 GB for the whole kind cluster) |
| Stack | Plain YAML (+ kustomize, which ships inside `kubectl`), FastAPI, React + TypeScript |
| Compose | Removed — k8s only |
| AWS | Out of scope for now; **GitHub Actions CI/CD** instead |
| SMR dashboard styling | You'll provide it later; Phase 5 starts with a neutral layout and gets restyled |

## Target architecture

```
              EDGE node (tier=edge)                              CORE node (tier=core)
 ┌──────────────────────────────────────────┐      ┌───────────────────────────────────────────┐
 │ plc-1  OPC UA server :4840  (conveyor)    │      │ Kafka (KRaft)                             │
 │ plc-2  OPC UA server :4840  (robot cell)  │      │   plc-tags       scada-events            │
 │      ▲ OPC UA subscriptions               │      │   imu-readings   camera-poses            │
 │ opcua-gateway ── produce ─────────────────┼────► │                                           │
 │ imu-1..4      ── produce ─────────────────┼────► │ historian ── consume ──► MongoDB          │
 │ camera-1..4   ── produce ─────────────────┼────► │                            ▲              │
 └──────────────────────────────────────────┘      │                     mongo-express (UI)    │
                                                   │ api (FastAPI) ── consume ──► SSE stream    │
                                                   │      ├─ k8s API: pod status, scale 0/1     │
                                                   │      └─ serves built React app             │
                                                   └───────────────────────────────────────────┘
```

### Repo layout after Phase 0

```
common/telemetry.py         # shared envelope + Kafka producer helpers
k8s/config/plc-tags.json    # the one file you edit to change PLC tags (kustomize only reads files under k8s/)
plc/  gateway/  imu/  camera/  historian/  api/  web/   # build from repo root: docker build -f <svc>/Dockerfile .
k8s/                        # manifests + kustomization.yaml + deploy.sh + kind-config.yaml
.github/workflows/ci.yml
```
`producer/`, `imu-producer/`, `dashboard/`, `docker-compose.yml` are removed or renamed.

### Message envelope (all topics)

```json
{ "device_id": "imu-2", "kind": "imu", "boot_id": "a91f…", "seq": 812,
  "ts": "2026-10-23T17:01:02.123Z", "data": { ... } }
```
`boot_id` is a random id generated when the process starts. With `seq` it makes every message
unique, even after a restart resets `seq` (needed for the duplicate/gap checks in Phase 6).

### Kafka topics

| Topic | Key | Partitions | Producer | Content |
|---|---|---|---|---|
| `plc-tags` | plc id | 6 | gateway | changed tags per publishing interval |
| `imu-readings` | device id | 6 | imu-* | accel/gyro samples |
| `camera-poses` | device id | 6 | camera-* | marker poses per frame |
| `scada-events` | device id | 3 | gateway, api | alarms, faults, connect/disconnect, operator stop/start (audit log) |

---

## Phase 0 — Foundation + CI skeleton ✅ done

- Remove `docker-compose.yml` and the temperature sensors; rename and move the dirs as above.
- `k8s/kind-config.yaml`: 1 control-plane + 2 workers, labelled `tier=edge` / `tier=core`.
  Edge pods get `nodeSelector: {tier: edge}`.
- `k8s/kustomization.yaml`:
  - `configMapGenerator` builds a ConfigMap from `k8s/config/plc-tags.json`. The hash suffix it adds means
    editing the JSON and re-applying **automatically restarts the PLCs**.
  - `images:` overrides, so the same manifests use local `:dev` images or GHCR `:<sha>` images in CI.
- `deploy.sh` keeps its step-by-step order (Kafka ready → topics → everything else), switched to `kubectl apply -k`.
- `common/telemetry.py` holds the envelope and producer helpers. Images build from the repo root so each Dockerfile copies it in, with no package publishing.
- Namespace `scada`, kind cluster `edge-scada-sim`. The configMapGenerator waits for Phase 1, when the tags file exists.
- **CI (`.github/workflows/ci.yml`)**, on every push and PR:
  - `ruff` lint + `pytest` for all Python dirs
  - `kubeconform` validation of the rendered manifests (`kubectl kustomize k8s | kubeconform`)
  - `docker build` of each image (one matrix job per image, with layer caching)

**Done when:** `./k8s/deploy.sh` brings up the 3-node cluster with the new topics, and CI is green on GitHub.

## Phase 1 — PLC simulators (OPC UA) + edge gateway ✅ done

> Built as planned. Notes: alarm limits travel as OPC UA properties (`Unit`, `Enum`, `AlarmLimits`) on each
> tag, and the gateway evaluates them, so a new alarm only needs the JSON. Profiles auto-reset faults (marked
> `ponytail:`). `kubectl apply -k k8s` puts replicas back to 1, which undoes any stop made from the dashboard.
> The gateway is also `scada/controllable` (role `edge-service`).

### Tag model — `k8s/config/plc-tags.json`

Scenario: one production cell. **plc-1** runs a conveyor driven by a VFD (variable-frequency drive);
**plc-2** runs a robot cell with spindle and coolant. JSON nesting becomes OPC UA folders. Node ids
come from the path: `ns=2;s=plc-1.Drive.Speed_Actual`.

```jsonc
{
  "plc-1": {
    "description": "Line 1 conveyor controller",
    "scan_ms": 100,
    "tags": {
      "System": {
        "PLC_ON":          {"type": "Boolean", "value": true},
        "Mode":            {"type": "Int16",   "value": 2, "enum": {"0":"Off","1":"Manual","2":"Auto"}},
        "Heartbeat":       {"type": "UInt32",  "sim": "counter"},
        "Fault_Code":      {"type": "UInt16",  "value": 0},
        "CPU_Load":        {"type": "Float",   "unit": "%",   "sim": {"walk": 1.0, "min": 8, "max": 35}}
      },
      "Drive": {
        "Run_Cmd":         {"type": "Boolean", "value": true},
        "Running":         {"type": "Boolean"},
        "Speed_Setpoint":  {"type": "Float",   "unit": "rpm", "value": 1450},
        "Speed_Actual":    {"type": "Float",   "unit": "rpm"},
        "Motor_Current":   {"type": "Float",   "unit": "A"},
        "Motor_Temp":      {"type": "Float",   "unit": "°C",  "alarm": {"hi": 80, "hihi": 90}},
        "DC_Bus_Voltage":  {"type": "Float",   "unit": "V",   "sim": {"walk": 0.5, "min": 640, "max": 660}},
        "Run_Hours":       {"type": "Double",  "unit": "h"}
      },
      "Line": {
        "Parts_Count":     {"type": "UInt32"},
        "EStop_OK":        {"type": "Boolean", "value": true},
        "Guard_Door_Closed": {"type": "Boolean", "value": true}
      }
    }
  },
  "plc-2": {
    "description": "Robot cell 1 controller",
    "tags": {
      "System":  { "PLC_ON": …, "Mode": …, "Heartbeat": …, "Fault_Code": … },
      "Robot":   { "ROBOT_ON": {"type":"Boolean","value":true},
                   "State": {"type":"Int16","enum":{"0":"Idle","1":"Running","2":"Paused","3":"Faulted"}},
                   "Program_No": {"type":"Int16","value":12},
                   "Cycle_Count": {"type":"UInt32"}, "Cycle_Time": {"type":"Float","unit":"s"} },
      "Spindle": { "Speed": {"type":"Float","unit":"rpm"}, "Temp": {"type":"Float","unit":"°C","alarm":{"hi":70}} },
      "Coolant": { "Pump_On": {"type":"Boolean"}, "Temp": {"type":"Float","unit":"°C"},
                   "Pressure": {"type":"Float","unit":"bar","alarm":{"lo":2.0}} },
      "Safety":  { "EStop_OK": {"type":"Boolean","value":true}, "Light_Curtain_Clear": {"type":"Boolean","value":true} }
    }
  }
}
```

How each tag gets its value:
- **Built-in profile logic** (Python, one profile per PLC: `conveyor`, `robot_cell`) drives the tags that
  depend on each other. Examples: speed ramps toward the setpoint when `Run_Cmd` is on, current rises
  with speed, motor temp lags behind current, an over-temperature trips `Fault_Code`, the drive ramps
  down, cools, and auto-resets. The robot runs through cycles, increments `Cycle_Count`, and pauses
  when the light curtain trips (a random event every few minutes).
- **Generic `sim`** (`const` / `counter` / `walk`) covers any tag you add that the profile doesn't know
  about, so you can add a tag in JSON alone. Your plain `{"PLC_ON": 1}` form is also accepted (static tag).
- `alarm` limits → the PLC sets the matching status, and the gateway emits alarm events to `scada-events`.

### PLC service (`plc/`)
- `asyncua` server, SecurityPolicy None + anonymous to start (Q3). Tags are read-only nodes.
- Each PLC is its own Deployment + Service (`plc-1:4840`, `plc-2:4840`). `PLC_ID` comes from an env var.
- Check: `test_tags.py` covers config parsing, node-id generation and alarm thresholds.

### Gateway (`gateway/`)
- `asyncua` client with one **subscription per PLC** (publishing interval 250 ms), not polling. That's
  the real OPC UA pattern.
- Batches data-change notifications into one `plc-tags` message per PLC per interval. Each value carries
  OPC UA `StatusCode` (quality) and source timestamp.
- PLC disconnect → reconnect with backoff. Emits `plc_offline` / `plc_online` to `scada-events` and
  marks that PLC's last values as quality `Bad`.
- Browses each PLC's address space on connect, so new JSON tags show up with no gateway change.

**Done when:** editing `k8s/config/plc-tags.json` + `kubectl apply -k k8s` → PLC restarts → the new tag
appears in `plc-tags`.

## Phase 2 — IMU ×4 and ArUco cameras ×4

- **IMU**: the existing simulator gets 4 Deployments (`imu-1`..`imu-4`), each with a different
  tilt amplitude/frequency/axis so the plots don't look identical.
- **Camera** (`camera/`), 15 Hz:
  - Each camera watches 2–3 markers moving along a **closed path in the camera frame**: the marker rides
    a rounded-rectangle loop (like parts on a conveyor) at a constant speed with small jitter.
  - Pose: `tvec` (x, y, z in m) is the position along the path. `rvec` has yaw following the direction
    of travel, plus small roll/pitch wobble.
  - Realism: the marker drops out (`visible: false`) when it leaves the field of view or at random 1–2%
    of frames; pose noise grows with distance.
  - Each camera gets its own path size/speed/offset via env.
  - Check: assert consecutive poses are continuous (no jumps > speed × dt × margin).
- All 10 device Deployments carry labels `role=device`, `kind=plc|imu|camera`,
  `scada/controllable=true`.
  - ponytail: one Deployment block per device repeated in YAML. Template it once device count or
    copy-paste bugs actually hurt.

**Done when:** all four topics flow (`kafka-console-consumer`), and camera positions trace a loop when plotted.

## Phase 3 — MongoDB + historian + query UI

### Data layout — database `scada`

| Collection | Type | Document | Notes |
|---|---|---|---|
| `plc_tags` | time-series, granularity `seconds` | `{ts, meta:{plc, tag, unit}, value, quality}` | one doc per tag change; query a single tag's history quickly |
| `imu` | time-series, `seconds` | `{ts, meta:{device_id}, seq, accel:{x,y,z}, gyro:{x,y,z}}` | |
| `camera_poses` | time-series, `seconds` | `{ts, meta:{device_id, marker_id}, seq, tvec:{…}, rvec:{…}, visible}` | |
| `events` | regular | `{ts, device_id, type, severity, code, message}` | alarms, faults, offline/online, operator stop/start. Index `{ts:-1}`, `{device_id:1, ts:-1}` |
| `devices` | regular | `{_id: device_id, kind, first_seen, last_seen, tags:[…]}` | upserted registry. `tags` holds the PLC address space as the gateway browsed it |

- Time-series collections expire data after 7 days (`expireAfterSeconds`), so the PVC stays small.
- Mongo itself: StatefulSet, 1 replica, PVC, `--wiredTigerCacheSizeGB 0.25`, memory limit 512 Mi.

### Historian (`historian/`)
- Consumer group `historian` on all four topics. Writes with `insert_many(ordered=False)` every 500 ms
  or 1000 docs, **commits offsets only after the insert succeeds** (at-least-once).
- Duplicates after a crash: time-series collections can't have unique indexes. The historian keeps the
  last `(boot_id, seq)` per device in memory and drops duplicates on replay, which covers the common case.
  - ponytail: an in-memory dedupe misses duplicates that cross a historian restart. Switch to regular
    collections with a unique index if exact-once storage matters.

### Query UI
- **mongo-express** Deployment (~64 Mi): browse collections and run find/aggregate queries in the
  browser. Reached via `kubectl port-forward svc/mongo-express 8081`. Basic-auth from a Secret.
- **MongoDB Compass** (desktop) also works against `port-forward svc/mongo 27017`. No setup needed.
- `docs/queries.md`: ready-made queries, e.g. motor temp last hour, average cycle time per hour,
  alarms today, one camera's path.

**Done when:** mongo-express shows all collections growing, and killing the historian pod mid-stream
leaves no gaps (verified by a `seq` continuity aggregate).

## Phase 4 — API (FastAPI)

- **Live stream** `GET /api/stream` (SSE): one consumer (group `dashboard-live`, `latest`) on all topics,
  fanned out to browsers. IMU/camera are downsampled to about 5 Hz per device on the server. PLC tag
  changes are sent as-is.
- **Devices** `GET /api/devices`: k8s watch on `role=device` pods + Deployments →
  `{id, kind, node, desired, ready, restarts, last_msg_age}`.
  Status: 🟢 Ready and data seen within 3 s · 🟡 Ready but silent / PLC quality Bad · 🔴 scaled to 0 or not Ready.
- **Control** `POST /api/devices/{id}/stop|start` → patch `deployments/scale` to 0 or 1. Writes an
  operator event to `scada-events`.
  - RBAC: ServiceAccount + namespaced Role allowing only `pods get/list/watch`,
    `deployments get/list/watch` and `deployments/scale get/patch`.
  - The API refuses any Deployment without `scada/controllable=true`, so the UI can never scale Kafka or Mongo.
- **Kafka panel** `GET /api/kafka`: msgs/sec per topic (from end-offset deltas) and consumer lag for
  `historian` and `dashboard-live`.
- **History** `GET /api/history/{device}?tag=&minutes=` reads Mongo, used to fill charts on page load.
- **Events** `GET /api/events?limit=` returns the recent alarm/event log.
- Tags are **read-only**: there is no write path to the PLCs anywhere.
- Check: `test_status.py` covers the green/amber/red rules, and a test that the API refuses a non-controllable Deployment.

**Done when:** `curl -X POST …/imu-3/stop` → pod gone, `/api/devices` shows red, an event is logged.

## Phase 5 — React frontend (TypeScript)

- Vite + React + TS. Runtime deps: `react`, `react-dom`, `uplot` (small, handles 10 Hz+ well).
  Icons are hand-made inline SVGs (PLC, IMU, camera, gateway, broker, DB). No UI or icon library.
- Multi-stage Dockerfile: `node` builds `web/` → static files copied into the `api` image, so it's still one pod.
- Single page:
  1. **Topology:** Edge / Core columns, a card per pod with icon, status dot, node name, restarts.
     Device cards get Stop/Start with an inline "confirm?" step. Core services are view-only.
  2. **PLC panel:** tag tree per PLC with value, unit, quality and alarm colouring. Values flash on change.
  3. **Motion panel:** IMU accel/gyro sparklines (4 devices) and camera x/y path plots (4 devices) with a fading trail.
  4. **Kafka panel:** msgs/sec per topic and consumer lag.
  5. **Event log:** live alarms and operator actions.
- Native `EventSource` reconnects on its own. A banner shows when the stream is down.
- CI: add `npm ci && tsc --noEmit && vite build`.
- Restyled to match your SMR dashboard once you share it.

**Done when:** Stop a camera in the UI → red within ~3 s, its plot freezes, an event appears. Start → it recovers.

## Phase 6 — Message rate + reliability (memory-conscious)

- **Kafka → 3 brokers** (KRaft combined mode), heap 256 MB each, limit 512 Mi. Topics RF=3,
  `min.insync.replicas=2`. Producers already use `acks=all` + idempotence.
- **Edge buffering:** producers get a large `message.timeout.ms` and a bounded local queue, so devices
  ride out a broker outage and catch up. The gateway does the same.
- **Rate knob:** env `RATE_HZ` on IMU/camera, plus a `kafka-producer-perf-test.sh` script (it ships in
  the Kafka image, so nothing new to install) for ceiling tests.
  Target to prove: **≥ 5k msgs/s sustained** end to end on a laptop with lag staying bounded (Q4).
- Historian batching tuned for that rate.
- **Chaos checks** (`k8s/chaos.sh`): delete a broker pod, delete the historian, scale the gateway to 0.
  Each one is followed by an automated check: no `seq` gaps in Mongo, lag returns to ~0.
- Memory budget:

  | Component | Limit |
  |---|---|
  | Kafka ×3 | 1.5 Gi |
  | MongoDB | 512 Mi |
  | 10 devices + gateway | ~600 Mi |
  | historian, api, mongo-express | ~350 Mi |
  | **Total** | **≈ 3 Gi** (Docker currently has 7.7 GB) |

## Phase 7 — CI/CD on GitHub Actions

Built on the Phase 0 skeleton:
- **On PR:** lint, unit tests, TypeScript build, manifest validation, image builds (no push).
- **On push to `main`:**
  1. Build and push all images to **GHCR** (`ghcr.io/cylissama/edge-scada-sim-<svc>:<sha>` and `:latest`).
  2. **End-to-end job:** create a kind cluster in the runner, `kubectl apply -k` with the GHCR tags,
     run `k8s/smoke.sh`. The smoke test asserts all 10 devices are green, every topic has traffic, Mongo
     counts are growing, and stop/start through the API works.
- **CD target** (Q1): by default "deploy" means verified, versioned images in GHCR plus the e2e check.
  `deploy.sh --pull <sha>` runs any green build locally. Adding a real environment later (AWS) only adds a deploy job.
- **On tag `v*`:** a GitHub Release with the image tags in the notes.

---

## Order & size

| Phase | Size | Depends on |
|---|---|---|
| 0 Foundation + CI skeleton | S | — |
| 1 OPC UA PLCs + gateway | L (most new learning) | 0 |
| 2 IMU / cameras | S–M | 0 |
| 3 Mongo + historian + mongo-express | M | 1, 2 |
| 4 FastAPI | M | 1, 2 |
| 5 React UI | M–L | 4 |
| 6 Rate + reliability | M | 0–5 |
| 7 CI/CD full | S–M | 0, 5 |

Phases 1 and 2 can be swapped (2 is quick). Phases 3 and 4 are independent of each other.

## Skipped on purpose

- Schema Registry / Avro: the JSON envelope is enough. Add it if message formats start to drift.
- Prometheus / Grafana: the Kafka panel covers throughput and lag. Add it if you want the resume keyword.
- Helm: plain YAML + kustomize, as decided.
- OPC UA writes and PLC command paths: tags are read-only.

## Resolved

1. **CD:** images pushed to GHCR + e2e on kind in Actions. No deploy target until AWS.
2. **Repo:** public, renamed `cylissama/kafka-testing` → `cylissama/edge-scada-sim`.
3. **OPC UA security:** None + anonymous for now. Certificates are an optional later learning step.
4. **Rate target:** ≥ 5k msgs/s sustained, with bounded lag.
5. **Tag scenario:** conveyor (plc-1) + robot cell (plc-2).

## Open

- SMR dashboard reference, needed for the Phase 5 styling only.
