import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

export interface Series {
  label: string;
  color: string;
}

interface Props {
  data: uPlot.AlignedData; // [x (unix s), y1, y2, ...]
  series: Series[];
  height?: number;
  unit?: string;
  showTime?: boolean; // false: sparkline without the time axis
}

const hms = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hourCycle: "h23" });

const axis = { stroke: "#8b95a5", grid: { stroke: "#232a35", width: 1 }, ticks: { stroke: "#232a35" } };

/** Thin uPlot wrapper: creates the plot once, then only pushes new data (cheap at 4 renders/s). */
export function Chart({ data, series, height = 120, unit, showTime = true }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);

  useEffect(() => {
    const node = el.current!;
    const u = new uPlot(
      {
        width: node.clientWidth,
        height,
        legend: { show: false },
        cursor: { show: false },
        scales: { x: { time: true } },
        axes: [
          { ...axis, show: showTime, space: 70, values: (_u, ticks) => ticks.map(hms) },
          { ...axis, size: 44, label: unit, labelSize: unit ? 14 : 0 },
        ],
        series: [{}, ...series.map((s) => ({ label: s.label, stroke: s.color, width: 1.4, points: { show: false } }))],
      },
      data,
      node,
    );
    plot.current = u;
    const ro = new ResizeObserver(() => u.setSize({ width: node.clientWidth, height }));
    ro.observe(node);
    return () => {
      ro.disconnect();
      u.destroy();
    };
  }, []); // series/height are fixed per chart instance

  useEffect(() => {
    plot.current?.setData(data);
  });

  return <div ref={el} className="chart" />;
}
