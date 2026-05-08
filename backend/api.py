"""
backend/api.py — FastAPI server cho TikTok Auto Uploader V2.

Chạy: uvicorn api:app --host 127.0.0.1 --port 8765 --reload

Endpoints:
  GET  /status          — trạng thái scheduler + config tổng hợp
  GET  /config          — toàn bộ config
  POST /config          — update config
  GET  /accounts        — danh sách accounts
  POST /accounts        — thêm account mới
  DELETE /accounts/{i}  — xóa account
  POST /accounts/{i}/active  — set active account
  POST /accounts/{i}/login   — mở Chrome đăng nhập TikTok

  GET  /scheduler/status   — trạng thái scheduler
  POST /scheduler/start    — bật scheduler
  POST /scheduler/stop     — tắt scheduler
  POST /scheduler/trigger  — chạy ngay
  POST /scheduler/time     — đổi giờ chạy
  POST /scheduler/target   — đổi ngày target

  GET  /videos             — quét video theo ngày
  POST /videos/analyze     — nhận dạng bài hát 1 video
  POST /videos/caption-ai  — tạo caption AI
  POST /videos/upload      — upload 1 video

  GET  /retry              — danh sách video lỗi
  POST /retry/run          — retry tất cả video lỗi
  DELETE /retry/{i}        — xóa khỏi retry queue

  WS   /ws/logs           — stream logs real-time
"""

import asyncio
import sys
import io
import threading
import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# ── Fix encoding cho Windows (PyInstaller bundle không có proper console) ────
# Emoji trong log gây UnicodeEncodeError nếu không set UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Thêm backend folder vào sys.path ─────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from core.config import cfg, APP_DIR
from core.pipeline import (
    Scheduler, run_pipeline, retry_failed,
    find_date_folder, scan_videos, process_video,
    build_caption, recognize, folder_name_for,
)

# ══════════════════════════════════════════════════════════════════════════════
# WebSocket log manager — broadcast log tới tất cả clients đang kết nối
# ══════════════════════════════════════════════════════════════════════════════

class LogManager:
    def __init__(self):
        self._clients: list[WebSocket] = []
        self._history: list[dict] = []   # giữ 500 dòng cuối cho client mới kết nối
        self._lock = threading.Lock()

    def add_client(self, ws: WebSocket):
        with self._lock:
            self._clients.append(ws)

    def remove_client(self, ws: WebSocket):
        with self._lock:
            self._clients = [c for c in self._clients if c != ws]

    async def send_history(self, ws: WebSocket):
        """Gửi log cũ cho client mới kết nối."""
        for msg in self._history[-200:]:
            try:
                await ws.send_json(msg)
            except Exception:
                break

    def push(self, line: str, level: str = "info"):
        """Gọi từ bất kỳ thread nào — gửi log tới tất cả WS clients."""
        msg = {
            "type": "log",
            "level": level,
            "text": line,
            "ts": datetime.datetime.now().strftime("%H:%M:%S"),
        }
        with self._lock:
            self._history.append(msg)
            if len(self._history) > 500:
                self._history = self._history[-500:]
            clients = list(self._clients)

        # Broadcast async từ thread bất kỳ
        for ws in clients:
            try:
                asyncio.run_coroutine_threadsafe(
                    ws.send_json(msg),
                    _event_loop,
                )
            except Exception:
                pass

    def push_status(self, payload: dict):
        """Broadcast status update (scheduler state change, progress...)."""
        msg = {"type": "status", **payload}
        with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                asyncio.run_coroutine_threadsafe(
                    ws.send_json(msg),
                    _event_loop,
                )
            except Exception:
                pass


log_mgr = LogManager()
_event_loop: asyncio.AbstractEventLoop = None   # set khi app startup


def _log(line: str):
    """Hàm log chung — gửi tới WS + in terminal."""
    try:
        print(f"[LOG] {line}", flush=True)
    except (UnicodeEncodeError, Exception):
        try:
            print(f"[LOG] {line.encode('ascii', errors='replace').decode()}", flush=True)
        except Exception:
            pass
    level = "ok"     if "✅" in line else \
            "err"    if "❌" in line else \
            "warn"   if "⚠️" in line else \
            "upload" if "📤" in line else \
            "ai"     if "🤖" in line else "info"
    log_mgr.push(line, level)


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler setup
# ══════════════════════════════════════════════════════════════════════════════

def _on_pipeline_start(date: datetime.date):
    log_mgr.push_status({"event": "pipeline_start", "date": str(date)})
    _log(f"🚀 Pipeline bắt đầu: {date}")


