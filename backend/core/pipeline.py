"""
core/pipeline.py — Auto pipeline + error recovery.

Pipeline mỗi video:
  recognize → caption → upload → (nếu lỗi → push retry_queue)

Scheduler: chạy ngầm 24/7, kích hoạt đúng giờ.
"""

import threading
import datetime
import time
import asyncio
from pathlib import Path
from typing import Callable, Optional

from core.config import cfg

# ── Global upload lock — chặn 2 Chrome upload chạy cùng lúc ─────────────────
# Khi 2 instance cùng dùng 1 profile Chrome → chúng kill nhau → crash
_UPLOAD_LOCK = threading.Lock()


# ── Folder helpers ────────────────────────────────────────────────────────────

def folder_name_for(date: datetime.date) -> str:
    """Trả về tên folder ngày dạng DD (vd: '08')."""
    return f"{date.day:02d}"


def find_date_folder(base: str, target: datetime.date) -> Optional[Path]:
    """
    Tìm subfolder chứa video cho ngày target.

    Ưu tiên theo thứ tự:
      1. base/MM/DD          ← cấu trúc CHÍNH (tháng → ngày)
      2. base/M/DD           ← tháng không có số 0 (5, 6...)
      3. base/DDMM           ← cấu trúc cũ (backward compat)
      4. Các format phẳng khác

    Ví dụ ngày 08/05/2026:
      base/05/08  ✓ (chuẩn)
      base/5/08   ✓
      base/0805   ✓ (cũ)
    """
    base_p = Path(base)
    if not base_p.is_dir():
        return None

    d_zero = f"{target.day:02d}"      # "08"
    d_bare = str(target.day)          # "8"
    m_zero = f"{target.month:02d}"    # "05"
    m_bare = str(target.month)        # "5"
    y_str  = str(target.year)

    # ── 1. Cấu trúc MỚI: base / MM / DD ──────────────────────────────────
    # Thử tất cả biến thể tên tháng
    month_candidates = [m_zero, m_bare]
    for m_folder in month_candidates:
        month_p = base_p / m_folder
        if not month_p.is_dir():
            continue
        # Tìm folder ngày bên trong
        for d_folder in [d_zero, d_bare]:
            day_p = month_p / d_folder
            if day_p.is_dir():
                return day_p

    # ── 2. Cấu trúc cũ: base / DDMM ──────────────────────────────────────
    p = base_p / f"{d_zero}{m_zero}"
    if p.is_dir():
        return p

    # ── 3. Các format phẳng khác ──────────────────────────────────────────
    for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y",
                "%Y%m%d", "%d%m%Y", "%d_%m_%Y", "%Y_%m_%d"]:
        p = base_p / target.strftime(fmt)
        if p.is_dir():
            return p

    # ── 4. Fuzzy trong base: tên chứa ngày + tháng ────────────────────────
    for folder in sorted(base_p.iterdir()):
        if not folder.is_dir():
            continue
        n = folder.name
        if d_zero in n and m_zero in n and (y_str in n or len(n) <= 6):
            return folder

    return None


def expected_folder_path(base: str, target: datetime.date) -> str:
    """Trả về path dự kiến để hiển thị trong log khi không tìm thấy folder."""
    m = f"{target.month:02d}"
    d = f"{target.day:02d}"
    return f"{base}\\{m}\\{d}"


def scan_videos(folder: Path) -> list:
    exts = cfg.get("video_exts", ["mp4", "mov", "avi", "webm", "mkv"])
    vids = []
    for ext in exts:
        vids += list(folder.glob(f"*.{ext}"))
        vids += list(folder.glob(f"*.{ext.upper()}"))
    return sorted(set(vids))


# ── Song recognition ──────────────────────────────────────────────────────────

def recognize(video_path: str) -> dict:
    """Gọi shazam_recognizer (nằm cùng thư mục app)."""
    try:
        import sys
        from core.config import APP_DIR
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
        from shazam_recognizer import recognize as _rec, song_display
        info    = _rec(video_path, cfg.get("audd_api_key", ""))
        display = song_display(info)
        return {"success": info.get("success", False),
                "display": display,
                "source":  info.get("source", ""),
                "error":   info.get("error", "")}
    except Exception as e:
        return {"success": False, "display": "", "error": str(e)}


