// HKU 邮件助手 —— 桌面悬浮小窗主进程
const { app, BrowserWindow, ipcMain, shell, Notification, Tray, Menu, nativeImage, screen } = require('electron');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const DATA_DIR = path.join(ROOT, 'data');
const DIGEST = path.join(DATA_DIR, 'digest.json');
const STATE = path.join(DATA_DIR, 'widget_state.json');
const ICON = path.join(__dirname, 'assets', 'icon.png');

let win = null;
let tray = null;
let lastRunId = null;
let collapsed = false;
const FULL_SIZE = { width: 400, height: 620 };
const COLLAPSED_SIZE = { width: 190, height: 52 };

// 小窗内容简单，禁用硬件加速更稳（部分显卡驱动会导致 GPU 进程反复崩溃）
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-gpu-compositing');
// 某些受限环境/安全软件下渲染进程沙箱初始化会失败，这里放开沙箱保证可用
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-dev-shm-usage');

function readJson(p, fallback) {
  try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return fallback; }
}
function writeJson(p, obj) {
  try { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, JSON.stringify(obj, null, 2)); } catch {}
}

function loadState() {
  return readJson(STATE, { x: null, y: null, collapsed: false });
}
function saveBounds() {
  if (!win || win.isDestroyed()) return;
  const b = win.getBounds();
  const s = loadState();
  writeJson(STATE, { ...s, x: b.x, y: b.y });
}

function buildIcon() {
  try { if (fs.existsSync(ICON)) return nativeImage.createFromPath(ICON); } catch {}
  return nativeImage.createEmpty();
}

function createWindow() {
  const state = loadState();
  const { workArea } = screen.getPrimaryDisplay();
  const margin = 24;

  const x = (typeof state.x === 'number') ? state.x : Math.round(workArea.x + workArea.width - FULL_SIZE.width - margin);
  const y = (typeof state.y === 'number') ? state.y : Math.round(workArea.y + workArea.height - FULL_SIZE.height - margin);

  win = new BrowserWindow({
    width: FULL_SIZE.width,
    height: FULL_SIZE.height,
    x, y,
    frame: false,
    transparent: true,
    resizable: false,
    maximizable: false,
    minimizable: false,
    fullscreenable: false,
    skipTaskbar: false,
    hasShadow: false,
    alwaysOnTop: true,
    show: false,
    icon: buildIcon(),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false
    }
  });

  win.setAlwaysOnTop(true, 'screen-saver');
  win.webContents.on('did-finish-load', () => console.log('[debug] did-finish-load'));
  win.webContents.on('did-fail-load', (_e, code, desc) => console.error('[debug] did-fail-load', code, desc));
  win.webContents.on('render-process-gone', (_e, details) => console.error('[debug] renderer gone:', JSON.stringify(details)));
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  // 调试用：--debug-shot 截一张渲染图后退出（--debug-scroll N 先滚动 N 像素）
  if (process.argv.includes('--debug-shot')) {
    const scrollArg = process.argv.find((a) => a.startsWith('--debug-scroll='));
    const scrollPx = scrollArg ? parseInt(scrollArg.split('=')[1], 10) || 0 : 0;
    win.webContents.once('did-finish-load', () => {
      setTimeout(async () => {
        try {
          if (scrollPx > 0) {
            await win.webContents.executeJavaScript(
              `var el=document.querySelector('.body')||document.scrollingElement;el.scrollTop=${scrollPx};` +
              `document.querySelectorAll('.scroll,[class*=scroll]').forEach(e=>e.scrollTop=${scrollPx});`);
          }
          await new Promise((r) => setTimeout(r, 250));
          const img = await win.webContents.capturePage();
          fs.mkdirSync(DATA_DIR, { recursive: true });
          fs.writeFileSync(path.join(DATA_DIR, 'widget_preview.png'), img.toPNG());
          console.log('[debug] screenshot saved');
        } catch (e) { console.error('[debug] capture failed:', e); }
        app.quit();
      }, 1500);
    });
  }

  win.once('ready-to-show', () => {
    console.log('[debug] ready-to-show fired, showing window');
    win.show();
    if (state.collapsed) setCollapsed(true);
    pushDigest(true);
  });

  win.on('moved', saveBounds);

  // 外部链接用系统浏览器打开
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

