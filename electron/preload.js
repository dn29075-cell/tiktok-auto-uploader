/**
 * preload.js — Security bridge giữa Electron main và renderer.
 *
 * contextIsolation=true nên renderer KHÔNG có quyền truy cập Node.js trực tiếp.
 * preload expose API an toàn qua contextBridge.
 */

const { contextBridge, ipcRenderer } = require("electron");

const API_PORT = 8765;
const API_BASE = `http://127.0.0.1:${API_PORT}`;
const WS_BASE  = `ws://127.0.0.1:${API_PORT}`;

// ── Wrapper gọi API ───────────────────────────────────────────────────────────
async function apiCall(method, path, body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== null) opts.body = JSON.stringify(body);
  const res  = await fetch(`${API_BASE}${path}`, opts);
  const json = await res.json();
  if (!res.ok) throw new Error(json.detail || JSON.stringify(json));
  return json;
}

// ══════════════════════════════════════════════════════════════════════════════
// Expose to renderer via window.api
// ══════════════════════════════════════════════════════════════════════════════
contextBridge.exposeInMainWorld("api", {

  // ── Base URL (cho fetch thủ công) ─────────────────────────────────────────
  baseUrl: API_BASE,
  wsUrl:   WS_BASE,

  // ── Status / Config ───────────────────────────────────────────────────────
  getStatus:  ()       => apiCall("GET",  "/status"),
  getConfig:  ()       => apiCall("GET",  "/config"),
  setConfig:  (data)   => apiCall("POST", "/config", { data }),

  // ── Accounts ──────────────────────────────────────────────────────────────
  getAccounts:  ()      => apiCall("GET",    "/accounts"),
  addAccount:   (name)  => apiCall("POST",   "/accounts", { name }),
  deleteAccount:(idx)   => apiCall("DELETE", `/accounts/${idx}`),
  setActive:    (idx)   => apiCall("POST",   `/accounts/${idx}/active`),
  loginAccount: (idx)   => apiCall("POST",   `/accounts/${idx}/login`),

  // ── Scheduler ─────────────────────────────────────────────────────────────
  schedulerStatus:  ()     => apiCall("GET",  "/scheduler/status"),
  schedulerStart:   ()     => apiCall("POST", "/scheduler/start"),
  schedulerStop:    ()     => apiCall("POST", "/scheduler/stop"),
  schedulerTrigger: ()     => apiCall("POST", "/scheduler/trigger"),
  setScheduleTime:  (time) => apiCall("POST", "/scheduler/time",   { time }),
  setTargetDate:    (date) => apiCall("POST", "/scheduler/target", { date }),

  // ── Videos ────────────────────────────────────────────────────────────────
  scanVideos:   (base_path, date) => apiCall("POST", "/videos/scan",       { base_path, date }),
  analyzeVideo: (video_path)      => apiCall("POST", "/videos/analyze",    { video_path }),
  aiCaption:    (video_path, tpl) => apiCall("POST", "/videos/caption-ai", { video_path, tpl_idx: tpl }),
  uploadVideo:  (video_path, cap) => apiCall("POST", "/videos/upload",     { video_path, caption: cap }),

  // ── Retry ─────────────────────────────────────────────────────────────────
  getRetry:     ()    => apiCall("GET",    "/retry"),
  runRetry:     ()    => apiCall("POST",   "/retry/run"),
  deleteRetry:  (idx) => apiCall("DELETE", `/retry/${idx}`),
  clearRetry:   ()    => apiCall("DELETE", "/retry"),

  // ── IPC — Window controls ─────────────────────────────────────────────────
  minimize: () => ipcRenderer.send("window:minimize"),
  maximize: () => ipcRenderer.send("window:maximize"),
  close:    () => ipcRenderer.send("window:close"),

  // ── IPC — Dialogs ─────────────────────────────────────────────────────────
  openFolder: ()        => ipcRenderer.invoke("dialog:openFolder"),
  openFile:   (filters) => ipcRenderer.invoke("dialog:openFile", filters),

  // ── IPC — Events từ main ─────────────────────────────────────────────────
  onBackendReady:  (cb) => ipcRenderer.on("backend:ready",   () => cb()),
  onUpdateAvail:   (cb) => ipcRenderer.on("update:available",(_, i) => cb(i)),
  onUpdateProgress:(cb) => ipcRenderer.on("update:progress", (_, p) => cb(p)),
  onUpdateReady:   (cb) => ipcRenderer.on("update:ready",    () => cb()),
  downloadUpdate:  ()   => ipcRenderer.send("update:download"),
  installUpdate:   ()   => ipcRenderer.send("update:install"),

  // ── WebSocket logs ────────────────────────────────────────────────────────
  connectLogs: (onMessage) => {
    const ws = new WebSocket(`${WS_BASE}/ws/logs`);
    ws.onmessage = e => {
      try { onMessage(JSON.parse(e.data)); }
      catch (_) {}
    };
    ws.onopen  = () => {
      const ping = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        else clearInterval(ping);
      }, 30000);
    };
    ws.onerror = e => console.error("[WS] error", e);
    return ws;  // caller có thể .close() khi cần
  },
});