# ── Caption builder ───────────────────────────────────────────────────────────

_FALLBACK = [
    "🎵 Nhạc hay mỗi ngày! #FYP #nhachay #xuhuong",
    "🎶 Giai điệu tuyệt vời! #FYP #nhachay #amnhac",
    "🎵 Chill cùng bài hát hay! #FYP #nhacviet #viral",
    "🎤 Hit mới cực đỉnh! #FYP #nhachay #trending",
    "🎵 Ngọt ngào quá! #FYP #nhacviet #xuhuong",
    "🎶 Đỉnh của chóp! #FYP #nhachay #viral",
    "🎵 Nghe hoài không chán! #FYP #nhacviet #trending",
]


def build_caption(song_display: str, tpl_idx: int = 0) -> str:
    tpls = cfg.get("caption_templates", _FALLBACK)
    if song_display:
        return tpls[tpl_idx % len(tpls)].format(song=song_display)
    return _FALLBACK[tpl_idx % len(_FALLBACK)]


# ── TikTok upload ─────────────────────────────────────────────────────────────

def upload(video_path: str, caption: str, profile_path: str,
           log: Callable = print, headless: bool = True) -> bool:
    """
    Upload video lên TikTok.

    headless=True  → Chrome ẩn (đặt ngoài màn hình, không thấy)
    headless=False → Chrome hiện lên bình thường

    Dùng _UPLOAD_LOCK để đảm bảo không có 2 Chrome upload chạy cùng lúc.
    """
    acquired = _UPLOAD_LOCK.acquire(blocking=True, timeout=600)  # chờ tối đa 10 phút
    if not acquired:
        log("⚠️ Upload lock timeout — bỏ qua video này")
        return False
    try:
        from core.config import APP_DIR
        import sys
        if str(APP_DIR) not in sys.path:
            sys.path.insert(0, str(APP_DIR))
        from tiktok_bot import upload_video_sync
        return upload_video_sync(
            video_path, caption, profile_path,
            log=log, headless=headless,
        )
    except Exception as e:
        log(f"❌ Upload exception: {e}")
        return False
    finally:
        _UPLOAD_LOCK.release()


# ── Single video processor ────────────────────────────────────────────────────

class VideoResult:
    def __init__(self, video_path: str):
        self.video_path   = video_path
        self.name         = Path(video_path).name
        self.song_display = ""
        self.caption      = ""
        self.uploaded     = False
        self.error        = ""


def process_video(vp: Path, profile_path: str,
                  tpl_idx: int, log: Callable) -> VideoResult:
    """
    Xử lý 1 video: recognize → caption → upload.
    Nếu lỗi → đưa vào retry_queue.
    """
    result = VideoResult(str(vp))

    # ── Bước 1: Nhận dạng ────────────────────────────────────────────────
    log(f"   🎵 Nhận dạng: {vp.name}")
    info = recognize(str(vp))
    result.song_display = info.get("display", "")
    if info.get("success"):
        log(f"   ✅ {result.song_display} [{info.get('source','?')}]")
    else:
        log(f"   ⚠️ Không nhận dạng — dùng fallback caption")

    # ── Bước 2: Caption (AI nếu được bật, fallback template) ─────────────
    if cfg.ai_caption_enabled and cfg.openai_api_key:
        try:
            from core.ai_caption import build_caption_with_ai
            ai = build_caption_with_ai(
                str(vp), cfg.openai_api_key, tpl_idx, cfg.ai_model)
            result.caption = ai["caption"]
            if ai["source"] == "ai":
                if not result.song_display and ai["song"]:
                    result.song_display = ai["song"]
                log(f"   🤖 AI caption [{cfg.ai_model}]: {result.caption[:60]}...")
            else:
                log(f"   ⚠️ AI lỗi ({ai['error']}) — dùng template")
                log(f"   📝 {result.caption[:60]}...")
        except Exception as e:
            result.caption = build_caption(result.song_display, tpl_idx)
            log(f"   ⚠️ AI exception: {e} — dùng template")
    else:
        result.caption = build_caption(result.song_display, tpl_idx)
        log(f"   📝 {result.caption[:60]}...")

    # ── Bước 3: Upload ────────────────────────────────────────────────────
    headless = cfg.headless_mode          # đọc từ config (mặc định True = ẩn)
    mode_str = "ẩn" if headless else "hiển thị"
    log(f"   📤 Upload TikTok (Chrome {mode_str})...")
    ok = upload(str(vp), result.caption, profile_path, log=log, headless=headless)

    if ok:
        result.uploaded = True
        cfg.pop_retry(str(vp))          # Xóa khỏi retry nếu đã thành công
        log(f"   ✅ Upload thành công!")
    else:
        result.error = "Upload thất bại"
        cfg.push_retry(str(vp), result.caption, result.error)
        log(f"   ❌ Upload thất bại → đã thêm vào retry queue")

    return result