function pushDigest(isInitial = false) {
  const digest = readJson(DIGEST, null);
  if (!win || win.isDestroyed()) return;
  win.webContents.send('digest:update', digest);

  if (!digest) return;
  const runId = digest.run_id || digest.generated_at;
  const isNew = lastRunId !== null && runId !== lastRunId;
  const fresh = digest.generated_at && (Date.now() - new Date(digest.generated_at).getTime() < 15 * 60 * 1000);

  if (isNew && fresh && !isInitial) {
    notify(digest);
  }
  lastRunId = runId;
}

function notify(digest) {
  try {
    if (!Notification.isSupported()) return;
    const stats = digest.stats || {};
    const title = `HKU 邮件助手 · ${digest.slot || ''}汇总`.trim();
    const body = `${digest.headline_cn || ''}`.slice(0, 220);
    const n = new Notification({
      title,
      body,
      icon: buildIcon(),
      silent: false,
      urgency: (stats.urgent_key > 0) ? 'critical' : 'normal'
    });
    n.on('click', () => {
      if (win && !win.isDestroyed()) { win.show(); win.setAlwaysOnTop(true, 'screen-saver'); }
      shell.openExternal((digest.key_emails && digest.key_emails[0] && digest.key_emails[0].web_link) || 'https://outlook.office365.com/mail/inbox');
    });
    n.show();
  } catch (e) { /* 通知失败不影响主流程 */ }
}

function setCollapsed(next) {
  if (!win || win.isDestroyed()) return;
  collapsed = !!next;
  const cur = win.getBounds();
  if (collapsed) {
    win.setBounds({ x: cur.x + (FULL_SIZE.width - COLLAPSED_SIZE.width), y: cur.y, ...COLLAPSED_SIZE });
  } else {
    win.setBounds({ x: cur.x - (FULL_SIZE.width - COLLAPSED_SIZE.width), y: cur.y, ...FULL_SIZE });
  }
  const s = loadState();
  writeJson(STATE, { ...s, collapsed });
  win.webContents.send('widget:collapsed', collapsed);
}

function createTray() {
  try {
    tray = new Tray(buildIcon());
    const menu = Menu.buildFromTemplate([
      { label: '显示 / 隐藏小窗', click: () => { if (!win) return; win.isVisible() ? win.hide() : win.show(); } },
      { label: '立即刷新数据', click: () => pushDigest() },
      { type: 'separator' },
      { label: '打开港大邮箱', click: () => shell.openExternal('https://outlook.office365.com/mail/inbox') },
      { label: '打开数据目录', click: () => shell.openPath(DATA_DIR) },
      { type: 'separator' },
      { label: '退出', click: () => { app.isQuitting = true; app.quit(); } }
    ]);
    tray.setToolTip('HKU 邮件助手');
    tray.setContextMenu(menu);
    tray.on('double-click', () => { if (win) win.show(); });
  } catch (e) { /* 托盘失败不致命 */ }
}

// ----------------------------- IPC -----------------------------
ipcMain.handle('digest:get', () => readJson(DIGEST, null));
ipcMain.handle('widget:toggle-collapse', () => { setCollapsed(!collapsed); return collapsed; });
ipcMain.handle('widget:resize-height', (_e, height) => {
  if (!win || win.isDestroyed() || collapsed) return;
  const cur = win.getBounds();
  const h = Math.max(160, Math.min(900, Math.round(height)));
  win.setBounds({ x: cur.x, y: cur.y + (cur.height - h), width: FULL_SIZE.width, height: h });
});
ipcMain.handle('app:open-external', (_e, url) => { if (url) shell.openExternal(url); });
ipcMain.handle('app:quit', () => { app.isQuitting = true; app.quit(); });
ipcMain.handle('app:minimize-to-tray', () => { if (win) win.hide(); });
ipcMain.handle('app:notify-test', () => {
  const d = readJson(DIGEST, null);
  if (d) notify(d);
});
ipcMain.handle('app:paths', () => ({ digest: DIGEST, dataDir: DATA_DIR, root: ROOT }));

// --------------------------- 生命周期 ---------------------------
const single = app.requestSingleInstanceLock();
if (!single) {
  app.quit();
} else {
  app.on('second-instance', () => { if (win) { win.show(); win.focus(); } });

  app.whenReady().then(() => {
    app.setAppUserModelId('hku.mail.widget');
    createWindow();
    createTray();

    // 每 15 秒检查一次 digest.json 是否有更新
    setInterval(() => pushDigest(), 15000);
    try { fs.watchFile(DIGEST, { interval: 5000 }, () => pushDigest()); } catch {}

    app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
  });

  app.on('window-all-closed', () => { if (app.isQuitting) app.quit(); });
}
