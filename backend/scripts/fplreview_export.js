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
// The free tier projects six gameweeks ahead; premium extends the horizon to 14.
//
// Columns per gameweek, all from FPL Review's per-fixture projection object:
//   N_Pts    expected FPL points            N_xG   expected goals
//   N_xMins  expected minutes               N_xA   expected assists
//   N_xCS    clean sheet probability        N_xDC  probability of defensive-contribution points
//   N_opp    opponent(s) with (H)/(A)
// Plus one per-player column, `price_progress`: FPL Review's estimate of how far the player is
// towards their next price change, -1000 (about to fall) to +1000 (about to rise). The Prices
// page shows it as a percentage.
// The importer stores all but opp (which is for reading the file by eye). `code` is FPL's
// stable player id, so the import matches on it and never has to guess by name.
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
  const esc = (v) => (v == null ? "" : /[",]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const r = (x, d) => (x == null || Number.isNaN(x) ? "" : Math.round(x * 10 ** d) / 10 ** d);
  // A gameweek's fixtures are an array: one entry normally, two in a double, none in a blank.
  const sum = (fx, k) => fx.reduce((s, x) => s + (x?.[k] || 0), 0);

  const perGw = ["Pts", "xMins", "xG", "xA", "xCS", "xDC", "opp"];
  const header = ["code", "name", "team", "pos", "price", "ownership", "price_progress",
    ...gws.flatMap((g) => perGw.map((s) => `${g}_${s}`))];
  const lines = [header.join(",")];
  for (const p of all) {
    const row = [p.code, esc(p.web_name), p.team_short, POS[p.element_type],
      (p.now_cost / 10).toFixed(1), p.selected_by_percent, p.price_progress ?? ""];
    for (const g of gws) {
      const fx = p.fixtures?.[g] || [];
      const opp = fx.map((x) => `${x.opponent}(${x.isHome ? "H" : "A"})`).join("+");
      row.push(r(p.projections?.[g], 2), r(sum(fx, "xMins"), 0), r(sum(fx, "xG"), 3),
        r(sum(fx, "xA"), 3), r(sum(fx, "xCS"), 3), r(sum(fx, "livdc"), 3), esc(opp));
    }
    lines.push(row.join(","));
  }

  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "fplreview.csv";
  a.click();
  console.log(`fplreview.csv: ${all.length} players, GW${gws[0]}-${gws[gws.length - 1]}`);
})();