# ── Pipeline (nhiều video) ────────────────────────────────────────────────────

class PipelineResult:
    def __init__(self):
        self.total   = 0
        self.success = 0
        self.failed  = 0
        self.results: list[VideoResult] = []


def run_pipeline(target_date: datetime.date, log: Callable = print) -> PipelineResult:
    """
    Chạy toàn bộ pipeline cho ngày target_date.
    Trả về PipelineResult.
    """
    result = PipelineResult()
    date_str  = target_date.strftime("%d/%m/%Y")
    m_str     = f"{target_date.month:02d}"
    d_str     = f"{target_date.day:02d}"

    log(f"═══ PIPELINE BẮT ĐẦU — {date_str} (tìm folder: {m_str}/{d_str}) ═══")

    # ── Kiểm tra cấu hình ────────────────────────────────────────────────
    base = cfg.video_base_path
    if not base:
        log("❌ Chưa cấu hình Video Folder! Vào Settings để thiết lập.")
        return result

    account = cfg.active_account
    if not account:
        log("❌ Chưa có tài khoản TikTok! Vào tab Accounts để thêm.")
        return result

    profile_path = str(cfg.profile_path(account))

    # ── Tìm folder ────────────────────────────────────────────────────────
    folder = find_date_folder(base, target_date)
    if not folder:
        expected = expected_folder_path(base, target_date)
        log(f"❌ Không tìm thấy folder ngày {date_str}")
        log(f"   Cần tạo: {expected}")
        return result

    log(f"📁 Folder: {folder}")

    # ── Scan video ────────────────────────────────────────────────────────
    videos = scan_videos(folder)
    if not videos:
        log(f"📭 Không có video trong: {folder}")
        return result

    result.total = len(videos)
    log(f"🎬 Tìm thấy {len(videos)} video — tài khoản: {account['name']}")

    # ── Xử lý từng video ─────────────────────────────────────────────────
    for i, vp in enumerate(videos):
        log(f"\n── [{i+1}/{len(videos)}] {vp.name}")
        vr = process_video(vp, profile_path, tpl_idx=i, log=log)
        result.results.append(vr)
        if vr.uploaded:
            result.success += 1
        else:
            result.failed += 1

        if i < len(videos) - 1:
            log("⏸️ Nghỉ 15 giây...")
            time.sleep(15)

    log(f"\n═══ HOÀN THÀNH: {result.success}/{result.total} thành công ═══")
    return result


