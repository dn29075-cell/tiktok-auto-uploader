"""
core/config.py — Quản lý config portable.

- Tất cả đường dẫn lưu dạng TƯƠNG ĐỐI so với APP_DIR
- Khi copy app sang máy mới → config tự adapt
- Không hardcode ổ đĩa hay tên user
"""

import json
import platform
import sys
from pathlib import Path

# ── App root (portable — luôn là folder chứa main.py) ────────────────────────
if getattr(sys, "frozen", False):
    # Đang chạy từ PyInstaller exe
    APP_DIR = Path(sys.executable).parent
else:
    # Đang chạy từ source
    APP_DIR = Path(__file__).parent.parent

DATA_DIR     = APP_DIR / "data"
PROFILES_DIR = DATA_DIR / "profiles"
LOGS_DIR     = DATA_DIR / "logs"
CONFIG_FILE  = DATA_DIR / "config.json"

# Tạo các folder cần thiết nếu chưa có
for _d in [DATA_DIR, PROFILES_DIR, LOGS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── Platform ──────────────────────────────────────────────────────────────────
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# ── Default config ────────────────────────────────────────────────────────────
_DEFAULTS: dict = {
    # Đường dẫn folder gốc chứa các folder ngày (DDMM)
    "video_base_path": "",

    # Giờ chạy tự động hàng ngày
    "schedule_time": "10:00",

    # Danh sách tài khoản TikTok
    # Mỗi account: { "name": str, "profile_dir": str (relative to PROFILES_DIR) }
    "accounts": [],

    # Index account đang active
    "active_account": 0,

    # AudD API key (tuỳ chọn — backup cho Shazam)
    "audd_api_key": "",

    # Định dạng video được scan
    "video_exts": ["mp4", "mov", "avi", "webm", "mkv"],

    # Template caption tiếng Việt
    "caption_templates": [
        "🎵 {song} 🌸 Nghe là nghiện luôn! #FYP #nhachay #xuhuong #nhacviet",
        "🎶 {song} ✨ Ai nghe cũng mê! #FYP #nhachay #xuhuong #amnhac",
        "🎵 {song} 💫 Chill cùng giai điệu này! #FYP #nhacviet #xuhuong #viral",
        "🎤 {song} 🔥 Hit quá đi! #FYP #nhachay #amnhac #trending",
        "🎵 {song} 💕 Ngọt ngào quá! #FYP #nhacviet #xuhuong #nhachay",
        "🎶 {song} 🌟 Đỉnh của chóp! #FYP #nhachay #xuhuong #viral",
        "🎵 {song} 🎶 Nghe hoài không chán! #FYP #nhacviet #amnhac #trending",
    ],

    # Queue video thất bại — tự thêm khi upload lỗi
    "retry_queue": [],

    # Đã hoàn thành setup wizard chưa
    "setup_done": False,

    # ── Upload mode settings ──────────────────────────────────────────────────
    # headless_mode=True  → Chrome ẩn hoàn toàn khi auto upload (không thấy cửa sổ)
    # headless_mode=False → Chrome hiện lên (dùng khi debug hoặc upload thủ công)
    "headless_mode": True,

    # TikTok Content Posting API credentials (tuỳ chọn — cần đăng ký developer app)
    # Xem: https://developers.tiktok.com/
    "tiktok_client_key":    "",
    "tiktok_client_secret": "",
    "tiktok_access_token":  "",
    "tiktok_refresh_token": "",

    # ── OpenAI / ChatGPT API ──────────────────────────────────────────────────
    # Lấy key tại: https://platform.openai.com/api-keys
    "openai_api_key": "",
    "ai_model":       "gpt-4o-mini",   # gpt-4o-mini (rẻ) | gpt-4o (mạnh hơn)
    "ai_caption_enabled": False,       # Tắt mặc định đến khi user nhập key

    # ── Google Gemini API (MIỄN PHÍ) ─────────────────────────────────────────
    # Lấy key miễn phí tại: https://aistudio.google.com/apikey
    # Free tier: 15 req/min, 1500 req/ngày — không cần billing
    "gemini_api_key": "",
}


# ── Config class ──────────────────────────────────────────────────────────────

class Config:
    """Singleton config — load/save/get/set."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
            cls._instance._load()
        return cls._instance

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self):
        self._data = dict(_DEFAULTS)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except Exception:
                pass

    def save(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def reload(self):
        self._load()

    # ── Get / Set ─────────────────────────────────────────────────────────────

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    def update(self, d: dict):
        self._data.update(d)
        self.save()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def setup_done(self) -> bool:
        return self._data.get("setup_done", False)

    @property
    def video_base_path(self) -> str:
        return self._data.get("video_base_path", "")

    @property
    def schedule_time(self) -> str:
        return self._data.get("schedule_time", "10:00")

    @property
    def accounts(self) -> list:
        return self._data.get("accounts", [])

    @property
    def active_account(self) -> dict | None:
        accs = self.accounts
        idx  = self._data.get("active_account", 0)
        return accs[idx] if accs and idx < len(accs) else None

    def profile_path(self, account: dict) -> Path:
        """Trả về absolute path của profile folder cho account."""
        rel = account.get("profile_dir", "")
        return PROFILES_DIR / rel

    # ── Accounts management ───────────────────────────────────────────────────

    def add_account(self, name: str) -> dict:
        """Tạo account mới, tự tạo folder profile."""
        safe_name   = "".join(c if c.isalnum() else "_" for c in name)
        profile_dir = safe_name
        (PROFILES_DIR / profile_dir).mkdir(parents=True, exist_ok=True)
        account = {"name": name, "profile_dir": profile_dir}
        accs    = self.accounts
        accs.append(account)
        self.set("accounts", accs)
        return account

    def remove_account(self, idx: int):
        accs = self.accounts
        if 0 <= idx < len(accs):
            accs.pop(idx)
            self.set("accounts", accs)

    def set_active(self, idx: int):
        self.set("active_account", idx)

    # ── Retry queue ───────────────────────────────────────────────────────────

    def push_retry(self, video_path: str, caption: str, error: str):
        q = self._data.get("retry_queue", [])
        # Không thêm trùng
        if not any(r["video_path"] == video_path for r in q):
            q.append({"video_path": video_path, "caption": caption, "error": error})
        self.set("retry_queue", q)

    def pop_retry(self, video_path: str):
        q = [r for r in self._data.get("retry_queue", []) if r["video_path"] != video_path]
        self.set("retry_queue", q)

    def clear_retry(self):
        self.set("retry_queue", [])

    @property
    def headless_mode(self) -> bool:
        return self._data.get("headless_mode", True)

    @property
    def openai_api_key(self) -> str:
        return self._data.get("openai_api_key", "")

    @property
    def ai_model(self) -> str:
        return self._data.get("ai_model", "gpt-4o-mini")

    @property
    def ai_caption_enabled(self) -> bool:
        return bool(self._data.get("ai_caption_enabled", False))

    @property
    def gemini_api_key(self) -> str:
        return self._data.get("gemini_api_key", "")

    @property
    def retry_queue(self) -> list:
        return self._data.get("retry_queue", [])


# Singleton instance
cfg = Config()
