import { EventLog } from "./components/EventLog";
import { KafkaPanel } from "./components/KafkaPanel";
import { Motion } from "./components/Motion";
import { PlcPanel } from "./components/PlcPanel";
import { Topology } from "./components/Topology";
import { useScada } from "./useScada";

export function App() {
  const s = useScada();
  const edge = s.topology.filter((c) => c.tier === "edge");
  const online = edge.filter((c) => c.status === "ok").length;

  return (
    <>
      <header className="topbar">
        <h1>edge-scada-sim</h1>
        <span className="muted">
          {online}/{edge.length} edge nodes online
        </span>
        <span className="muted">{s.kafka?.rate_total ?? "—"} msgs/s</span>
        <span className={`pill ${s.connected ? "status-ok" : "status-down"}`}>
          <span className="dot" />
          {s.connected ? "Live" : "Disconnected"}
        </span>
      </header>
      {!s.connected && <div className="banner">Live stream disconnected, reconnecting…</div>}
      <main>
        <Topology components={s.topology} />
        <PlcPanel plc={s.plc} changedAt={s.changedAt} />
        <Motion imu={s.imu} cameras={s.cameras} />
        <div className="two-col">
          <KafkaPanel kafka={s.kafka} history={s.rateHistory} />
          <EventLog events={s.events} />
        </div>
      </main>
    </>
  );
}
