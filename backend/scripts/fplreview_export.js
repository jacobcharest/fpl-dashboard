// Export FPL Review's projections to a CSV the dashboard can import.
//
// Premium members can skip this: use the site's own CSV download instead, which
// scripts/import_projections.py reads directly.
//
// On the FREE tier the download is disabled, but the projections themselves are present in the
// page. To grab them:
//   1. open https://app.fplreview.com/free and connect your team id
//   2. click the PROJECTIONS tab (the table must render - that's what mounts the data)
//   3. paste this whole file into the browser console and press enter
//   4. import the downloaded file:
//        backend/.venv/bin/python backend/scripts/import_projections.py 2026-27 ~/Downloads/fplreview.csv
//
// The free tier only projects a few gameweeks ahead; premium extends the horizon to 14.
(() => {
  const fiberOf = (el) => { for (const k in el) if (k.startsWith("__reactFiber$")) return el[k]; };
  let f = fiberOf(document.querySelector("tr")), all = null, depth = 0;
  while (f && depth < 80) {
    const p = f.memoizedProps;
    if (p && Array.isArray(p.allPlayers) && p.allPlayers.length > 100) { all = p.allPlayers; break; }
    f = f.return; depth++;
  }
  if (!all) throw new Error("Projections not found - open the PROJECTIONS tab first, then re-run.");

  const POS = { 1: "GK", 2: "DEF", 3: "MID", 4: "FWD" };
  const gws = [...new Set(all.flatMap((p) => Object.keys(p.projections || {})))]
    .map(Number).sort((a, b) => a - b);
  const esc = (v) => (/[",]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : v);
  const r2 = (x) => Math.round((x || 0) * 100) / 100;

  const header = ["name", "team", "pos", "price",
    ...gws.map((g) => `${g}_Pts`), ...gws.map((g) => `${g}_xMins`)];
  const lines = [header.join(",")];
  for (const p of all) {
    lines.push([
      esc(p.web_name), p.team_short, POS[p.element_type], (p.now_cost / 10).toFixed(1),
      ...gws.map((g) => r2(p.projections?.[g])),
      ...gws.map((g) => Math.round(p.fixtures?.[g]?.[0]?.xMins || 0)),
    ].join(","));
  }

  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "fplreview.csv";
  a.click();
  console.log(`fplreview.csv: ${all.length} players, GW${gws[0]}-${gws[gws.length - 1]}`);
})();
