import type { Layout } from "plotly.js";

// Mirrors theme.css - Plotly configs are plain JS/JSON, so these can't reference CSS custom
// properties directly and are kept in sync by hand.
export const PALETTE = [
  "#00e58a", "#8b5cf6", "#4dabf7", "#f5a623", "#ff5c7a",
  "#3bc9db", "#ffd43b", "#69db7c", "#f06595", "#748ffc",
];

export const baseLayout: Partial<Layout> = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: { color: "#9aa0b4", size: 12, family: "Inter, -apple-system, sans-serif" },
  margin: { l: 50, r: 20, t: 20, b: 40 },
  legend: { font: { color: "#9aa0b4" } },
  xaxis: { gridcolor: "#262a37", zerolinecolor: "#363c4c" },
  yaxis: { gridcolor: "#262a37", zerolinecolor: "#363c4c" },
  colorway: PALETTE,
};

export const plotConfig = { displaylogo: false, responsive: true };
