import { useEffect, useRef } from "react";
import type { CameraState, ImuSeries } from "../useScada";
import { Chart } from "./Chart";

const XYZ = [
  { label: "x", color: "#ff6b6b" },
  { label: "y", color: "#51cf66" },
  { label: "z", color: "#4cc2ff" },
];
const MARKER_COLORS = ["#ffd43b", "#4cc2ff", "#ff922b", "#cc5de8", "#51cf66", "#ff6b6b"];
const TRAIL_MS = 20_000;

function ImuCard({ id, s }: { id: string; s: ImuSeries }) {
  const last = s.t.length - 1;
  return (
    <div className="card imu">
      <h3>{id}</h3>
      <div className="muted small">
        accel m/s² {last >= 0 && `· ${s.ax[last].toFixed(2)}, ${s.ay[last].toFixed(2)}, ${s.az[last].toFixed(2)}`}
      </div>
      <Chart data={[s.t, s.ax, s.ay, s.az]} series={XYZ} height={80} showTime={false} />
      <div className="muted small">
        gyro °/s {last >= 0 && `· ${s.gx[last].toFixed(1)}, ${s.gy[last].toFixed(1)}, ${s.gz[last].toFixed(1)}`}
      </div>
      <Chart data={[s.t, s.gx, s.gy, s.gz]} series={XYZ} height={80} showTime={false} />
    </div>
  );
}

/** Top-down view of what the camera sees: fading trail per marker, current markers labelled. */
function CameraCard({ id, cam }: { id: string; cam: CameraState }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const bounds = useRef({ minX: -0.5, maxX: 0.5, minY: -0.5, maxY: 0.5 });

  useEffect(() => {
    const el = canvas.current!;
    const dpr = window.devicePixelRatio || 1;
    const w = el.clientWidth;
    const h = el.clientHeight;
    el.width = w * dpr;
    el.height = h * dpr;
    const ctx = el.getContext("2d")!;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    // Grow-only bounds so the view doesn't jitter as markers move.
    const b = bounds.current;
    for (const p of cam.trail) {
      b.minX = Math.min(b.minX, p.x);
      b.maxX = Math.max(b.maxX, p.x);
      b.minY = Math.min(b.minY, p.y);
      b.maxY = Math.max(b.maxY, p.y);
    }
    const pad = 24;
    const scale = Math.min((w - 2 * pad) / (b.maxX - b.minX), (h - 2 * pad) / (b.maxY - b.minY));
    const cx = (b.minX + b.maxX) / 2;
    const cy = (b.minY + b.maxY) / 2;
    const px = (x: number) => w / 2 + (x - cx) * scale;
    const py = (y: number) => h / 2 + (y - cy) * scale; // image coords: y grows downward

    // Optical axis crosshair
    ctx.strokeStyle = "#2a3240";
    ctx.beginPath();
    ctx.moveTo(px(0), 0);
    ctx.lineTo(px(0), h);
    ctx.moveTo(0, py(0));
    ctx.lineTo(w, py(0));
    ctx.stroke();

    const newest = cam.trail.length ? cam.trail[cam.trail.length - 1].t : 0;
    for (const p of cam.trail) {
      const age = newest - p.t;
      if (age > TRAIL_MS) continue;
      ctx.globalAlpha = 0.1 + 0.6 * (1 - age / TRAIL_MS);
      ctx.fillStyle = MARKER_COLORS[p.id % MARKER_COLORS.length];
      ctx.fillRect(px(p.x) - 1.5, py(p.y) - 1.5, 3, 3);
    }
    ctx.globalAlpha = 1;

    ctx.font = "11px ui-monospace, monospace";
    for (const m of Object.values(cam.markers)) {
      if (!m.visible || !m.tvec) continue;
      const color = MARKER_COLORS[m.marker_id % MARKER_COLORS.length];
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(px(m.tvec.x) - 6, py(m.tvec.y) - 6, 12, 12);
      ctx.fillStyle = color;
      ctx.fillText(`#${m.marker_id}`, px(m.tvec.x) + 9, py(m.tvec.y) + 4);
    }
  });

  const markers = Object.values(cam.markers);
  const visible = markers.filter((m) => m.visible).length;
  return (
    <div className="card camera">
      <h3>{id}</h3>
      <div className="muted small">
        {visible}/{markers.length} markers visible
      </div>
      <canvas ref={canvas} className="camera-canvas" />
    </div>
  );
}

export function Motion({ imu, cameras }: { imu: Record<string, ImuSeries>; cameras: Record<string, CameraState> }) {
  return (
    <section className="panel">
      <h2>Motion</h2>
      <h3 className="sub">IMUs · last 30 s</h3>
      <div className="motion-grid">
        {Object.keys(imu)
          .sort()
          .map((id) => (
            <ImuCard key={id} id={id} s={imu[id]} />
          ))}
      </div>
      <h3 className="sub">ArUco cameras · marker positions in the image plane (m)</h3>
      <div className="motion-grid">
        {Object.keys(cameras)
          .sort()
          .map((id) => (
            <CameraCard key={id} id={id} cam={cameras[id]} />
          ))}
      </div>
    </section>
  );
}
