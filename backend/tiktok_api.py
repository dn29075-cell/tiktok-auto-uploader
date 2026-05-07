"""
tiktok_api.py — Cookie-based TikTok helper.

Chức năng:
  1. Đọc cookies từ Chrome profile sau khi login (sessionid, tt_csrf_token, ...)
  2. Lưu cookies ra JSON trong profile folder
  3. Kiểm tra cookies còn hạn không (tránh mở browser không cần thiết)
  4. Skeleton cho Official TikTok Content Posting API (nếu muốn dùng trong tương lai)

Tại sao cần:
  - Sau khi user login 1 lần bằng Chrome thật, cookies được lưu vào SQLite
  - Đọc cookies → dùng requests → KHÔNG cần mở Chrome lại
  - Nếu cookies hết hạn → thông báo user cần login lại

Giới hạn:
  - Cookies TikTok thường hết hạn sau 30–60 ngày
  - TikTok internal upload API rất phức tạp (cần JS signing)
    → Nên dùng kết hợp: cookie check + headless browser upload
  - Official API (Content Posting API) cần developer app được duyệt

Cài thêm:
  pip install browser_cookie3 requests
"""

import json
import os
import sqlite3
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional


# ── Cookie file names ─────────────────────────────────────────────────────────

COOKIES_FILE = "tiktok_cookies.json"   # Lưu trong profile folder


# ── Cookie extractor (Chrome profile → JSON) ─────────────────────────────────

def extract_cookies_from_chrome(profile_path: str) -> list[dict]:
    """
    Đọc cookies TikTok từ Chrome profile SQLite database.

    Chrome lưu cookies ở:
      Windows: profile_path/Default/Cookies  (SQLite, encrypted)
      Mac:     profile_path/Default/Cookies  (SQLite, encrypted)

    Trả về list dict [{"name": ..., "value": ..., "domain": ..., ...}]
    hoặc [] nếu thất bại.

    Lưu ý: Chrome phải đóng trước khi đọc (file bị lock khi Chrome chạy).
    """

    # Thử browser_cookie3 trước (handle DPAPI decryption tự động)
    try:
        import browser_cookie3  # type: ignore
        # Chrome mới (v96+) lưu cookies tại Default/Network/Cookies
        cookie_db = Path(profile_path) / "Default" / "Network" / "Cookies"
        if not cookie_db.exists():
            cookie_db = Path(profile_path) / "Default" / "Cookies"
        if cookie_db.exists():
            jar = browser_cookie3.chrome(
                cookie_file=str(cookie_db),
                domain_name=".tiktok.com",
            )
            cookies = []
            for c in jar:
                cookies.append({
                    "name":   c.name,
                    "value":  c.value,
                    "domain": c.domain,
                    "path":   c.path,
                    "secure": c.secure,
                    "expires": c.expires,
                })
            return cookies
    except ImportError:
        pass  # browser_cookie3 chưa cài
    except Exception:
        pass

    # Fallback: đọc SQLite trực tiếp (không decrypt — Chrome 80+ mã hóa giá trị)
    # Chỉ dùng được trên Mac (không có DPAPI) hoặc khi value không encrypted
    try:
        db_path = Path(profile_path) / "Default" / "Network" / "Cookies"
        if not db_path.exists():
            db_path = Path(profile_path) / "Default" / "Cookies"
        if not db_path.exists():
            return []

        # Copy file tránh lock
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy2(str(db_path), tmp.name)

        conn = sqlite3.connect(tmp.name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, value, host_key, path, is_secure, expires_utc "
            "FROM cookies WHERE host_key LIKE '%tiktok.com%'"
        )
        rows = cursor.fetchall()
        conn.close()
        os.unlink(tmp.name)

        cookies = []
        for name, value, domain, path, secure, expires in rows:
            # Nếu value rỗng → encrypted (Windows DPAPI) → bỏ qua
            if value:
                cookies.append({
                    "name":    name,
                    "value":   value,
                    "domain":  domain,
                    "path":    path,
                    "secure":  bool(secure),
                    "expires": expires,
                })
        return cookies

    except Exception:
        return []


