import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
const dir = path.dirname(fileURLToPath(import.meta.url));
const cli = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
const disabledPath = path.join(dir, '.theme-disabled');
// Stop and emergency disable work before reading JSON, CSS or images.
if (cli && process.argv.includes('--stop')) { fs.writeFileSync(path.join(dir, '.theme-runtime.stop'), 'stop'); process.exit(0); }
if (cli && fs.existsSync(disabledPath)) process.exit(0);
const configPath = path.join(dir, 'config.json');
const config = fs.existsSync(configPath) ? JSON.parse(fs.readFileSync(configPath, 'utf8').replace(/^\uFEFF/, '')) : {};
const port = Number(process.env.SUNFADED_CDP_PORT || config.port || 9228);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw Error('Invalid local port');
let css = fs.readFileSync(path.join(dir, 'SunFaded.css'), 'utf8');
const sidebarPath = path.join(dir, 'Sidebar.css');
const palettePath = path.join(dir, 'SidebarPalette.svg');
const paletteSvg = config.sidebarEnabled !== false && fs.existsSync(palettePath) ? fs.readFileSync(palettePath, 'utf8') : '';
if (config.sidebarEnabled !== false && fs.existsSync(sidebarPath)) {
  css += '\n' + fs.readFileSync(sidebarPath, 'utf8');
}
const opacity = Number(config.backgroundOpacity ?? .15);
if (!Number.isFinite(opacity) || opacity < 0 || opacity > 1) throw Error('Invalid backgroundOpacity');
css += '\nhtml { --sf-background-opacity: '+opacity+' !important; }\n';
const sidebarOpacity = Number(config.sidebarOpacity ?? .30);
if (!Number.isFinite(sidebarOpacity) || sidebarOpacity < 0 || sidebarOpacity > 1) throw Error('Invalid sidebarOpacity');
css += '\nhtml { --sf-sidebar-opacity: '+sidebarOpacity+' !important; }\n';
const revision = createHash('sha256').update(css + paletteSvg).digest('hex');
const styleId = 'codex-sunfaded-local-theme';
export const healthy = `(() => {
  const style = document.getElementById(${JSON.stringify(styleId)});
  if (style?.dataset.sunfadedRevision !== ${JSON.stringify(revision)} || style.textContent.length !== ${css.length}) return false;
  return ${paletteSvg ? `document.getElementById('codex-sidebar-palette-filter')?.dataset.sunfadedRevision === ${JSON.stringify(revision)} && !!document.getElementById('codex-sidebar-bg-match')` : 'true'};
})()`;
export const inject = `(() => {
  if (!document.querySelector('.app-shell, .app-shell-main, main.main-surface, main.bg-surface, .app-shell-left-panel')) return false;
  const oldPalette = document.getElementById('codex-sidebar-palette-filter');
  if (${JSON.stringify(paletteSvg)} && (oldPalette?.dataset.sunfadedRevision !== ${JSON.stringify(revision)} || !document.getElementById('codex-sidebar-bg-match'))) {
    oldPalette?.remove();
    const svg = new DOMParser().parseFromString(${JSON.stringify(paletteSvg)}, 'image/svg+xml').documentElement;
    svg.dataset.sunfadedRevision = ${JSON.stringify(revision)};
    document.body.appendChild(document.importNode(svg, true));
  }
  let s = document.getElementById(${JSON.stringify(styleId)});
  if (!s) { s = document.createElement('style'); s.id = ${JSON.stringify(styleId)}; document.head.appendChild(s); }
  s.textContent = ${JSON.stringify(css)};
  s.dataset.sunfadedRevision = ${JSON.stringify(revision)};
  return true;
})()`;
export const remove = `(() => { document.getElementById(${JSON.stringify(styleId)})?.remove(); document.getElementById('codex-sidebar-palette-filter')?.remove(); })()`;
export const isCodexPage = t => t.type === 'page' && /^app:\/\//i.test(t.url || '');
class Connection {
  constructor(url) {
    this.ws = new WebSocket(url); this.pending = new Map(); this.seq = 0;
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, {once:true});
      this.ws.addEventListener('error', () => reject(Error('CDP connection failed')), {once:true});
    });
    this.ws.addEventListener('message', e => {
      const m = JSON.parse(e.data); const p = this.pending.get(m.id);
      if (p) { this.pending.delete(m.id); clearTimeout(p.timer); m.error ? p.reject(Error(m.error.message)) : p.resolve(m.result); }
    });
    this.ws.addEventListener('close', () => {
      for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(Error('CDP closed')); }
      this.pending.clear();
    });
  }
  async call(method, params={}) {
    await this.ready;
    return new Promise((resolve, reject) => {
      const id = ++this.seq;
      const timer = setTimeout(() => { this.pending.delete(id); reject(Error('CDP timeout')); }, 8000);
      this.pending.set(id, {resolve,reject,timer});
      this.ws.send(JSON.stringify({id, method, params}));
    });
  }
  close() { this.ws.close(); }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const lock = path.join(dir, '.theme-runtime.lock');
  const stop = path.join(dir, '.theme-runtime.stop');
  const status = path.join(dir, 'theme-status.json');
  if (process.argv.includes('--stop')) { fs.writeFileSync(stop, 'stop'); return; }
  if (config.enabled === false) return;
  const { acquireInstance } = await import('./instance-lock.mjs');
  const release = await acquireInstance(dir);
  if (!release) { console.log('Theme companion already running.'); return; }
  try { fs.writeFileSync(lock, String(process.pid)); }
  catch (error) { await release(); throw error; }
  const clients = new Map(); let quitting = false;
  process.on('SIGINT', () => { quitting=true; }); process.on('SIGTERM', () => { quitting=true; });
  const writeStatus = data => fs.writeFileSync(status, JSON.stringify({time:new Date().toISOString(),pid:process.pid,...data},null,2));
  try {
    if (fs.existsSync(stop)) fs.unlinkSync(stop);
    while (!quitting && !fs.existsSync(stop) && !fs.existsSync(disabledPath)) {
      try {
        const res = await fetch(`http://127.0.0.1:${port}/json/list`, {signal:AbortSignal.timeout(2500)});
        if (!res.ok) throw Error('Debug endpoint unavailable');
        const targets = (await res.json()).filter(isCodexPage);
        const active = new Set(targets.map(t=>t.id));
        for (const [id,c] of clients) if (!active.has(id)) { c.close(); clients.delete(id); }
        let applied=0;
        for (const t of targets) {
          let c = clients.get(t.id);
          try {
            if (!c) {
              const url = new URL(t.webSocketDebuggerUrl);
              if (!['127.0.0.1','localhost'].includes(url.hostname)) throw Error('Nonlocal endpoint rejected');
              c = new Connection(url.href); clients.set(t.id,c);
            }
            let r = await c.call('Runtime.evaluate', {expression:healthy,returnByValue:true});
            if (r.result?.value !== true) r = await c.call('Runtime.evaluate', {expression:inject,returnByValue:true});
            if (r.exceptionDetails) throw Error('Theme evaluation failed');
            if (r.result?.value === true) applied++;
          } catch { c?.close(); clients.delete(t.id); }
        }
        writeStatus({state:applied?'applied':'waiting_for_codex_page',applied_windows:applied,port,pid:process.pid,revision,sidebar_enabled:config.sidebarEnabled !== false && fs.existsSync(sidebarPath)});
      } catch { writeStatus({state:'waiting_for_debug_port',port,recovery:'Fully exit Codex and reopen with its themed desktop shortcut, or run wallpaper recovery. The companion will reconnect automatically.'}); }
      await sleep(2500);
    }
  } finally {
    for (const c of clients.values()) {
      try { await c.call('Runtime.evaluate',{expression:remove}); } catch {}
      c.close();
    }
    try {
      writeStatus({state:'stopped',port});
      if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === String(process.pid)) fs.unlinkSync(lock);
      if (fs.existsSync(stop)) fs.unlinkSync(stop);
    } finally { await release(); }
  }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
