# Querying the historian (MongoDB)

Database `scada`. All timestamps are UTC. Data older than 7 days expires automatically.

| Collection | Kind | One document = |
|---|---|---|
| `plc_tags` | time-series | one tag value change: `{ts, meta: {plc, tag, unit}, value, quality}` |
| `imu` | time-series | one IMU sample: `{ts, meta: {device_id}, boot_id, seq, accel: {x,y,z}, gyro: {x,y,z}}` |
| `camera_poses` | time-series | one marker in one frame: `{ts, meta: {device_id, marker_id}, boot_id, seq, visible, tvec, rvec}` |
| `events` | regular | alarm / alarm_cleared / plc_online / plc_offline / operator action: `{ts, device_id, type, severity, message, ...}` |
| `devices` | regular | one device: `{_id, kind, first_seen, last_seen, tags}` (`tags` = PLC address space as browsed by the gateway) |

## Where to run queries

**mongo-express** (web UI, browse + simple find queries):

```sh
kubectl -n scada port-forward svc/mongo-express 8081
# http://localhost:8081   login admin / admin
```

In a collection view, use **Advanced** to type a find query, e.g. `{"meta.tag": "Drive.Motor_Temp"}`, a
sort `{"ts": -1}` and a projection. Aggregations need mongosh.

**mongosh** (full shell, aggregations):

```sh
kubectl -n scada exec -it mongo-0 -- mongosh -u scada -p scada-dev-password --authenticationDatabase admin scada
```

**MongoDB Compass** (desktop app): `kubectl -n scada port-forward svc/mongo 27017`, then connect to
`mongodb://scada:scada-dev-password@localhost:27017/?authSource=admin`.

## Recipes (mongosh)

Latest value of every tag on a PLC:

```js
db.plc_tags.aggregate([
  {$match: {"meta.plc": "plc-1", ts: {$gte: new Date(Date.now() - 3600e3)}}},
  {$sort: {ts: 1}},
  {$group: {_id: "$meta.tag", value: {$last: "$value"}, quality: {$last: "$quality"}, unit: {$last: "$meta.unit"}, at: {$last: "$ts"}}},
  {$sort: {_id: 1}}
])
```

Motor temperature, last hour, one point per minute (min / avg / max):

```js
db.plc_tags.aggregate([
  {$match: {"meta.plc": "plc-1", "meta.tag": "Drive.Motor_Temp", ts: {$gte: new Date(Date.now() - 3600e3)}}},
  {$group: {_id: {$dateTrunc: {date: "$ts", unit: "minute"}}, min: {$min: "$value"}, avg: {$avg: "$value"}, max: {$max: "$value"}}},
  {$sort: {_id: 1}}
])
```

Robot cycles per hour and average cycle time:

```js
db.plc_tags.aggregate([
  {$match: {"meta.plc": "plc-2", "meta.tag": "Robot.Cycle_Time", value: {$gt: 0}}},
  {$group: {_id: {$dateTrunc: {date: "$ts", unit: "hour"}}, cycles: {$sum: 1}, avg_s: {$avg: "$value"}}},
  {$sort: {_id: -1}}
])
```

Alarms today, newest first:

```js
db.events.find(
  {type: "alarm", ts: {$gte: new Date(new Date().setUTCHours(0, 0, 0, 0))}},
  {_id: 0, ts: 1, device_id: 1, severity: 1, message: 1}
).sort({ts: -1})
```

How long each alarm stayed active (pairs alarm → alarm_cleared per tag):

```js
db.events.aggregate([
  {$match: {type: {$in: ["alarm", "alarm_cleared"]}}},
  {$sort: {ts: 1}},
  {$setWindowFields: {partitionBy: {d: "$device_id", t: "$tag"}, sortBy: {ts: 1},
                      output: {next: {$shift: {output: "$ts", by: 1}}}}},
  {$match: {type: "alarm"}},
  {$project: {_id: 0, device_id: 1, tag: 1, message: 1, ts: 1,
              active_s: {$dateDiff: {startDate: "$ts", endDate: "$next", unit: "second"}}}},
  {$sort: {ts: -1}}, {$limit: 20}
])
```

One marker's path over the last minute (for plotting x/y):

```js
db.camera_poses.find(
  {"meta.device_id": "camera-2", "meta.marker_id": 4, visible: true, ts: {$gte: new Date(Date.now() - 60e3)}},
  {_id: 0, ts: 1, "tvec.x": 1, "tvec.y": 1}
).sort({ts: 1})
```

Camera detection rate per camera (share of frames where a marker was seen):

```js
db.camera_poses.aggregate([
  {$match: {ts: {$gte: new Date(Date.now() - 600e3)}}},
  {$group: {_id: "$meta.device_id", frames: {$sum: 1}, seen: {$sum: {$cond: ["$visible", 1, 0]}}}},
  {$project: {visible_pct: {$round: [{$multiply: [{$divide: ["$seen", "$frames"]}, 100]}, 1]}}},
  {$sort: {_id: 1}}
])
```

IMU peak rotation rate per device, last 5 minutes:

```js
db.imu.aggregate([
  {$match: {ts: {$gte: new Date(Date.now() - 300e3)}}},
  {$group: {_id: "$meta.device_id", max_gyro_x: {$max: {$abs: "$gyro.x"}}, max_gyro_y: {$max: {$abs: "$gyro.y"}}}},
  {$sort: {_id: 1}}
])
```

Gap / duplicate audit: every `(device, boot_id)` should have a contiguous `seq` range:

```js
db.imu.aggregate([
  {$group: {_id: {d: "$meta.device_id", b: "$boot_id"}, n: {$sum: 1}, lo: {$min: "$seq"}, hi: {$max: "$seq"}, u: {$addToSet: "$seq"}}},
  {$project: {docs: "$n", unique: {$size: "$u"}, expected: {$add: [{$subtract: ["$hi", "$lo"]}, 1]}}},
  {$sort: {_id: 1}}
])
```

Devices and when they were last heard from:

```js
db.devices.find({}, {kind: 1, first_seen: 1, last_seen: 1}).sort({_id: 1})
```
