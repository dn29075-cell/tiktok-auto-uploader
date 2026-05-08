/**
 * app.js — Renderer process (chạy trong browser context của Electron)
 *
 * Tất cả giao tiếp với Python backend qua window.api (exposed từ preload.js)
 */

"use strict";

// ══════════════════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════════════════

const $ = id => document.getElementById(id);
const fmtDate = d => `${String(d.getDate()).padStart(2,"0")}/${String(d.getMonth()+1).padStart(2,"0")}/${d.getFullYear()}`;
const isoDate = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
const today   = () => new Date();

// ── Trạng thái local ─────────────────────────────────────────────────────────
let _targetDate   = new Date();
let _manualDate   = new Date();
let _ws           = null;
let _tickInterval = null;
let _schedStatus  = {};
let _tplIndex     = 0;        // caption template index (manual tab)
let _toastTimer   = null;

// ── Toast notifications ──────────────────────────────────────────────────────
function toast(msg, type = "ok", duration = 2500) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className   = `toast ${type} show`;
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove("show"), duration);
}

// ══════════════════════════════════════════════════════════════════════════════
// Khởi động
// ══════════════════════════════════════════════════════════════════════════════

window.addEventListener("DOMContentLoaded", () => {
  // Window controls
  $("btn-close").onclick    = () => api.close();
  $("btn-minimize").onclick = () => api.minimize();
  $("btn-maximize").onclick = () => api.maximize();

  // Đợi backend ready
  api.onBackendReady(() => {
    console.log("Backend ready — initializing UI");
    initApp();
  });

  // Nếu backend đã sẵn sàng trước khi event fire (reload)
  api.getStatus().then(() => initApp()).catch(() => {
    // Backend chưa sẵn sàng — đợi event
  });

  // Auto-update
  api.onUpdateAvail(info => {
    $("update-banner").classList.remove("hidden");
    $("btn-download-update").textContent = `Tải về v${info.version}`;
  });
  api.onUpdateProgress(p => {
    $("btn-download-update").textContent = `${Math.round(p.percent)}%...`;
  });
  api.onUpdateReady(() => {
    $("btn-download-update").style.display = "none";
    $("btn-install-update").style.display = "";
  });
  $("btn-download-update").onclick = () => api.downloadUpdate();
  $("btn-install-update").onclick  = () => api.installUpdate();
});

async function initApp() {
  if ($("app-layout").style.display !== "none") return;  // đã init

  $("loading-overlay").style.display = "none";
  $("app-layout").style.display = "flex";

  setupTabs();
  setupAutoTab();
  setupManualTab();
  setupAccountsTab();
  setupRetryTab();
  setupSettingsTab();
  setupLogs();
  await loadStatus();
  await loadAccounts();
  await loadRetry();
  await loadSettings();
  startTick();
}

// ══════════════════════════════════════════════════════════════════════════════
// TABS
// ══════════════════════════════════════════════════════════════════════════════

function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.add("hidden"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.remove("hidden");
      // Refresh khi chuyển tab
      if (btn.dataset.tab === "accounts") loadAccounts();
      if (btn.dataset.tab === "retry")    loadRetry();
      if (btn.dataset.tab === "settings") loadSettings();
    };
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// AUTO TAB
// ══════════════════════════════════════════════════════════════════════════════

