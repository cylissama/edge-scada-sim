import { useEffect, useRef, useState } from "react";
import type { Component, Envelope, KafkaStats, Marker, PlcState, ScadaEvent, TagSchema, TagValue, Vec3 } from "./types";

const IMU_POINTS = 150; // 30 s at the API's 5 Hz downsampling
const TRAIL_POINTS = 400; // per camera
const RATE_POINTS = 150; // 5 min of kafka snapshots (every 2 s)
const MAX_EVENTS = 200;
const RENDER_MS = 250;

export interface ImuSeries {
  t: number[];
  ax: number[];
  ay: number[];
  az: number[];
  gx: number[];
  gy: number[];
  gz: number[];
}

export interface CameraState {
  markers: Record<number, Marker>;
  trail: { x: number; y: number; id: number; t: number }[];
}

export interface Scada {
  topology: Component[];
  kafka: KafkaStats | null;
  rateHistory: { t: number[]; v: number[] };
  plc: Record<string, PlcState>;
  changedAt: Record<string, number>; // "plc-1/Drive.Speed_Actual" -> ms timestamp of last change
  events: ScadaEvent[];
  imu: Record<string, ImuSeries>;
  cameras: Record<string, CameraState>;
}

const emptyImu = (): ImuSeries => ({ t: [], ax: [], ay: [], az: [], gx: [], gy: [], gz: [] });

function pushCapped<T>(arr: T[], value: T, cap: number) {
  arr.push(value);
  if (arr.length > cap) arr.splice(0, arr.length - cap);
}

const eventKey = (e: ScadaEvent) => `${e.ts}|${e.device_id}|${e.type}|${e.message}`;

/**
 * One EventSource for the whole page. Messages (~120/s) mutate buffers in a ref;
 * React re-renders at most every RENDER_MS, and only when something changed.
 */
export function useScada() {
  const store = useRef<Scada>({
    topology: [],
    kafka: null,
    rateHistory: { t: [], v: [] },
    plc: {},
    changedAt: {},
    events: [],
    imu: {},
    cameras: {},
  });
  const dirty = useRef(false);
  const [connected, setConnected] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const s = store.current;
    const touch = () => (dirty.current = true);

    // Snapshots for things the stream only sends as changes.
    fetch("/api/plc")
      .then((r) => r.json())
      .then((snap: Record<string, PlcState>) => {
        for (const [id, p] of Object.entries(snap)) {
          const live = s.plc[id];
          s.plc[id] = {
            schema: live && Object.keys(live.schema).length ? live.schema : p.schema,
            values: { ...p.values, ...live?.values },
          };
        }
        touch();
      })
      .catch(() => {});
    fetch("/api/events?limit=100")
      .then((r) => r.json())
      .then((hist: ScadaEvent[]) => {
        const seen = new Set(s.events.map(eventKey));
        s.events = [...s.events, ...hist.filter((e) => !seen.has(eventKey(e)))]
          .sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts))
          .slice(0, MAX_EVENTS);
        touch();
      })
      .catch(() => {});

    const es = new EventSource("/api/stream");
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false); // EventSource reconnects by itself

    const on = <T,>(name: string, fn: (data: T) => void) =>
      es.addEventListener(name, (e) => {
        fn(JSON.parse((e as MessageEvent).data));
        touch();
      });

    on<Component[]>("topology", (t) => (s.topology = t));
    on<KafkaStats>("kafka", (k) => {
      s.kafka = k;
      if (k.rate_total != null) {
        pushCapped(s.rateHistory.t, Date.now() / 1000, RATE_POINTS);
        pushCapped(s.rateHistory.v, k.rate_total, RATE_POINTS);
      }
    });
    on<Envelope<{ tags?: Record<string, TagValue>; schema?: Record<string, TagSchema> }>>("plc", (m) => {
      const p = (s.plc[m.device_id] ??= { schema: {}, values: {} });
      if (m.data.schema) p.schema = m.data.schema;
      const now = Date.now();
      for (const [tag, v] of Object.entries(m.data.tags ?? {})) {
        if (p.values[tag]?.v !== v.v) s.changedAt[`${m.device_id}/${tag}`] = now;
        p.values[tag] = v;
      }
    });
    on<Envelope<{ accel_ms2: Vec3; gyro_dps: Vec3 }>>("imu", (m) => {
      const d = (s.imu[m.device_id] ??= emptyImu());
      const { accel_ms2: a, gyro_dps: g } = m.data;
      const row: [keyof ImuSeries, number][] = [
        ["t", Date.parse(m.ts) / 1000], ["ax", a.x], ["ay", a.y], ["az", a.z], ["gx", g.x], ["gy", g.y], ["gz", g.z],
      ];
      for (const [k, v] of row) pushCapped(d[k], v, IMU_POINTS);
    });
    on<Envelope<{ markers: Marker[] }>>("camera", (m) => {
      const c = (s.cameras[m.device_id] ??= { markers: {}, trail: [] });
      const t = Date.parse(m.ts);
      for (const mk of m.data.markers) {
        c.markers[mk.marker_id] = mk;
        if (mk.visible && mk.tvec) pushCapped(c.trail, { x: mk.tvec.x, y: mk.tvec.y, id: mk.marker_id, t }, TRAIL_POINTS);
      }
    });
    on<Envelope<Omit<ScadaEvent, "ts" | "device_id">>>("event", (m) => {
      s.events.unshift({ ts: m.ts, device_id: m.device_id, ...m.data });
      if (s.events.length > MAX_EVENTS) s.events.length = MAX_EVENTS;
    });

    const timer = setInterval(() => {
      if (dirty.current) {
        dirty.current = false;
        setTick((t) => t + 1);
      }
    }, RENDER_MS);
    return () => {
      es.close();
      clearInterval(timer);
    };
  }, []);

  return { ...store.current, connected, tick };
}