def save_cookies(profile_path: str, cookies: list[dict]) -> bool:
    """Lưu cookies ra JSON trong profile folder."""
    try:
        out = Path(profile_path) / COOKIES_FILE
        with open(out, "w", encoding="utf-8") as f:
            json.dump({
                "saved_at": time.time(),
                "cookies":  cookies,
            }, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_cookies(profile_path: str) -> list[dict]:
    """Load cookies từ JSON file."""
    try:
        p = Path(profile_path) / COOKIES_FILE
        if not p.exists():
            return []
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("cookies", [])
    except Exception:
        return []


def check_session_valid(profile_path: str) -> bool:
    """
    Kiểm tra session TikTok còn hợp lệ không bằng cách gửi request đơn giản.
    Trả về True nếu còn đăng nhập, False nếu cần login lại.
    """
    try:
        import requests  # type: ignore
        cookies = load_cookies(profile_path)
        if not cookies:
            cookies = extract_cookies_from_chrome(profile_path)

        session_id = next(
            (c["value"] for c in cookies if c["name"] == "sessionid"), None
        )
        if not session_id:
            return False

        # Ping API đơn giản để kiểm tra session
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
        }
        resp = requests.get(
            "https://www.tiktok.com/api/user/detail/",
            cookies=cookie_dict,
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        return data.get("statusCode", -1) == 0

    except Exception:
        return False


def get_session_info(profile_path: str) -> dict:
    """
    Trả về thông tin cookie session.
    {
      "has_session": bool,
      "session_id": str,
      "saved_at": float,  # timestamp lúc lưu
      "age_days": float,  # bao nhiêu ngày từ lúc lưu
    }
    """
    try:
        p = Path(profile_path) / COOKIES_FILE
        if not p.exists():
            return {"has_session": False}

        with open(p, encoding="utf-8") as f:
            data = json.load(f)

        cookies   = data.get("cookies", [])
        saved_at  = data.get("saved_at", 0)
        age_days  = (time.time() - saved_at) / 86400 if saved_at else 999

        session_id = next(
            (c["value"] for c in cookies if c["name"] == "sessionid"), ""
        )

        return {
            "has_session": bool(session_id),
            "session_id":  session_id[:20] + "..." if session_id else "",
            "saved_at":    saved_at,
            "age_days":    round(age_days, 1),
        }
    except Exception:
        return {"has_session": False}


def extract_and_save(profile_path: str) -> bool:
    """
    Đọc cookies từ Chrome profile và lưu ra JSON.
    Gọi hàm này sau khi user đã login bằng Chrome thật.
    Trả về True nếu tìm được sessionid.
    """
    cookies = extract_cookies_from_chrome(profile_path)
    has_session = any(c["name"] == "sessionid" for c in cookies)
    if has_session:
        save_cookies(profile_path, cookies)
    return has_session


# ── Official TikTok Content Posting API skeleton ──────────────────────────────
# Dùng khi có Client Key + Client Secret từ developers.tiktok.com
#
# Flow:
#   1. User đăng ký app tại: https://developers.tiktok.com/
#   2. Nhận Client Key + Client Secret
#   3. OAuth 2.0: mở browser → user approve → nhận access_token
#   4. Upload video qua /v2/post/publish/video/init/
#
# Hiện tại: chưa implement đầy đủ vì cần app được TikTok duyệt
# Xem thêm: https://developers.tiktok.com/doc/content-posting-api-reference-direct-post/

class TikTokContentAPI:
    """
    TikTok Official Content Posting API.

    Yêu cầu:
      - Đăng ký app tại developers.tiktok.com
      - Được duyệt quyền video.publish
      - Người dùng authorize qua OAuth

    Dùng khi muốn upload HOÀN TOÀN không có browser.
    """

    API_BASE = "https://open.tiktokapis.com/v2"
    AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

    def __init__(self, client_key: str, client_secret: str):
        self.client_key    = client_key
        self.client_secret = client_secret
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.open_id:       Optional[str] = None

    def get_auth_url(self, redirect_uri: str, state: str = "auto") -> str:
        """Tạo URL để user authorize ứng dụng."""
        return (
            f"{self.AUTH_URL}"
            f"?client_key={self.client_key}"
            f"&response_type=code"
            f"&scope=user.info.basic,video.publish"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )

    def exchange_code(self, code: str, redirect_uri: str) -> bool:
        """Đổi authorization code lấy access_token."""
        try:
            import requests
            resp = requests.post(self.TOKEN_URL, data={
                "client_key":    self.client_key,
                "client_secret": self.client_secret,
                "code":          code,
                "grant_type":    "authorization_code",
                "redirect_uri":  redirect_uri,
            })
            data = resp.json()
            if "access_token" in data.get("data", {}):
                d = data["data"]
                self.access_token  = d["access_token"]
                self.refresh_token = d.get("refresh_token")
                self.open_id       = d.get("open_id")
                return True
        except Exception:
            pass
        return False

    def refresh(self) -> bool:
        """Làm mới access_token bằng refresh_token."""
        if not self.refresh_token:
            return False
        try:
            import requests
            resp = requests.post(self.TOKEN_URL, data={
                "client_key":    self.client_key,
                "client_secret": self.client_secret,
                "grant_type":    "refresh_token",
                "refresh_token": self.refresh_token,
            })
            data = resp.json()
            if "access_token" in data.get("data", {}):
                self.access_token  = data["data"]["access_token"]
                self.refresh_token = data["data"].get("refresh_token", self.refresh_token)
                return True
        except Exception:
            pass
        return False

    def upload_video(self, video_path: str, caption: str,
                     privacy: str = "PUBLIC_TO_EVERYONE") -> dict:
        """
        Upload video qua Content Posting API (Direct Post).

        Trả về {"success": bool, "publish_id": str, "error": str}
        """
        if not self.access_token:
            return {"success": False, "error": "Chưa có access token"}

        try:
            import requests

            video_path = Path(video_path)
            video_size = video_path.stat().st_size

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type":  "application/json; charset=UTF-8",
            }

            # Bước 1: Init upload
            init_resp = requests.post(
                f"{self.API_BASE}/post/publish/video/init/",
                headers=headers,
                json={
                    "post_info": {
                        "title":          caption[:2200],
                        "privacy_level":  privacy,
                        "disable_duet":   False,
                        "disable_comment": False,
                        "disable_stitch": False,
                    },
                    "source_info": {
                        "source":     "FILE_UPLOAD",
                        "video_size": video_size,
                        "chunk_size": video_size,
                        "total_chunk_count": 1,
                    },
                },
                timeout=30,
            )
            init_data = init_resp.json()

            if init_data.get("error", {}).get("code") != "ok":
                return {
                    "success": False,
                    "error":   str(init_data.get("error", {})),
                }

            upload_url = init_data["data"]["upload_url"]
            publish_id = init_data["data"]["publish_id"]

            # Bước 2: Upload file
            with open(video_path, "rb") as f:
                video_data = f.read()

            put_resp = requests.put(
                upload_url,
                data=video_data,
                headers={
                    "Content-Type":       "video/mp4",
                    "Content-Length":     str(video_size),
                    "Content-Range":      f"bytes 0-{video_size - 1}/{video_size}",
                },
                timeout=300,
            )

            if put_resp.status_code not in (200, 201, 206):
                return {
                    "success": False,
                    "error":   f"Upload failed: HTTP {put_resp.status_code}",
                }

            return {"success": True, "publish_id": publish_id}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def check_status(self, publish_id: str) -> dict:
        """Kiểm tra trạng thái video vừa publish."""
        if not self.access_token:
            return {"status": "error"}
        try:
            import requests
            resp = requests.post(
                f"{self.API_BASE}/post/publish/status/fetch/",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={"publish_id": publish_id},
                timeout=15,
            )
            return resp.json().get("data", {})
        except Exception:
            return {"status": "error"}