function setupAutoTab() {
  // Target date
  $("inp-target-date").value = fmtDate(_targetDate);

  $("btn-date-prev").onclick = () => {
    _targetDate.setDate(_targetDate.getDate() - 1);
    $("inp-target-date").value = fmtDate(_targetDate);
  };
  $("btn-date-next").onclick = () => {
    _targetDate.setDate(_targetDate.getDate() + 1);
    $("inp-target-date").value = fmtDate(_targetDate);
  };
  $("btn-date-today").onclick = () => {
    _targetDate = today();
    $("inp-target-date").value = fmtDate(_targetDate);
  };
  $("btn-date-apply").onclick = async () => {
    const parts = $("inp-target-date").value.split("/");
    if (parts.length === 3) {
      const d = new Date(+parts[2], +parts[1]-1, +parts[0]);
      await api.setTargetDate(isoDate(d));
      toast("✅ Đã áp dụng ngày mục tiêu", "ok");
      $("lbl-target-info").textContent = "✅";
      setTimeout(() => $("lbl-target-info").textContent = "", 3000);
      await loadStatus();
    }
  };

  // Scheduler controls
  $("btn-run-now").onclick = async () => {
    $("btn-run-now").classList.add("loading");
    try {
      await api.schedulerTrigger();
      toast("▶ Pipeline đang khởi chạy...", "ok", 3000);
    } catch(e) {
      toast("❌ " + e.message, "err");
    } finally {
      setTimeout(() => $("btn-run-now").classList.remove("loading"), 3000);
    }
  };

  $("btn-pause").onclick = async () => {
    try {
      if (_schedStatus.paused) {
        await api.schedulerStart();
        toast("▶ Scheduler đã tiếp tục", "ok");
      } else {
        await api.schedulerStop();
        toast("⏸ Scheduler đã tạm dừng", "warn");
      }
      await loadStatus();
    } catch(e) {
      toast("❌ " + e.message, "err");
    }
  };

  $("btn-save-time").onclick = async () => {
    const t = $("inp-stime").value.trim();
    if (/^\d{1,2}:\d{2}$/.test(t)) {
      try {
        await api.setScheduleTime(t);
        toast(`⏰ Đã đặt giờ: ${t}`, "ok");
        await loadStatus();
      } catch(e) {
        toast("❌ " + e.message, "err");
      }
    } else {
      toast("⚠️ Định dạng sai, dùng HH:MM", "warn");
    }
  };
}

// ══════════════════════════════════════════════════════════════════════════════
// STATUS LOADING + TICK
// ══════════════════════════════════════════════════════════════════════════════

async function loadStatus() {
  try {
    const data = await api.getStatus();
    _schedStatus = data.scheduler;
    updateSchedulerUI(data.scheduler);
    updateStatusBar(data.scheduler);
  } catch (e) {
    console.error("loadStatus error:", e);
  }
}

function updateSchedulerUI(s) {
  // Schedule time input
  if ($("inp-stime").value === "") $("inp-stime").value = s.schedule_time;

  // Pause button
  $("btn-pause").textContent = s.paused ? "▶ Tiếp tục" : "⏸ Tạm dừng";
  $("btn-pause").className   = "btn pill " + (s.paused ? "btn-green" : "btn-amber");

  // Run now button
  $("btn-run-now").disabled = s.pipeline_running;
  $("btn-run-now").textContent = s.pipeline_running ? "⏳ Đang chạy..." : "▶ Chạy ngay";

  // Last triggered
  if (s.last_triggered) {
    const dt = new Date(s.last_triggered);
    $("lbl-last-run").textContent = `Lần cuối: ${dt.toLocaleString("vi-VN")}`;
  }
}

function updateStatusBar(s) {
  let dotClass = "status-dot";
  let txt      = "";

  if (s.pipeline_running) {
    dotClass = "status-dot warning";   // amber — đang chạy pipeline
    txt = "🔄 Pipeline đang chạy...";
  } else if (s.paused) {
    dotClass = "status-dot offline";   // red — tạm dừng
    txt = "Đã tạm dừng";
  } else if (s.thread_alive) {
    dotClass = "status-dot";           // green — hoạt động bình thường
    txt = "Đang chạy 24/7";
  } else {
    dotClass = "status-dot offline";
    txt = "Scheduler tắt";
  }

  $("status-dot").className   = dotClass;
  $("status-text").textContent = txt;
}

function startTick() {
  if (_tickInterval) clearInterval(_tickInterval);
  _tickInterval = setInterval(tick, 1000);
  tick();
}

