// Headless version of fplreview_export.js: load FPL Review's free planner, connect a
// team id (any valid one - the projections are the same for everyone), open the
// PROJECTIONS tab and write the table as a CSV that transfer_plan.py and
// import_projections.py both read.
//
//   npm install -g playwright && npx playwright install chromium   (once)
//   node backend/scripts/fplreview_fetch.js <team id> [out.csv]
//
// Requests are fetched on the Node side and handed to the browser, so an HTTPS_PROXY
// with a custom CA (NODE_EXTRA_CA_CERTS) works without weakening the browser's TLS.
const { chromium } = require("playwright");
const fs = require("fs");

const teamId = process.argv[2];
const outPath = process.argv[3] || "fplreview.csv";
if (!teamId) {
  console.error("usage: node fplreview_fetch.js <team id> [out.csv]");
  process.exit(2);
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
    args: ["--no-sandbox"],
  });
  const ctx = await browser.newContext({
    proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined,
    viewport: { width: 1600, height: 1200 },
  });
  const page = await ctx.newPage();
  await page.route("**/*", async (route) => {
    try {
      const resp = await route.fetch();
      await route.fulfill({ response: resp });
    } catch {
      await route.abort();
    }
  });

  await page.goto("https://app.fplreview.com/free", { waitUntil: "load", timeout: 60000 });
  await page.waitForTimeout(5000);
  await page.locator("input").first().fill(String(teamId));
  await page.getByRole("button", { name: /connect/i }).first().click();
  await page.waitForTimeout(10000);
  await page.getByText(/^projections$/i).first().click();
  await page.waitForSelector("tr", { timeout: 30000 });
  await page.waitForTimeout(3000);

  const csv = await page.evaluate(() => {
    const fiberOf = (el) => {
      for (const k in el) if (k.startsWith("__reactFiber$")) return el[k];
    };
    let f = fiberOf(document.querySelector("tr"));
    let all = null;
    for (let depth = 0; f && depth < 80; depth++, f = f.return) {
      const p = f.memoizedProps;
      if (p && Array.isArray(p.allPlayers) && p.allPlayers.length > 100) {
        all = p.allPlayers;
        break;
      }
    }
    if (!all) return null;
    const POS = { 1: "GK", 2: "DEF", 3: "MID", 4: "FWD" };
    const gws = [...new Set(all.flatMap((p) => Object.keys(p.projections || {})))]
      .map(Number)
      .sort((a, b) => a - b);
    const esc = (v) => (/[",]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : v);
    const r2 = (x) => Math.round((x || 0) * 100) / 100;
    const header = ["name", "team", "pos", "price", "code", "id",
      ...gws.map((g) => `${g}_Pts`), ...gws.map((g) => `${g}_xMins`)];
    const lines = [header.join(",")];
    for (const p of all) {
      lines.push([
        esc(p.web_name), p.team_short, POS[p.element_type], (p.now_cost / 10).toFixed(1),
        p.code ?? "", p.id ?? "",
        ...gws.map((g) => r2(p.projections?.[g])),
        ...gws.map((g) => Math.round(p.fixtures?.[g]?.[0]?.xMins || 0)),
      ].join(","));
    }
    return lines.join("\n");
  });
  await browser.close();
  if (!csv) {
    console.error("Projections table not found - the page layout may have changed.");
    process.exit(1);
  }
  fs.writeFileSync(outPath, csv + "\n");
  console.log(`${outPath}: ${csv.split("\n").length - 1} players`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
