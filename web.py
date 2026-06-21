import asyncio
import logging
from collections import deque

from aiohttp import web

import db

LOG_BUFFER: deque[str] = deque(maxlen=200)

_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>BLE Scanner</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: monospace; background: #111; color: #eee; margin: 0; padding: 16px; }
  h1 { font-size: 1.1em; color: #7cf; margin: 0 0 14px; }
  .stats { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
  .stat { background: #1e1e1e; border: 1px solid #333; border-radius: 6px; padding: 10px 18px; min-width: 150px; }
  .stat .label { font-size: 0.72em; color: #888; margin-bottom: 4px; }
  .stat .value { font-size: 1.8em; color: #7cf; font-weight: bold; line-height: 1.1; }
  .stat .sub { font-size: 0.68em; color: #666; margin-top: 2px; }
  .controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
  select, button { background: #2a2a2a; color: #eee; border: 1px solid #555; border-radius: 4px; padding: 5px 12px; cursor: pointer; font-family: monospace; }
  button:hover { background: #3a3a3a; }
  button.reset { color: #f88; border-color: #833; }
  button.reset:hover { background: #311; }
  .updated { font-size: 0.7em; color: #555; }
  .chart-section { background: #1e1e1e; border: 1px solid #333; border-radius: 6px; padding: 12px 12px 8px; margin-bottom: 14px; }
  .chart-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .chart-label { font-size: 0.75em; color: #888; }
  .chart-wrap { position: relative; }
  #chart { display: block; width: 100%; height: 160px; }
  .tip { position: absolute; background: #2a2a2a; border: 1px solid #555; border-radius: 4px; padding: 3px 8px; font-size: 0.72em; pointer-events: none; display: none; white-space: nowrap; color: #eee; }
  #log { background: #000; border: 1px solid #2a2a2a; border-radius: 6px; padding: 12px; height: 50vh; overflow-y: auto; font-size: 0.76em; line-height: 1.6; white-space: pre-wrap; word-break: break-all; }
</style>
</head>
<body>
<h1>BLE Scanner</h1>
<div class="stats" id="stats"><div class="stat"><div class="label">Loading...</div></div></div>
<div class="controls">
  <label>Auto-refresh:
    <select id="interval">
      <option value="0">OFF</option>
      <option value="5">5s</option>
      <option value="10" selected>10s</option>
      <option value="30">30s</option>
      <option value="60">60s</option>
    </select>
  </label>
  <button onclick="fetchAll()">今すぐ更新</button>
  <button class="reset" onclick="doReset()">セッションリセット</button>
  <span class="updated" id="updated"></span>
</div>
<div class="chart-section">
  <div class="chart-header">
    <span class="chart-label">検出デバイス数の推移</span>
    <label><select id="histHours" onchange="fetchHistory()">
      <option value="1">1時間</option>
      <option value="3" selected>3時間</option>
      <option value="12">12時間</option>
    </select></label>
  </div>
  <div class="chart-wrap">
    <svg id="chart"></svg>
    <div class="tip" id="tip"></div>
  </div>
</div>
<div id="log"></div>
<script>
let timer = null;
let histData = [];

function fmt(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('ja-JP', {timeZone: 'Asia/Tokyo'});
}

async function fetchAll() {
  try {
    const [s, lines] = await Promise.all([
      fetch('/api/stats').then(r => r.json()),
      fetch('/api/logs').then(r => r.json()),
    ]);
    document.getElementById('stats').innerHTML =
      `<div class="stat"><div class="label">計測開始</div><div class="value" style="font-size:0.9em">${fmt(s.session_start)}</div></div>` +
      `<div class="stat"><div class="label">直近スキャン</div><div class="value">${s.latest_count}</div><div class="sub">${fmt(s.latest_time)}</div></div>` +
      `<div class="stat"><div class="label">累積検出デバイス数</div><div class="value">${s.total_devices}</div></div>` +
      `<div class="stat"><div class="label">ピーク</div><div class="value">${s.peak_count}</div><div class="sub">${fmt(s.peak_time)}</div></div>`;
    const el = document.getElementById('log');
    const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 60;
    el.textContent = lines.join('\\n');
    if (atBottom) el.scrollTop = el.scrollHeight;
    document.getElementById('updated').textContent = '更新: ' + new Date().toLocaleTimeString('ja-JP');
  } catch(e) { console.error(e); }
  fetchHistory();
}

async function fetchHistory() {
  const hours = document.getElementById('histHours').value;
  try {
    histData = await fetch('/api/history?hours=' + hours).then(r => r.json());
  } catch(e) { histData = []; }
  renderChart();
}

function renderChart() {
  const svg = document.getElementById('chart');
  const W = svg.getBoundingClientRect().width || 600;
  const H = 160;
  const pL = 38, pR = 12, pT = 8, pB = 26;
  const cW = W - pL - pR, cH = H - pT - pB;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

  if (histData.length < 2) {
    svg.innerHTML = `<text x="${W/2}" y="${H/2}" fill="#444" font-size="12" text-anchor="middle" dominant-baseline="middle">データが不足しています</text>`;
    return;
  }

  const ts = histData.map(d => new Date(d.t).getTime());
  const tMin = ts[0], tMax = ts[ts.length - 1];
  const cMax = Math.max(...histData.map(d => d.c), 1);
  const xS = t => pL + (t - tMin) / (tMax - tMin) * cW;
  const yS = v => pT + cH * (1 - v / cMax);
  const pts = histData.map((d, i) => [xS(ts[i]), yS(d.c)]);

  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join('');
  const area = line + `L${pts[pts.length-1][0].toFixed(1)},${(pT+cH).toFixed(1)}L${pts[0][0].toFixed(1)},${(pT+cH).toFixed(1)}Z`;

  let yGrid = '', yLbls = '';
  for (let i = 0; i <= 4; i++) {
    const v = Math.round(cMax * i / 4);
    const y = yS(v).toFixed(1);
    yGrid += `<line x1="${pL}" x2="${W-pR}" y1="${y}" y2="${y}" stroke="#2a2a2a" stroke-width="1"/>`;
    yLbls += `<text x="${pL-4}" y="${y}" fill="#555" font-size="10" text-anchor="end" dominant-baseline="middle">${v}</text>`;
  }

  let xLbls = '';
  for (let i = 0; i <= 4; i++) {
    const t = tMin + (tMax - tMin) * i / 4;
    const x = (pL + cW * i / 4).toFixed(1);
    const lbl = new Date(t).toLocaleTimeString('ja-JP', {hour:'2-digit', minute:'2-digit'});
    xLbls += `<text x="${x}" y="${H-pB+16}" fill="#555" font-size="10" text-anchor="middle">${lbl}</text>`;
  }

  svg.innerHTML =
    yGrid +
    `<path d="${area}" fill="#7cf" opacity="0.08"/>` +
    `<path d="${line}" fill="none" stroke="#7cf" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>` +
    yLbls + xLbls +
    `<line x1="${pL}" x2="${pL}" y1="${pT}" y2="${pT+cH}" stroke="#333"/>` +
    `<line x1="${pL}" x2="${W-pR}" y1="${pT+cH}" y2="${pT+cH}" stroke="#333"/>` +
    `<line id="cur" x1="0" x2="0" y1="${pT}" y2="${pT+cH}" stroke="#555" stroke-width="1" stroke-dasharray="3,3" display="none"/>` +
    `<circle id="dot" r="3" fill="#7cf" display="none"/>` +
    `<rect id="ov" x="${pL}" y="${pT}" width="${cW}" height="${cH}" fill="transparent" style="cursor:crosshair"/>`;

  const ov = svg.querySelector('#ov');
  const cur = svg.querySelector('#cur');
  const dot = svg.querySelector('#dot');
  const tip = document.getElementById('tip');

  ov.addEventListener('mousemove', e => {
    const r = svg.getBoundingClientRect();
    const mx = (e.clientX - r.left) * W / r.width;
    const t = tMin + (mx - pL) / cW * (tMax - tMin);
    let ni = 0, md = Infinity;
    ts.forEach((pt, i) => { const d = Math.abs(pt - t); if (d < md) { md = d; ni = i; } });
    const p = pts[ni];
    cur.setAttribute('x1', p[0]); cur.setAttribute('x2', p[0]); cur.removeAttribute('display');
    dot.setAttribute('cx', p[0]); dot.setAttribute('cy', p[1]); dot.removeAttribute('display');
    tip.style.display = 'block';
    tip.textContent = new Date(histData[ni].t).toLocaleTimeString('ja-JP') + '  ' + histData[ni].c + '台';
    tip.style.left = (e.clientX - r.left + 12) + 'px';
    tip.style.top = (e.clientY - r.top - 28) + 'px';
  });
  ov.addEventListener('mouseleave', () => {
    cur.setAttribute('display', 'none'); dot.setAttribute('display', 'none'); tip.style.display = 'none';
  });
}

function setTimer() {
  if (timer) clearInterval(timer);
  const v = parseInt(document.getElementById('interval').value);
  if (v > 0) timer = setInterval(fetchAll, v * 1000);
}

document.getElementById('interval').addEventListener('change', setTimer);

async function doReset() {
  if (!confirm('計測開始日時をリセットします。\\nDB内のデータは保持されます。よろしいですか？')) return;
  await fetch('/api/reset', {method: 'POST'});
  await fetchAll();
}

fetchAll();
setTimer();
</script>
</body>
</html>"""


class WebLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        LOG_BUFFER.append(self.format(record))


async def run_server(conn, port: int = 8080) -> None:
    app = web.Application()
    app["db_conn"] = conn
    app.router.add_get("/", _index)
    app.router.add_get("/api/stats", _stats)
    app.router.add_get("/api/logs", _logs)
    app.router.add_get("/api/history", _history)
    app.router.add_post("/api/reset", _reset)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.getLogger(__name__).info("Web UI ready: http://raspberrypi.local:%d/", port)
    await asyncio.Event().wait()


async def _index(request: web.Request) -> web.Response:
    return web.Response(text=_HTML, content_type="text/html")


async def _stats(request: web.Request) -> web.Response:
    return web.json_response(db.get_stats(request.app["db_conn"]))


async def _logs(request: web.Request) -> web.Response:
    return web.json_response(list(LOG_BUFFER))


async def _history(request: web.Request) -> web.Response:
    hours = int(request.rel_url.query.get("hours", 3))
    return web.json_response(db.get_scan_history(request.app["db_conn"], hours))


async def _reset(request: web.Request) -> web.Response:
    db.reset_session(request.app["db_conn"])
    return web.json_response({"ok": True})