function tick() {
  if (!_schedStatus.next_run_dt) return;

  const now      = new Date();
  const nextRun  = new Date(_schedStatus.next_run_dt);
  const secs     = Math.max(0, Math.round((nextRun - now) / 1000));
  const h        = Math.floor(secs / 3600);
  const m        = Math.floor((secs % 3600) / 60);
  const s        = secs % 60;
  const txt      = `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;

  const el = $("countdown");
  el.textContent = txt;

  // Color + animation
  if (secs <= 0)        { el.className = "countdown urgent blink"; }
  else if (secs <= 60)  { el.className = "countdown urgent"; }
  else if (secs <= 300) { el.className = "countdown warn"; }
  else                  { el.className = "countdown"; }

  // Label
  $("lbl-next-run").textContent = secs > 0
    ? `còn ${txt} — ${nextRun.toLocaleTimeString("vi-VN")}`
    : "Đang chạy...";

  // Day progress
  const startOfDay = new Date(now); startOfDay.setHours(0,0,0,0);
  const pct = ((now - startOfDay) / 86400000) * 100;
  $("day-progress").style.width = pct + "%";
}

// ══════════════════════════════════════════════════════════════════════════════
// MANUAL TAB
// ══════════════════════════════════════════════════════════════════════════════

function setupManualTab() {
  $("inp-manual-date").value = isoDate(today());

  $("btn-browse-base").onclick = async () => {
    const p = await api.openFolder();
    if (p) $("inp-base-path").value = p;
  };

  const setManualDate = (offset) => {
    _manualDate = today();
    _manualDate.setDate(_manualDate.getDate() + offset);
    $("inp-manual-date").value = isoDate(_manualDate);
  };
  $("btn-manual-yesterday").onclick = () => setManualDate(-1);
  $("btn-manual-today").onclick     = () => setManualDate(0);
  $("btn-manual-tomorrow").onclick  = () => setManualDate(1);

  $("btn-scan").onclick         = scanVideos;
  $("btn-analyze-all").onclick  = analyzeAll;
  $("btn-upload-all").onclick   = uploadAll;
}

async function scanVideos() {
  const base = $("inp-base-path").value.trim();
  const date = $("inp-manual-date").value.trim();
  if (!base) { $("lbl-scan-status").textContent = "⚠️ Chưa chọn folder!"; return; }

  $("lbl-scan-status").textContent = "🔍 Đang quét...";
  $("video-list").innerHTML = "<div class='empty-state'>Đang quét...</div>";

  try {
    const res = await api.scanVideos(base, date);
    if (!res.videos.length) {
      $("video-list").innerHTML = `<div class='empty-state'>Không tìm thấy video trong ${res.folder || "folder"}</div>`;
      $("lbl-scan-status").textContent = "Không có video nào";
      return;
    }
    $("lbl-scan-status").textContent = `✅ Tìm thấy ${res.videos.length} video — ${res.folder}`;
    renderVideoList(res.videos);
  } catch (e) {
    $("lbl-scan-status").textContent = "❌ " + e.message;
  }
}

function renderVideoList(videos) {
  $("video-list").innerHTML = "";
  videos.forEach((vp, i) => {
    const name = vp.split(/[\\/]/).pop();
    const row  = document.createElement("div");
    row.className = "video-row";
    row.id = `vrow-${i}`;
    row.innerHTML = `
      <div class="row align-center">
        <span class="video-name flex-1">🎬 ${name}</span>
        <span class="video-status text-muted" id="vstatus-${i}">⏳</span>
      </div>
      <div class="video-inputs">
        <input class="input" id="vsong-${i}"   placeholder="🎵 Tên bài hát..."/>
        <input class="input" id="vcaption-${i}" placeholder="📝 Caption TikTok..."/>
      </div>
      <div class="video-actions">
        <button class="btn btn-ghost btn-sm" id="vbtn-analyze-${i}">🔍 Nhận dạng</button>
        <button class="btn btn-purple btn-sm" id="vbtn-ai-${i}">🤖 AI</button>
        <button class="btn btn-ghost btn-sm" id="vbtn-cycle-${i}">🔄</button>
        <button class="btn btn-blue  btn-sm" id="vbtn-upload-${i}" style="margin-left:auto">📤 Upload</button>
      </div>
    `;
    $("video-list").appendChild(row);

    // Bind events
    $(`vbtn-analyze-${i}`).onclick = () => analyzeVideo(vp, i);
    $(`vbtn-ai-${i}`).onclick      = () => aiCaption(vp, i);
    $(`vbtn-cycle-${i}`).onclick   = () => cycleCaption(i);
    $(`vbtn-upload-${i}`).onclick  = () => uploadVideo(vp, i);
  });
}

async function analyzeVideo(vp, idx) {
  $(`vstatus-${idx}`).textContent = "🔄 Nhận dạng...";
  $(`vstatus-${idx}`).className   = "video-status text-amber";
  $(`vbtn-analyze-${idx}`).disabled = true;
  try {
    const res = await api.analyzeVideo(vp);
    // Kết quả về qua WebSocket status event — lắng nghe ở log mgr
    $(`vstatus-${idx}`).textContent = "🎵 Đang phân tích...";
  } catch (e) {
    $(`vstatus-${idx}`).textContent = "❌ " + e.message;
    $(`vstatus-${idx}`).className   = "video-status text-red";
  } finally {
    $(`vbtn-analyze-${idx}`).disabled = false;
  }
}

async function aiCaption(vp, idx) {
  $(`vstatus-${idx}`).textContent = "🤖 AI đang viết...";
  $(`vbtn-ai-${idx}`).disabled = true;
  try {
    await api.aiCaption(vp, _tplIndex);
  } catch (e) {
    $(`vstatus-${idx}`).textContent = "❌ " + e.message;
  } finally {
    $(`vbtn-ai-${idx}`).disabled = false;
  }
}

function cycleCaption(idx) {
  _tplIndex++;
  const song    = $(`vsong-${idx}`).value;
  const tplList = [
    `🎵 ${song} 🌸 Nghe là nghiện luôn! #FYP #nhachay #xuhuong`,
    `🎶 ${song} ✨ Ai nghe cũng mê! #FYP #nhachay #xuhuong`,
    `🎵 ${song} 💫 Chill cùng giai điệu này! #FYP #nhacviet`,
    `🎤 ${song} 🔥 Hit quá đi! #FYP #nhachay #amnhac`,
    `🎵 ${song} 💕 Ngọt ngào quá! #FYP #nhacviet #nhachay`,
  ];
  $(`vcaption-${idx}`).value = tplList[_tplIndex % tplList.length];
}

