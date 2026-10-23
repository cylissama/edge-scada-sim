import { useState } from "react";
import type { Component } from "../types";
import { Icon } from "./Icon";

const STATUS_LABEL = { ok: "Online", stale: "No data", down: "Down", stopped: "Stopped" };

function Controls({ c }: { c: Component }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const action = c.desired === 0 ? "start" : "stop";

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`/api/components/${c.id}/${action}`, { method: "POST" });
      if (!r.ok) setError((await r.json()).detail ?? r.statusText);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  if (confirming)
    return (
      <div className="controls">
        <span className="confirm-text">{action === "stop" ? "Stop" : "Start"} {c.id}?</span>
        <button className={`btn ${action === "stop" ? "danger" : "primary"}`} onClick={run} disabled={busy}>
          {busy ? "…" : "Yes"}
        </button>
        <button className="btn" onClick={() => setConfirming(false)} disabled={busy}>
          No
        </button>
      </div>
    );
  return (
    <div className="controls">
      <button className={`btn ${action === "stop" ? "" : "primary"}`} onClick={() => setConfirming(true)}>
        {action === "stop" ? "Stop" : "Start"}
      </button>
      {error && <span className="error-text">{error}</span>}
    </div>
  );
}

function Card({ c }: { c: Component }) {
  const age = c.last_msg_age;
  return (
    <div className={`card node status-${c.status}`}>
      <div className="node-head">
        <span className="node-icon">
          <Icon kind={c.kind} />
        </span>
        <div className="node-title">
          <div className="node-id" title={c.id}>
            {c.id}
          </div>
          <span className={`pill status-${c.status}`}>
            <span className="dot" />
            {STATUS_LABEL[c.status]}
          </span>
        </div>
      </div>
      <dl className="node-meta">
        <dt>Pods</dt>
        <dd>
          {c.ready}/{c.desired}
        </dd>
        <dt>Restarts</dt>
        <dd>{c.restarts}</dd>
        {c.role !== "core-service" && (
          <>
            <dt>Last msg</dt>
            <dd>{age == null ? "—" : age < 1 ? "now" : `${age.toFixed(0)} s ago`}</dd>
          </>
        )}
      </dl>
      {c.controllable && <Controls c={c} />}
    </div>
  );
}

export function Topology({ components }: { components: Component[] }) {
  const tiers = [
    { key: "edge", title: "Edge", note: "devices and edge services, one pod each" },
    { key: "core", title: "Core", note: "broker, storage and API" },
  ] as const;
  return (
    <section className="panel">
      <h2>Topology</h2>
      {components.length === 0 && <p className="muted">Waiting for cluster status…</p>}
      {tiers.map((t) => {
        const items = components.filter((c) => c.tier === t.key);
        if (!items.length) return null;
        const up = items.filter((c) => c.status === "ok").length;
        return (
          <div key={t.key} className="tier">
            <h3>
              {t.title} <span className="muted small">· {t.note} · {up}/{items.length} online</span>
            </h3>
            <div className="node-grid">
              {items.map((c) => (
                <Card key={c.id} c={c} />
              ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}
