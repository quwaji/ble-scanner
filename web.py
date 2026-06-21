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
  .controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
  select, button { background: #2a2a2a; color: #eee; border: 1px solid #555; border-radius: 4px; padding: 5px 12px; cursor: pointer; font-family: monospace; }
  button:hover { background: #3a3a3a; }
  button.reset { color: #f88; border-color: #833; }
  button.reset:hover { background: #311; }
  .updated { font-size: 0.7em; color: #555; }
  #log { background: #000; border: 1px solid #2a2a2a; border-radius: 6px; padding: 12px; height: 60vh; overflow-y: auto; font-size: 0.76em; line-height: 1.6; white-space: pre-wrap; word-break: break-all; }
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
<div id="log"></div>
<script>
let timer = null;

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
    app.router.add_post("/api/reset", _reset)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.getLogger(__name__).info("Web UI ready: http://ugopi2026.local:%d/", port)
    await asyncio.Event().wait()


async def _index(request: web.Request) -> web.Response:
    return web.Response(text=_HTML, content_type="text/html")


async def _stats(request: web.Request) -> web.Response:
    return web.json_response(db.get_stats(request.app["db_conn"]))


async def _logs(request: web.Request) -> web.Response:
    return web.json_response(list(LOG_BUFFER))


async def _reset(request: web.Request) -> web.Response:
    db.reset_session(request.app["db_conn"])
    return web.json_response({"ok": True})
