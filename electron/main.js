/**
 * main.js — Electron main process
 *
 * Trách nhiệm:
 *  1. Spawn Python FastAPI backend (backend.exe hoặc uvicorn khi dev)
 *  2. Tạo BrowserWindow hiển thị UI
 *  3. Quản lý auto-update (electron-updater)
 *  4. Xử lý tắt app → kill backend
 */

const { app, BrowserWindow, shell, ipcMain, dialog } = require("electron");
const { autoUpdater } = require("electron-updater");
const path   = require("path");
const { spawn, execFile } = require("child_process");
const http   = require("http");
const fs     = require("fs");

// ── Config ────────────────────────────────────────────────────────────────────
const API_PORT    = 8765;
const API_HOST    = "127.0.0.1";
const API_URL     = `http://${API_HOST}:${API_PORT}`;
const IS_DEV      = process.argv.includes("--dev") || !app.isPackaged;
const IS_WIN      = process.platform === "win32";

let mainWindow  = null;
let backendProc = null;

// ══════════════════════════════════════════════════════════════════════════════
// Backend: spawn Python process
// ══════════════════════════════════════════════════════════════════════════════

function getBackendPath() {
  if (IS_DEV) {
    // Dev: chạy trực tiếp bằng Python
    return null;
  }
  // Production: dùng backend.exe đã đóng gói
  const exeName = IS_WIN ? "backend.exe" : "backend";
  return path.join(process.resourcesPath, "backend", exeName);
}

function spawnBackend() {
  const backendExe = getBackendPath();

  if (IS_DEV) {
    // Dev mode: giả định Python server đang chạy riêng
    // Hoặc spawn uvicorn
    const backendDir = path.join(__dirname, "..", "backend");
    console.log("[Main] DEV mode — spawning uvicorn from:", backendDir);
    backendProc = spawn("python", ["-m", "uvicorn", "api:app",
      "--host", API_HOST, "--port", String(API_PORT), "--reload"], {
      cwd: backendDir,
      stdio: ["ignore", "pipe", "pipe"],
      detached: false,
    });
  } else {
    // Production: chạy backend.exe
    console.log("[Main] PROD mode — running:", backendExe);
    backendProc = execFile(backendExe, {
      cwd: path.dirname(backendExe),
      windowsHide: true,    // ← ẩn terminal window
    });
  }

  if (backendProc) {
    backendProc.stdout?.on("data", d => console.log("[Backend]", d.toString().trim()));
    backendProc.stderr?.on("data", d => console.error("[Backend ERR]", d.toString().trim()));
    backendProc.on("exit", code => console.log("[Backend] exited:", code));
  }
}

function killBackend() {
  if (!backendProc) return;
  try {
    if (IS_WIN) {
      spawn("taskkill", ["/F", "/T", "/PID", String(backendProc.pid)]);
    } else {
      backendProc.kill("SIGTERM");
    }
  } catch (e) {
    console.error("Kill backend error:", e);
  }
  backendProc = null;
}

// ── Đợi API sẵn sàng trước khi mở window ────────────────────────────────────
function waitForApi(maxRetries = 30, interval = 500) {
  return new Promise((resolve, reject) => {
    let tries = 0;
    const check = () => {
      http.get(`${API_URL}/status`, res => {
        if (res.statusCode === 200) resolve();
        else retry();
      }).on("error", retry);
    };
    const retry = () => {
      if (++tries >= maxRetries) return reject(new Error("Backend timeout"));
      setTimeout(check, interval);
    };
    check();
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// BrowserWindow
// ══════════════════════════════════════════════════════════════════════════════

function createWindow() {
  mainWindow = new BrowserWindow({
    width:  1280,
    height: 860,
    minWidth:  1040,
    minHeight: 720,
    title: "TikTok Auto Uploader",
    backgroundColor: "#05060b",     // khớp với màu app
    frame: false,                   // custom titlebar (macOS-style)
    titleBarStyle: "hidden",
    trafficLightPosition: { x: 16, y: 20 },  // macOS traffic lights
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
    icon: path.join(__dirname, "assets", IS_WIN ? "icon.ico" : "icon.png"),
    show: false,    // ẩn cho đến khi ready-to-show
  });

  // Load UI
  mainWindow.loadFile(path.join(__dirname, "src", "index.html"));

  // Hiện window mượt mà
  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    if (IS_DEV) mainWindow.webContents.openDevTools({ mode: "detach" });
  });

  // Mở link ngoài bằng browser mặc định
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.on("closed", () => { mainWindow = null; });
}

// ══════════════════════════════════════════════════════════════════════════════
// IPC — frontend gọi qua preload
// ══════════════════════════════════════════════════════════════════════════════

// Window controls (vì frame=false)
ipcMain.on("window:minimize", () => mainWindow?.minimize());
ipcMain.on("window:maximize", () => {
  if (mainWindow?.isMaximized()) mainWindow.unmaximize();
  else mainWindow?.maximize();
});
ipcMain.on("window:close", () => mainWindow?.close());

// Browse folder dialog
ipcMain.handle("dialog:openFolder", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory"],
  });
  return result.canceled ? null : result.filePaths[0];
});

// Browse file dialog
ipcMain.handle("dialog:openFile", async (_, filters) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openFile"],
    filters: filters || [{ name: "Video", extensions: ["mp4", "mov", "avi", "mkv", "webm"] }],
  });
  return result.canceled ? null : result.filePaths[0];
});

// App version
ipcMain.handle("app:getVersion", () => app.getVersion());

// ══════════════════════════════════════════════════════════════════════════════
// Auto-updater (production only)
// ══════════════════════════════════════════════════════════════════════════════

function setupAutoUpdater() {
  if (IS_DEV) return;

  autoUpdater.autoDownload = false;

  autoUpdater.on("update-available", info => {
    mainWindow?.webContents.send("update:available", info);
  });

  autoUpdater.on("download-progress", progress => {
    mainWindow?.webContents.send("update:progress", progress);
  });

  autoUpdater.on("update-downloaded", () => {
    mainWindow?.webContents.send("update:ready");
  });

  ipcMain.on("update:download", () => autoUpdater.downloadUpdate());
  ipcMain.on("update:install",  () => autoUpdater.quitAndInstall());

  // Check sau 3 giây
  setTimeout(() => autoUpdater.checkForUpdates(), 3000);
}

// ══════════════════════════════════════════════════════════════════════════════
// App lifecycle
// ══════════════════════════════════════════════════════════════════════════════

app.whenReady().then(async () => {
  spawnBackend();

  // Tạo splash / loading state
  createWindow();

  // Đợi backend sẵn sàng
  try {
    await waitForApi();
    console.log("[Main] Backend ready ✅");
    // Báo cho renderer biết backend sẵn sàng
    mainWindow?.webContents.send("backend:ready");
  } catch (err) {
    console.error("[Main] Backend failed to start:", err);
    dialog.showErrorBox(
      "Lỗi khởi động",
      "Không thể kết nối backend. Vui lòng khởi động lại app."
    );
  }

  setupAutoUpdater();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  killBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  killBackend();
});

// Xuất API_URL để preload dùng
global.API_URL = API_URL;
