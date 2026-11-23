const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('mailbot', {
  getDigest: () => ipcRenderer.invoke('digest:get'),
  onDigest: (cb) => ipcRenderer.on('digest:update', (_e, data) => cb(data)),
  onCollapsed: (cb) => ipcRenderer.on('widget:collapsed', (_e, v) => cb(v)),
  toggleCollapse: () => ipcRenderer.invoke('widget:toggle-collapse'),
  resizeHeight: (h) => ipcRenderer.invoke('widget:resize-height', h),
  openExternal: (url) => ipcRenderer.invoke('app:open-external', url),
  quit: () => ipcRenderer.invoke('app:quit'),
  hide: () => ipcRenderer.invoke('app:minimize-to-tray'),
  notifyTest: () => ipcRenderer.invoke('app:notify-test'),
  paths: () => ipcRenderer.invoke('app:paths')
});
