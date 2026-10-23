import type { KafkaStats } from "../types";
import { Chart } from "./Chart";

const num = new Intl.NumberFormat();

export function KafkaPanel({ kafka, history }: { kafka: KafkaStats | null; history: { t: number[]; v: number[] } }) {
  return (
    <section className="panel">
      <h2>Kafka</h2>
      {!kafka ? (
        <p className="muted">Waiting for broker stats…</p>
      ) : (
        <>
          <div className="stat-row">
            <div className="stat">
              <div className="stat-value">{kafka.rate_total ?? "—"}</div>
              <div className="muted small">msgs/s total</div>
            </div>
            {Object.entries(kafka.lag).map(([group, lag]) => (
              <div key={group} className={`stat ${lag > 1000 ? "warn" : ""}`}>
                <div className="stat-value">{num.format(lag)}</div>
                <div className="muted small">lag · {group}</div>
              </div>
            ))}
          </div>
          <Chart data={[history.t, history.v]} series={[{ label: "msgs/s", color: "#4cc2ff" }]} height={110} unit="msg/s" />
          <table className="grid-table">
            <thead>
              <tr>
                <th>Topic</th>
                <th>Partitions</th>
                <th>Messages</th>
                <th>msgs/s</th>
              </tr>
            </thead>
            <tbody>
              {kafka.topics.map((t) => (
                <tr key={t.name}>
                  <td className="mono">{t.name}</td>
                  <td>{t.partitions}</td>
                  <td>{num.format(t.messages)}</td>
                  <td>{t.rate ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