def retry_failed(log: Callable = print) -> int:
    """Thử upload lại tất cả video trong retry_queue."""
    queue = cfg.retry_queue
    if not queue:
        log("✅ Retry queue trống — không có gì cần retry.")
        return 0

    account = cfg.active_account
    if not account:
        log("❌ Chưa có tài khoản TikTok!")
        return 0

    profile_path = str(cfg.profile_path(account))
    headless     = cfg.headless_mode
    success      = 0

    log(f"🔄 Retry {len(queue)} video thất bại...")
    for item in list(queue):
        vp      = Path(item["video_path"])
        caption = item.get("caption", "")
        log(f"📤 Retry: {vp.name}")

        if not vp.exists():
            log(f"⚠️ File không còn tồn tại: {vp.name}")
            cfg.pop_retry(str(vp))
            continue

        ok = upload(str(vp), caption, profile_path, log=log, headless=headless)
        if ok:
            cfg.pop_retry(str(vp))
            success += 1
            log(f"✅ Retry thành công: {vp.name}")
        else:
            log(f"❌ Retry thất bại lần nữa: {vp.name}")

        time.sleep(10)

    log(f"🏁 Retry xong: {success}/{len(queue)} thành công.")
    return success


# ── Scheduler ─────────────────────────────────────────────────────────────────

