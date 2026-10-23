import type { ScadaEvent } from "../types";

const time = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });

export function EventLog({ events }: { events: ScadaEvent[] }) {
  return (
    <section className="panel">
      <h2>Events &amp; alarms</h2>
      {!events.length && <p className="muted">No events yet.</p>}
      <ul className="events">
        {events.map((e, i) => (
          <li key={`${e.ts}-${e.device_id}-${i}`} className={`sev-${e.severity}`}>
            <span className="mono muted">{time.format(new Date(e.ts))}</span>
            <span className="sev">{e.severity}</span>
            <span className="mono">{e.device_id}</span>
            <span>{e.message}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
