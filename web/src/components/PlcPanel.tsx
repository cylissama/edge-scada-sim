import { Fragment, useEffect, useState } from "react";
import type { PlcState, TagSchema, TagValue } from "../types";
import { Chart } from "./Chart";

const FLASH_MS = 800;
const num = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

/** Same rules as the gateway's alarm_level (minus deadband): which limit, if any, is breached. */
function alarmLevel(alarm: TagSchema["alarm"] | undefined, v: TagValue["v"]): string | null {
  if (!alarm) return null;
  if ("ne" in alarm && v !== alarm.ne) return "ne";
  if (typeof v !== "number") return null;
  for (const [level, hit] of [["hihi", v >= +alarm.hihi], ["lolo", v <= +alarm.lolo], ["hi", v >= +alarm.hi], ["lo", v <= +alarm.lo]] as const)
    if (level in alarm && hit) return level;
  return null;
}

function formatValue(v: TagValue["v"], schema?: TagSchema) {
  const label = schema?.enum?.[String(Number(v))];
  if (label && typeof v !== "boolean") return label;
  if (typeof v === "boolean") return v ? "ON" : "OFF";
  if (typeof v === "number") return num.format(v);
  return String(v);
}

function Trend({ plc, tag, unit }: { plc: string; tag: string; unit: string }) {
  const [data, setData] = useState<[number[], number[]]>([[], []]);
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch(`/api/history/${plc}?tag=${encodeURIComponent(tag)}&minutes=10&limit=600`)
        .then((r) => r.json())
        .then((rows: { ts: string; value: number }[]) => {
          if (alive && Array.isArray(rows))
            setData([rows.map((r) => Date.parse(r.ts) / 1000), rows.map((r) => Number(r.value))]);
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [plc, tag]);
  return (
    <div className="trend">
      <div className="muted small">
        {tag} · last 10 min{unit && ` (${unit})`}
      </div>
      <Chart key={`${plc}/${tag}`} data={data} series={[{ label: tag, color: "#4cc2ff" }]} height={140} unit={unit} />
    </div>
  );
}

function Plc({ id, plc, changedAt }: { id: string; plc: PlcState; changedAt: Record<string, number> }) {
  const [selected, setSelected] = useState<string | null>(null);
  const now = Date.now();
  const tags = Object.keys({ ...plc.schema, ...plc.values }).sort();
  const folders = new Map<string, string[]>();
  for (const t of tags) {
    const folder = t.includes(".") ? t.slice(0, t.indexOf(".")) : "";
    folders.set(folder, [...(folders.get(folder) ?? []), t]);
  }
  const bad = Object.values(plc.values).some((v) => v.q !== "Good");
  const active = tags.filter((t) => plc.values[t] && alarmLevel(plc.schema[t]?.alarm, plc.values[t].v)).length;

  return (
    <div className="card plc">
      <div className="plc-head">
        <h3>{id}</h3>
        <span className={`pill ${bad ? "status-stale" : "status-ok"}`}>
          <span className="dot" />
          {bad ? "Comms bad" : "Comms good"}
        </span>
        <span className={`pill ${active ? "status-down" : "status-idle"}`}>
          {active} active alarm{active === 1 ? "" : "s"}
        </span>
      </div>
      <table className="tags">
        <tbody>
          {[...folders].map(([folder, names]) => (
            <Fragment key={folder}>{folderRows(folder, names)}</Fragment>
          ))}
        </tbody>
      </table>
      {selected && <Trend plc={id} tag={selected} unit={plc.schema[selected]?.unit ?? ""} />}
    </div>
  );

  function folderRows(folder: string, names: string[]) {
    return (
      <>
        {folder && (
          <tr className="folder">
            <td colSpan={3}>{folder}</td>
          </tr>
        )}
        {names.map((tag) => {
          const v = plc.values[tag];
          const schema = plc.schema[tag];
          const level = v ? alarmLevel(schema?.alarm, v.v) : null;
          const numeric = v && typeof v.v === "number" && !schema?.enum?.[String(v.v)];
          const flashing = now - (changedAt[`${id}/${tag}`] ?? 0) < FLASH_MS;
          return (
            <tr
              key={tag}
              className={`${level ? `alarm alarm-${level}` : ""} ${numeric ? "clickable" : ""} ${selected === tag ? "selected" : ""}`}
              onClick={() => numeric && setSelected(selected === tag ? null : tag)}
              title={numeric ? "Click for a 10-minute trend" : undefined}
            >
              <td className="tag-name">{tag.slice(folder ? folder.length + 1 : 0)}</td>
              <td className={`tag-value ${flashing ? "flash" : ""} ${v?.q !== "Good" ? "bad-quality" : ""}`}>
                {v ? formatValue(v.v, schema) : "—"}
                {schema?.unit && <span className="unit"> {schema.unit}</span>}
              </td>
              <td className="tag-flag">{level ? level.toUpperCase() : v && v.q !== "Good" ? v.q : ""}</td>
            </tr>
          );
        })}
      </>
    );
  }
}

export function PlcPanel({ plc, changedAt }: { plc: Record<string, PlcState>; changedAt: Record<string, number> }) {
  const ids = Object.keys(plc).sort();
  return (
    <section className="panel">
      <h2>PLC tags</h2>
      {!ids.length && <p className="muted">Waiting for PLC data…</p>}
      <div className="plc-grid">
        {ids.map((id) => (
          <Plc key={id} id={id} plc={plc[id]} changedAt={changedAt} />
        ))}
      </div>
    </section>
  );
}