def _on_pipeline_done(result):
    """Callback nhận PipelineResult object từ Scheduler._run()."""
    ok   = getattr(result, "success", 0)
    fail = getattr(result, "failed",  0)
    log_mgr.push_status({"event": "pipeline_done", "ok": ok, "fail": fail})
    _log(f"✅ Pipeline xong: {ok} thành công, {fail} lỗi")


scheduler = Scheduler(
    on_start=_on_pipeline_start,
    on_done=_on_pipeline_done,
    on_log=_log,
)


# ── Lifespan + App init (phải định nghĩa TRƯỚC khi tạo app) ─────────────────
@asynccontextmanager
async def lifespan(app_: FastAPI):
    global _event_loop
    _event_loop = asyncio.get_event_loop()
    try:
        scheduler.start()
        _log("🟢 API server khởi động. Scheduler đang chạy.")
    except RuntimeError:
        _log("🟢 API server khởi động (scheduler thread đã chạy).")
    yield
    scheduler.pause()   # dừng nhẹ nhàng, không kill thread


# App được tạo SAU khi lifespan đã được định nghĩa
app = FastAPI(title="TikTok Auto Uploader API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket endpoint
# ══════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    await ws.accept()
    log_mgr.add_client(ws)
    await log_mgr.send_history(ws)
    try:
        while True:
            # Giữ connection sống — nhận ping từ client
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        log_mgr.remove_client(ws)


# ══════════════════════════════════════════════════════════════════════════════
# /status — dashboard data
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/status")
def get_status():
    sched = scheduler
    target = sched.target_date
    h, m   = sched._parse_time()
    sched_dt = datetime.datetime.combine(target, datetime.time(h, m))
    now      = datetime.datetime.now()
    secs_left = max(0, int((sched_dt - now).total_seconds()))

    return {
        "scheduler": {
            "thread_alive":  sched._thread.is_alive(),   # thread nền có đang chạy không
            "pipeline_running": sched.running,           # pipeline đang xử lý video không
            "paused":        sched._paused,
            "target_date":   str(target),
            "schedule_time": cfg.schedule_time,
            "next_run_dt":   sched_dt.isoformat(),
            "seconds_left":  secs_left,
            "last_triggered": sched._last_triggered.isoformat() if sched._last_triggered else None,
        },
        "config": {
            "video_base_path": cfg.video_base_path,
            "active_account":  cfg.active_account,
            "accounts_count":  len(cfg.accounts),
            "retry_count":     len(cfg.retry_queue),
            "setup_done":      cfg.setup_done,
            "headless_mode":   cfg.headless_mode,
            "ai_caption_enabled": cfg.ai_caption_enabled,
        }
    }


# ══════════════════════════════════════════════════════════════════════════════
# /config
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/config")
def get_config():
    return cfg._data


class ConfigUpdate(BaseModel):
    data: dict


@app.post("/config")
def update_config(body: ConfigUpdate):
    # Detect thay đổi schedule_time → reset _last_triggered
    old_stime = cfg.schedule_time
    cfg.update(body.data)
    if body.data.get("schedule_time") and body.data["schedule_time"] != old_stime:
        scheduler._last_triggered = None
        cfg.set("_last_triggered", "")
        _log(f"⏰ Đổi giờ: {old_stime} → {body.data['schedule_time']}")
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# /accounts
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/accounts")
def get_accounts():
    accounts = cfg.accounts
    active   = cfg.get("active_account", 0)
    result   = []
    for i, acc in enumerate(accounts):
        pp = cfg.profile_path(acc)
        session_info = {}
        try:
            from tiktok_api import get_session_info
            session_info = get_session_info(str(pp))
        except Exception:
            pass
        result.append({
            "index":      i,
            "name":       acc["name"],
            "profile_dir": acc.get("profile_dir", ""),
            "is_active":  (i == active),
            "session":    session_info,
        })
    return result


class AddAccountBody(BaseModel):
    name: str


@app.post("/accounts")
def add_account(body: AddAccountBody):
    if not body.name.strip():
        raise HTTPException(400, "Tên không được trống")
    acc = cfg.add_account(body.name.strip())
    _log(f"➕ Thêm tài khoản: {body.name}")
    return {"ok": True, "account": acc}


@app.delete("/accounts/{idx}")
def delete_account(idx: int):
    accs = cfg.accounts
    if idx < 0 or idx >= len(accs):
        raise HTTPException(404, "Không tìm thấy tài khoản")
    name = accs[idx]["name"]
    cfg.remove_account(idx)
    _log(f"🗑️ Đã xóa tài khoản: {name}")
    return {"ok": True}


@app.post("/accounts/{idx}/active")
def set_active(idx: int):
    if idx < 0 or idx >= len(cfg.accounts):
        raise HTTPException(404, "Không tìm thấy tài khoản")
    cfg.set_active(idx)
    _log(f"✅ Active account → {cfg.accounts[idx]['name']}")
    return {"ok": True}


@app.post("/accounts/{idx}/login")
def login_account(idx: int, background_tasks: BackgroundTasks):
    accs = cfg.accounts
    if idx < 0 or idx >= len(accs):
        raise HTTPException(404, "Không tìm thấy tài khoản")
    acc = accs[idx]
    pp  = str(cfg.profile_path(acc))

    def _run():
        import sys
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
        from tiktok_bot import setup_login_sync
        _log(f"🔐 Mở Chrome đăng nhập: {acc['name']}")
        setup_login_sync(pp, log=_log)
        try:
            from tiktok_api import extract_and_save, get_session_info
            ok = extract_and_save(pp)
            if ok:
                info = get_session_info(pp)
                _log(f"✅ Session đã lưu: {info.get('session_id', '?')}")
            else:
                _log("⚠️ Không đọc được cookies")
        except Exception as e:
            _log(f"⚠️ {e}")

    background_tasks.add_task(_run)
    return {"ok": True, "message": "Đang mở Chrome..."}


# ══════════════════════════════════════════════════════════════════════════════
# /scheduler
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/scheduler/status")
def scheduler_status():
    return get_status()["scheduler"]


@app.post("/scheduler/start")
def scheduler_start():
    """Resume scheduler (bỏ pause)."""
    scheduler.resume()
    _log("▶️ Scheduler đã bật / tiếp tục")
    return {"ok": True}


@app.post("/scheduler/stop")
def scheduler_stop():
    """Pause scheduler (không kill thread)."""
    scheduler.pause()
    _log("⏸️ Scheduler đã tạm dừng")
    return {"ok": True}


@app.post("/scheduler/trigger")
def scheduler_trigger(background_tasks: BackgroundTasks):
    """Chạy pipeline ngay lập tức."""
    _log("▶️ Chạy pipeline ngay theo yêu cầu...")
    background_tasks.add_task(scheduler.trigger_now)
    return {"ok": True}


class TimeBody(BaseModel):
    time: str   # "HH:MM"


@app.post("/scheduler/time")
def set_schedule_time(body: TimeBody):
    import re
    if not re.match(r"^\d{1,2}:\d{2}$", body.time):
        raise HTTPException(400, "Format sai, dùng HH:MM")
    old = cfg.schedule_time
    cfg.set("schedule_time", body.time)
    scheduler._last_triggered = None
    cfg.set("_last_triggered", "")
    _log(f"⏰ Đổi giờ: {old} → {body.time}")
    return {"ok": True}


class DateBody(BaseModel):
    date: str   # "YYYY-MM-DD"


@app.post("/scheduler/target")
def set_target_date(body: DateBody):
    try:
        d = datetime.date.fromisoformat(body.date)
    except Exception:
        raise HTTPException(400, "Format ngày sai, dùng YYYY-MM-DD")
    scheduler.set_target_date(d)
    _log(f"📅 Đặt ngày target: {d.strftime('%d/%m/%Y')}")
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# /videos
# ══════════════════════════════════════════════════════════════════════════════

class ScanBody(BaseModel):
    base_path: Optional[str] = None
    date: Optional[str] = None   # "YYYY-MM-DD"


@app.post("/videos/scan")
def scan_videos_endpoint(body: ScanBody):
    base = body.base_path or cfg.video_base_path
    if not base:
        raise HTTPException(400, "Chưa có video_base_path")
    try:
        date = datetime.date.fromisoformat(body.date) if body.date else datetime.date.today()
    except Exception:
        raise HTTPException(400, "Format ngày sai")

    folder = find_date_folder(base, date)
    if not folder:
        return {"videos": [], "folder": None, "date": str(date)}

    videos = scan_videos(str(folder))
    return {
        "videos": [str(v) for v in videos],
        "folder": str(folder),
        "date":   str(date),
    }


class AnalyzeBody(BaseModel):
    video_path: str


@app.post("/videos/analyze")
def analyze_video(body: AnalyzeBody, background_tasks: BackgroundTasks):
    """Nhận dạng bài hát — chạy background vì mất 5-10s."""
    import uuid
    task_id = str(uuid.uuid4())[:8]

    def _run():
        result = recognize(body.video_path, audd_api_key=cfg.get("audd_api_key", ""))
        if result.get("success"):
            _log(f"🎵 [{task_id}] {body.video_path} → {result.get('title')} - {result.get('artist')}")
        else:
            _log(f"⚠️ [{task_id}] Không nhận dạng: {result.get('error')}")
        log_mgr.push_status({
            "event": "analyze_done",
            "task_id": task_id,
            "video_path": body.video_path,
            "result": result,
        })

    background_tasks.add_task(_run)
    return {"ok": True, "task_id": task_id, "message": "Đang nhận dạng..."}


class AICaptionBody(BaseModel):
    video_path: str
    tpl_idx: Optional[int] = 0


@app.post("/videos/caption-ai")
def ai_caption(body: AICaptionBody, background_tasks: BackgroundTasks):
    import uuid
    task_id = str(uuid.uuid4())[:8]

    def _run():
        from core.ai_caption import build_caption_with_ai
        result = build_caption_with_ai(
            body.video_path,
            api_key=cfg.openai_api_key,
            tpl_idx=body.tpl_idx or 0,
            model=cfg.ai_model,
        )
        _log(f"🤖 [{task_id}] AI caption: {result.get('caption', '')[:60]}")
        log_mgr.push_status({
            "event": "ai_caption_done",
            "task_id": task_id,
            "video_path": body.video_path,
            "result": result,
        })

    background_tasks.add_task(_run)
    return {"ok": True, "task_id": task_id}


class UploadBody(BaseModel):
    video_path: str
    caption: str


@app.post("/videos/upload")
def upload_video(body: UploadBody, background_tasks: BackgroundTasks):
    acc = cfg.active_account
    if not acc:
        raise HTTPException(400, "Chưa có tài khoản active")
    import uuid
    task_id = str(uuid.uuid4())[:8]

    def _run():
        pp = str(cfg.profile_path(acc))
        import sys
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
        from tiktok_bot import upload_video_sync
        _log(f"📤 [{task_id}] Đang upload: {Path(body.video_path).name}")
        try:
            ok = upload_video_sync(body.video_path, body.caption, pp, log=_log)
        except Exception as e:
            ok = False
            _log(f"❌ [{task_id}] Upload exception: {e}")
        if not ok:
            cfg.push_retry(body.video_path, body.caption, "upload failed")
        log_mgr.push_status({
            "event": "upload_done",
            "task_id": task_id,
            "video_path": body.video_path,
            "ok": ok,
        })

    background_tasks.add_task(_run)
    return {"ok": True, "task_id": task_id, "message": "Đang upload..."}


# ══════════════════════════════════════════════════════════════════════════════
# /retry
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/retry")
def get_retry():
    return cfg.retry_queue


@app.post("/retry/run")
def run_retry(background_tasks: BackgroundTasks):
    if not cfg.retry_queue:
        return {"ok": True, "message": "Queue trống"}
    def _run():
        retry_failed(log=_log)
    background_tasks.add_task(_run)
    return {"ok": True, "message": f"Đang retry {len(cfg.retry_queue)} video..."}


@app.delete("/retry/{idx}")
def delete_retry(idx: int):
    q = cfg.retry_queue
    if idx < 0 or idx >= len(q):
        raise HTTPException(404, "Không tìm thấy")
    vp = q[idx]["video_path"]
    cfg.pop_retry(vp)
    return {"ok": True}


@app.delete("/retry")
def clear_retry():
    cfg.clear_retry()
    _log("🗑️ Đã xóa toàn bộ retry queue")
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# /folders — tạo cấu trúc thư mục tháng/ngày
# ══════════════════════════════════════════════════════════════════════════════

import calendar as _calendar

class CreateFoldersBody(BaseModel):
    base_path: str
    year:  int
    month: int


@app.post("/folders/create")
def create_folders(body: CreateFoldersBody):
    base = Path(body.base_path.strip())
    if not base.exists():
        raise HTTPException(400, f"Folder không tồn tại: {body.base_path}")
    if not (1 <= body.month <= 12):
        raise HTTPException(400, "Tháng phải từ 1 đến 12")
    if body.year < 2000 or body.year > 2100:
        raise HTTPException(400, "Năm không hợp lệ")

    m_str    = f"{body.month:02d}"
    num_days = _calendar.monthrange(body.year, body.month)[1]

    created  = []
    existing = []

    for day in range(1, num_days + 1):
        d_str   = f"{day:02d}"
        day_dir = base / m_str / d_str
        if day_dir.exists():
            existing.append(d_str)
        else:
            day_dir.mkdir(parents=True, exist_ok=True)
            created.append(d_str)

    msg = f"✅ Tạo {len(created)} folder mới trong {base / m_str}"
    if existing:
        msg += f" ({len(existing)} đã có sẵn)"
    _log(msg)

    return {
        "ok":       True,
        "month_dir": str(base / m_str),
        "created":  len(created),
        "existing": len(existing),
        "total":    num_days,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Run standalone (dev + PyInstaller bundle)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    # log_config=None: tắt uvicorn logging formatter (fix lỗi PyInstaller bundle)
    # access_log=False: không cần log từng HTTP request
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8765,
        reload=False,
        log_config=None,
        access_log=False,
    )
