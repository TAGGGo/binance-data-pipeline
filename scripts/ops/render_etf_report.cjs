// Render the ETF Daily report images (print layout of the dashboard's "ETF Daily" tab) with Playwright.
// Runs in the Claude cloud workspace, not on the Mac:
//   node render_etf_report.cjs <site_dir> <out_dir> [assets=BTC,ETH,SOL,XRP,ZEC] [lang=zh]
// <site_dir> holds index.html (mdh/dashboard/page.html), data.json and coins/. Writes etf_daily_<ASSET>_<date>_<lang>.png
const { chromium } = require("playwright");
const http = require("http"), fs = require("fs"), path = require("path");
const [site, out, assetsArg, lang = "zh"] = process.argv.slice(2);
const assets = (assetsArg || "BTC,ETH,SOL,XRP,ZEC").split(",");
const types = { ".html": "text/html; charset=utf-8", ".json": "application/json" };
const server = http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split("?")[0]); if (p === "/") p = "/index.html";
  const f = path.join(site, p);
  if (!fs.existsSync(f)) { res.writeHead(404); return res.end(); }
  let body = fs.readFileSync(f);
  if (p === "/index.html") body = Buffer.concat([Buffer.from('<!doctype html><meta charset="utf-8"><style>[hidden]{display:none!important}body{margin:0}</style>'), body]);
  res.writeHead(200, { "Content-Type": types[path.extname(f)] || "application/octet-stream" }); res.end(body);
}).listen(0, async () => {
  const port = server.address().port;
  const exe = ["/opt/pw-browsers/chromium-1194/chrome-linux/chrome"].find((x) => fs.existsSync(x));
  const b = await chromium.launch(exe ? { executablePath: exe } : {});
  const date = JSON.parse(fs.readFileSync(path.join(site, "data.json"))).etf_report;
  fs.mkdirSync(out, { recursive: true });
  for (const a of assets) {
    if (!date[a] || date[a].error) { console.log("skip", a); continue; }
    const p = await b.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 2, colorScheme: "light" });
    await p.goto(`http://localhost:${port}/?print=1&asset=${a}&lang=${lang}#etfreport`);
    await p.waitForTimeout(3500);
    const f = path.join(out, `etf_daily_${a}_${date[a].date}_${lang}.png`);
    await (await p.$("#er-sheet")).screenshot({ path: f });
    console.log("wrote", f);
    await p.close();
  }
  await b.close(); server.close();
});
