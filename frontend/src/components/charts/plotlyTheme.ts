import type { Layout } from "plotly.js";

export const PALETTE = [
  "#4dabf7", "#ff922b", "#69db7c", "#f06595", "#845ef7",
  "#ffd43b", "#3bc9db", "#ff6b6b", "#94d82d", "#748ffc",
];

export const baseLayout: Partial<Layout> = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: { color: "#ddd", size: 12 },
  margin: { l: 50, r: 20, t: 20, b: 40 },
  legend: { font: { color: "#ddd" } },
  xaxis: { gridcolor: "#2a2a2a", zerolinecolor: "#333" },
  yaxis: { gridcolor: "#2a2a2a", zerolinecolor: "#333" },
};

export const plotConfig = { displaylogo: false, responsive: true };