async function uploadVideo(vp, idx) {
  const cap = $(`vcaption-${idx}`).value.trim();
  if (!cap) { $(`vstatus-${idx}`).textContent = "⚠️ Cần có caption"; return; }
  $(`vstatus-${idx}`).textContent  = "📤 Đang upload...";
  $(`vstatus-${idx}`).className    = "video-status";
  $(`vbtn-upload-${idx}`).disabled = true;
  try {
    await api.uploadVideo(vp, cap);
    $(`vstatus-${idx}`).textContent = "📤 Đang xử lý...";
  } catch (e) {
    $(`vstatus-${idx}`).textContent = "❌ " + e.message;
    $(`vstatus-${idx}`).className   = "video-status text-red";
    $(`vbtn-upload-${idx}`).disabled = false;
  }
}

async function analyzeAll() {
  const rows = $("video-list").querySelectorAll(".video-row");
  rows.forEach((_, i) => analyzeVideo(
    document.querySelector(`#vrow-${i}`)?.dataset?.vp || "", i
  ));
}

async function uploadAll() {
  const rows = $("video-list").querySelectorAll(".video-row");
  rows.forEach((row, i) => {
    const vp = row.dataset?.vp;
    if (vp) uploadVideo(vp, i);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// ACCOUNTS TAB
// ══════════════════════════════════════════════════════════════════════════════

function setupAccountsTab() {
  $("btn-add-acc").onclick = async () => {
    const name = $("inp-new-acc").value.trim();
    if (!name) { showAccMsg("❌ Nhập tên!", "red"); return; }
    try {
      await api.addAccount(name);
      $("inp-new-acc").value = "";
      showAccMsg("✅ Đã thêm: " + name, "green");
      await loadAccounts();
    } catch (e) {
      showAccMsg("❌ " + e.message, "red");
    }
  };
}

function showAccMsg(msg, color) {
  $("lbl-acc-msg").textContent = msg;
  $("lbl-acc-msg").className = `fs-12 px-16 pb-8 text-${color}`;
  setTimeout(() => $("lbl-acc-msg").textContent = "", 3000);
}

async function loadAccounts() {
  try {
    const accounts = await api.getAccounts();
    const container = $("accounts-list");
    container.innerHTML = "";

    if (!accounts.length) {
      container.innerHTML = "<div class='empty-state'>Chưa có tài khoản nào</div>";
      return;
    }

    accounts.forEach(acc => {
      const card = document.createElement("div");
      card.className = "acc-card" + (acc.is_active ? " active-acc" : "");

      const sess = acc.session;
      let sessHtml = "<span class='acc-session text-muted'>🍪 Chưa có session</span>";
      if (sess?.has_session) {
        const age   = Math.round(sess.age_days || 0);
        const color = age > 25 ? "amber" : "green";
        sessHtml = `<span class='acc-session text-${color}'>🍪 Session: ${sess.session_id || "?"} (${age} ngày)${age > 25 ? " ⚠️" : ""}</span>`;
      }

      card.innerHTML = `
        <div class="row align-center gap-8">
          <span class="acc-name">👤 ${acc.name}</span>
          ${acc.is_active ? "<span class='acc-badge'>✅ ACTIVE</span>" : ""}
        </div>
        <div class="acc-path">📂 ${acc.profile_dir}</div>
        ${sessHtml}
        <div class="acc-actions">
          ${!acc.is_active ? `<button class="btn btn-teal btn-sm" data-idx="${acc.index}" data-action="active">✅ Đặt làm active</button>` : ""}
          <button class="btn btn-purple btn-sm" data-idx="${acc.index}" data-action="login">🔐 Đăng nhập TikTok</button>
          <button class="btn btn-red btn-sm"    data-idx="${acc.index}" data-action="delete">🗑️ Xóa</button>
        </div>
      `;

      card.querySelectorAll("[data-action]").forEach(btn => {
        btn.onclick = async () => {
          const idx    = +btn.dataset.idx;
          const action = btn.dataset.action;
          if (action === "active") { await api.setActive(idx); await loadAccounts(); }
          if (action === "login")  { await api.loginAccount(idx); appendLog({level:"info", text:`🔐 Đang mở Chrome đăng nhập ${acc.name}...`, ts: new Date().toLocaleTimeString()}); }
          if (action === "delete") { if (confirm(`Xóa tài khoản "${acc.name}"?`)) { await api.deleteAccount(idx); await loadAccounts(); } }
        };
      });

      container.appendChild(card);
    });
  } catch (e) {
    console.error("loadAccounts error:", e);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// RETRY TAB
// ══════════════════════════════════════════════════════════════════════════════

function setupRetryTab() {
  $("btn-retry-all").onclick   = async () => { await api.runRetry(); await loadRetry(); };
  $("btn-retry-clear").onclick = async () => { if (confirm("Xóa toàn bộ retry queue?")) { await api.clearRetry(); await loadRetry(); } };
}

async function loadRetry() {
  try {
    const queue = await api.getRetry();
    $("lbl-retry-count").textContent = queue.length ? `${queue.length} video` : "";
    const container = $("retry-list");
    container.innerHTML = "";

    if (!queue.length) {
      container.innerHTML = "<div class='empty-state'>✅ Không có video lỗi nào</div>";
      return;
    }

    queue.forEach((item, i) => {
      const el = document.createElement("div");
      el.className = "retry-item";
      const name = item.video_path.split(/[\\/]/).pop();
      el.innerHTML = `
        <div class="retry-name">🎬 ${name}</div>
        <div class="retry-err">❌ ${item.error}</div>
        <div class="retry-actions">
          <button class="btn btn-blue btn-sm" data-idx="${i}" data-action="retry-one">🔄 Retry</button>
          <button class="btn btn-ghost btn-sm" data-idx="${i}" data-action="del-retry">🗑️ Xóa</button>
        </div>
      `;
      el.querySelectorAll("[data-action]").forEach(btn => {
        btn.onclick = async () => {
          const idx = +btn.dataset.idx;
          if (btn.dataset.action === "del-retry") {
            await api.deleteRetry(idx);
            await loadRetry();
          } else {
            await api.uploadVideo(item.video_path, item.caption);
          }
        };
      });
      container.appendChild(el);
    });
  } catch (e) {
    console.error("loadRetry error:", e);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// SETTINGS TAB
// ══════════════════════════════════════════════════════════════════════════════

function setupSettingsTab() {
  $("btn-cfg-browse").onclick = async () => {
    const p = await api.openFolder();
    if (p) $("cfg-video-path").value = p;
  };

  // ── Tạo thư mục tự động ───────────────────────────────────────────────────
  $("btn-mkdir-browse").onclick = async () => {
    const p = await api.openFolder();
    if (p) $("mkdir-path").value = p;
  };

  $("btn-create-folders").onclick = async () => {
    const base_path = $("mkdir-path").value.trim();
    const month     = parseInt($("mkdir-month").value);
    const year      = parseInt($("mkdir-year").value);
    const lbl       = $("lbl-mkdir-result");

    if (!base_path) {
      lbl.textContent = "⚠️ Chưa nhập đường dẫn folder!";
      lbl.className   = "fs-12 mt-4 text-amber";
      return;
    }
    if (!month || month < 1 || month > 12) {
      lbl.textContent = "⚠️ Tháng không hợp lệ (1 - 12)!";
      lbl.className   = "fs-12 mt-4 text-amber";
      return;
    }
    if (!year || year < 2020 || year > 2100) {
      lbl.textContent = "⚠️ Năm không hợp lệ!";
      lbl.className   = "fs-12 mt-4 text-amber";
      return;
    }

    lbl.textContent = "⏳ Đang tạo thư mục...";
    lbl.className   = "fs-12 mt-4 text-muted";

    try {
      const res = await api.createFolders(base_path, year, month);
      lbl.textContent = `✅ Tạo ${res.created} folder mới${res.existing ? ` (${res.existing} đã có sẵn)` : ""} — ${res.month_dir}`;
      lbl.className   = "fs-12 mt-4 text-green";
      toast(`✅ Đã tạo ${res.created}/${res.total} folder cho tháng ${month}/${year}`, "ok", 3000);
    } catch (e) {
      lbl.textContent = "❌ " + e.message;
      lbl.className   = "fs-12 mt-4 text-red";
    }
  };

  $("btn-save-cfg").onclick = async () => {
    const data = {
      video_base_path:   $("cfg-video-path").value.trim(),
      schedule_time:     $("cfg-stime").value.trim(),
      audd_api_key:      $("cfg-audd").value.trim(),
      openai_api_key:    $("cfg-openai").value.trim(),
      ai_model:          $("cfg-ai-model").value,
      gemini_api_key:    $("cfg-gemini").value.trim(),
      headless_mode:     $("cfg-headless").checked,
      ai_caption_enabled: !!$("cfg-openai").value.trim(),
      setup_done:        true,
    };
    try {
      await api.setConfig(data);
      toast("✅ Đã lưu cài đặt!", "ok");
      $("lbl-cfg-saved").textContent = "✅ Đã lưu!";
      $("lbl-cfg-saved").className   = "fs-12 mt-6 text-center text-green";
      setTimeout(() => $("lbl-cfg-saved").textContent = "", 3000);
      await loadStatus();
    } catch (e) {
      toast("❌ " + e.message, "err");
      $("lbl-cfg-saved").textContent = "❌ " + e.message;
      $("lbl-cfg-saved").className   = "fs-12 mt-6 text-center text-red";
    }
  };
}

async function loadSettings() {
  try {
    const cfg = await api.getConfig();
    $("cfg-video-path").value  = cfg.video_base_path || "";
    $("cfg-stime").value       = cfg.schedule_time || "10:00";
    $("cfg-audd").value        = cfg.audd_api_key || "";
    $("cfg-openai").value      = cfg.openai_api_key || "";
    $("cfg-ai-model").value    = cfg.ai_model || "gpt-4o-mini";
    $("cfg-gemini").value      = cfg.gemini_api_key || "";
    $("cfg-headless").checked  = !!cfg.headless_mode;
    // Cũng fill manual base path
    if (cfg.video_base_path) $("inp-base-path").value = cfg.video_base_path;
    if (cfg.schedule_time)   $("inp-stime").value     = cfg.schedule_time;

    // Tạo folder: pre-fill tháng/năm hiện tại
    const now = new Date();
    if (!$("mkdir-month").value) $("mkdir-month").value = now.getMonth() + 1;
    if (!$("mkdir-year").value)  $("mkdir-year").value  = now.getFullYear();
    // Pre-fill folder gốc nếu đã có trong config
    if (cfg.video_base_path && !$("mkdir-path").value)
      $("mkdir-path").value = cfg.video_base_path;
  } catch (e) {
    console.error("loadSettings error:", e);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// LOG PANELS
// ══════════════════════════════════════════════════════════════════════════════

const LOG_COLORS = {
  ok:     "#4ade80",
  err:    "#f87171",
  warn:   "#fbbf24",
  upload: "#38bdf8",
  ai:     "#c084fc",
  info:   "#60a5fa",
};

function appendLog(msg) {
  const line = document.createElement("span");
  line.className = "log-line";
  const color = LOG_COLORS[msg.level] || "#94a3b8";
  line.innerHTML = `<span class="log-ts">[${msg.ts}] </span><span style="color:${color}">${escHtml(msg.text)}</span>\n`;

  [$("log-auto"), $("log-detail")].forEach(box => {
    if (!box) return;
    box.appendChild(line.cloneNode(true));
    box.scrollTop = box.scrollHeight;
    // Giới hạn 500 dòng
    while (box.children.length > 500) box.removeChild(box.firstChild);
  });
}

function setupLogs() {
  $("btn-clear-auto-log").onclick   = () => { $("log-auto").innerHTML = ""; };
  $("btn-clear-detail-log").onclick = () => { $("log-detail").innerHTML = ""; };

  connectWS();
}

function connectWS() {
  if (_ws && _ws.readyState < 2) return;   // đang mở hoặc đang kết nối

  _ws = api.connectLogs(msg => {
    if (msg.type === "log") {
      appendLog(msg);
    } else if (msg.type === "status") {
      handleStatusEvent(msg);
    }
  });

  _ws.onclose = () => {
    console.log("[WS] disconnected — retry in 3s");
    setTimeout(connectWS, 3000);
  };
}

function handleStatusEvent(evt) {
  if (evt.event === "pipeline_start") {
    $("lbl-progress").textContent = `🚀 Pipeline đang chạy: ${evt.date}...`;
    $("pipeline-progress").style.width = "10%";
    // Pulse animation trên countdown card
    document.querySelectorAll(".card").forEach(c => {
      if (c.querySelector("#countdown")) c.classList.add("active");
    });
    toast("🚀 Pipeline bắt đầu chạy!", "ok", 3000);
  }
  if (evt.event === "pipeline_done") {
    const total = evt.ok + evt.fail;
    $("lbl-progress").textContent = `✅ Xong ${total} video: ${evt.ok} OK, ${evt.fail} lỗi`;
    $("pipeline-progress").style.width = "100%";
    document.querySelectorAll(".card.active").forEach(c => c.classList.remove("active"));
    toast(evt.fail > 0
      ? `⚠️ Xong: ${evt.ok} OK, ${evt.fail} lỗi`
      : `✅ Upload xong ${evt.ok} video!`,
      evt.fail > 0 ? "warn" : "ok", 4000);
    loadStatus();
    loadRetry();
  }
  if (evt.event === "upload_done") {
    const name = evt.video_path.split(/[\\/]/).pop();
    const stat = $(`vstatus-${findVideoIdx(evt.video_path)}`);
    if (stat) {
      stat.textContent = evt.ok ? "✅ Đã upload" : "❌ Lỗi";
      stat.className   = "video-status " + (evt.ok ? "text-green" : "text-red");
    }
    loadRetry();
  }
  if (evt.event === "analyze_done") {
    const idx  = findVideoIdx(evt.video_path);
    const stat = $(`vstatus-${idx}`);
    const song = $(`vsong-${idx}`);
    const cap  = $(`vcaption-${idx}`);
    if (stat) {
      if (evt.result?.success) {
        stat.textContent = `✅ [${evt.result.source}]`;
        stat.className   = "video-status text-green";
        if (song) song.value = `${evt.result.title} - ${evt.result.artist}`;
        if (cap && song) cycleCaption(idx);
      } else {
        stat.textContent = "⚠️ Không nhận dạng";
        stat.className   = "video-status text-amber";
      }
      const btn = $(`vbtn-analyze-${idx}`);
      if (btn) btn.disabled = false;
    }
  }
  if (evt.event === "ai_caption_done") {
    const idx = findVideoIdx(evt.video_path);
    const cap = $(`vcaption-${idx}`);
    const stat = $(`vstatus-${idx}`);
    if (cap && evt.result?.caption) cap.value = evt.result.caption;
    if (stat) { stat.textContent = "🤖 AI done"; stat.className = "video-status"; }
    const btn = $(`vbtn-ai-${idx}`);
    if (btn) btn.disabled = false;
  }
}

function findVideoIdx(vp) {
  const rows = $("video-list").querySelectorAll(".video-row");
  for (let i = 0; i < rows.length; i++) {
    if ($(`vsong-${i}`) && vp.includes($(`vrow-${i}`)?.querySelector(".video-name")?.textContent?.replace("🎬 ",""))) return i;
  }
  return -1;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
