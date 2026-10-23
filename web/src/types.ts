export type Status = "ok" | "stale" | "down" | "stopped";

export interface Component {
  id: string;
  kind: string;
  role: string;
  tier: "edge" | "core";
  controllable: boolean;
  desired: number;
  ready: number;
  restarts: number;
  node: string | null;
  last_msg_age: number | null;
  status: Status;
}

export interface KafkaStats {
  topics: { name: string; partitions: number; messages: number; rate: number | null }[];
  rate_total: number | null;
  lag: Record<string, number>;
}

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

export interface TagValue {
  v: number | boolean | string;
  q: string;
  ts: string | null;
}

export interface TagSchema {
  type: string;
  unit: string;
  enum: Record<string, string>;
  alarm: Record<string, number | boolean>;
}

export interface PlcState {
  schema: Record<string, TagSchema>;
  values: Record<string, TagValue>;
}

export interface Envelope<T> {
  device_id: string;
  kind: string;
  boot_id: string;
  seq: number;
  ts: string;
  data: T;
}

export interface ScadaEvent {
  ts: string;
  device_id: string;
  type: string;
  severity: string;
  message: string;
}

export interface Marker {
  marker_id: number;
  visible: boolean;
  tvec: Vec3 | null;
}