class Scheduler:
    """
    Chạy nền 24/7.
    Mỗi 10 giây kiểm tra giờ, kích hoạt pipeline trong cửa sổ 90 giây.

    Target date logic:
      - Track _last_triggered (datetime) thay vì chỉ date → cho phép chạy lại
        hôm nay nếu user thay đổi giờ hẹn sang giờ khác.
      - _last_triggered so sánh ĐÚNG scheduled datetime:
          * Nếu hôm nay 10:00 đã chạy, user đặt lại 15:00 → 15:00 ≠ 10:00 → chạy được.
          * Nếu vẫn giữ 10:00 → 10:00 == 10:00 → không chạy lại (đúng).
      - Survive restart: lưu vào config._last_triggered (ISO datetime string).
    """

    def __init__(self, on_start: Callable, on_done: Callable, on_log: Callable):
        self._on_start = on_start
        self._on_done  = on_done
        self._on_log   = on_log

        self._stop     = threading.Event()
        self._paused   = False
        self._running  = False
        self._override_date: Optional[datetime.date] = None  # override thủ công
        self._thread   = threading.Thread(target=self._loop, daemon=True)

        # Restore _last_triggered từ config (survive restart)
        # Dùng datetime (không chỉ date) để phân biệt theo giờ chạy
        self._last_triggered: Optional[datetime.datetime] = None
        saved_dt = cfg.get("_last_triggered", "")
        if saved_dt:
            try:
                self._last_triggered = datetime.datetime.fromisoformat(saved_dt)
            except Exception:
                pass
        if self._last_triggered is None:
            # Backward compat: thử đọc _last_ran_date cũ (chỉ có date)
            saved_d = cfg.get("_last_ran_date", "")
            if saved_d:
                try:
                    d = datetime.date.fromisoformat(saved_d)
                    h, m = self._parse_time()
                    # Reconstruct: giả định đã trigger đúng giờ hẹn hiện tại
                    self._last_triggered = datetime.datetime.combine(
                        d, datetime.time(h, m))
                except Exception:
                    pass

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def running(self) -> bool:
        return self._running

    # ── Target date ───────────────────────────────────────────────────────────

    @property
    def target_date(self) -> datetime.date:
        """
        Ngày mục tiêu cho lần chạy tiếp theo.
        - Nếu user đã set override thủ công → dùng override
        - Nếu _last_triggered khớp với ĐÚNG scheduled datetime hôm nay → ngày mai
          (đã chạy giờ đó rồi, không cần chạy lại)
        - Còn lại → hôm nay
        """
        if self._override_date:
            return self._override_date
        today = datetime.date.today()
        if self._last_triggered is not None:
            h, m = self._parse_time()
            today_sched = datetime.datetime.combine(today, datetime.time(h, m))
            if self._last_triggered == today_sched:
                # Đã trigger đúng slot này hôm nay → advance sang ngày mai
                return today + datetime.timedelta(days=1)
        return today

    def set_target_date(self, d: datetime.date):
        """Override ngày mục tiêu thủ công."""
        self._override_date = d

    def clear_target_override(self):
        """Xóa override, về lại tự động theo ngày thực."""
        self._override_date = None

    def advance_target(self):
        """Nhảy target date sang ngày hôm sau (gọi sau khi pipeline xong)."""
        current = self.target_date
        self._override_date = current + datetime.timedelta(days=1)

    # ── Trigger ───────────────────────────────────────────────────────────────

    def trigger_now(self):
        """Kích hoạt thủ công ngay với target_date hiện tại."""
        if self._running:
            return
        date = self.target_date
        threading.Thread(target=self._run, args=(date,), daemon=True).start()

    def trigger_date(self, d: datetime.date):
        """Kích hoạt thủ công cho 1 ngày cụ thể."""
        if self._running:
            return
        threading.Thread(target=self._run, args=(d,), daemon=True).start()

    # ── Next scheduled run ────────────────────────────────────────────────────

    def next_run(self) -> datetime.datetime:
        """Thời điểm scheduled tiếp theo (datetime)."""
        h, m    = self._parse_time()
        target  = self.target_date
        return datetime.datetime.combine(target, datetime.time(h, m))

    # ── Internal ──────────────────────────────────────────────────────────────

    def _parse_time(self):
        s = cfg.schedule_time
        try:
            h, m = s.split(":")
            return int(h), int(m)
        except Exception:
            return 10, 0

    def _loop(self):
        """
        Poll mỗi 10 giây.
        Trigger trong cửa sổ 90 giây SAU giờ hẹn (tránh miss do polling delay).
        """
        while not self._stop.is_set():
            try:
                if not self._paused and not self._running:
                    self._check_schedule()
            except Exception as e:
                try:
                    self._on_log(f"⚠️ Scheduler lỗi: {e}")
                except Exception:
                    pass
            self._stop.wait(10)   # Poll mỗi 10s (trước là 30s)

    def _check_schedule(self):
        """
        Kiểm tra xem đã đến giờ chạy chưa.
        Dùng cửa sổ 0–90 giây sau giờ hẹn để tránh miss.

        So sánh theo FULL DATETIME (không chỉ date):
        → User đổi giờ hẹn sang slot mới hôm nay → trigger được ngay.
        """
        h, m       = self._parse_time()
        now        = datetime.datetime.now()
        target_d   = self.target_date

        # Scheduled datetime cụ thể cho lần chạy này
        scheduled  = datetime.datetime.combine(target_d, datetime.time(h, m))
        elapsed    = (now - scheduled).total_seconds()

        # Đã trigger đúng slot này chưa? (so sánh datetime, không phải date)
        already = (self._last_triggered is not None
                   and self._last_triggered == scheduled)

        # ── Điều kiện kích hoạt ───────────────────────────────────────────────
        # 1. Đã qua giờ hẹn nhưng không quá 90 giây
        # 2. Đúng ngày target
        # 3. Chưa trigger slot datetime này (cho phép trigger lại nếu đổi giờ)
        if (0 <= elapsed < 90
                and now.date() == target_d
                and not already):
            self._on_log(
                f"⏰ Đến giờ hẹn {h:02d}:{m:02d} — kích hoạt pipeline "
                f"({elapsed:.0f}s sau giờ hẹn)"
            )
            # Đánh dấu TRƯỚC khi chạy để tránh double-trigger
            self._last_triggered = scheduled
            cfg.set("_last_triggered", scheduled.isoformat())
            self._run(target_d)

    def _run(self, date: datetime.date):
        self._running = True
        if self._on_start:
            self._on_start(date)
        try:
            result = run_pipeline(date, log=self._on_log)
            if self._on_done:
                self._on_done(result)

            # _last_triggered đã được set trong _check_schedule() (trước khi gọi _run)
            # → target_date tự tính next day dựa vào _last_triggered
            # Backward compat: vẫn lưu _last_ran_date
            cfg.set("_last_ran_date", date.isoformat())

            next_d = self.target_date  # target_date đã advance do _last_triggered == today_sched
            self._on_log(
                f"📅 Xong ngày {date:%d/%m/%Y} → "
                f"Ngày mục tiêu tiếp theo: {next_d:%d/%m/%Y}"
            )

        except Exception as e:
            self._on_log(f"❌ Pipeline crash: {e}")
        finally:
            self._running = False
