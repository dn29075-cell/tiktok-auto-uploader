"""
shazam_recognizer.py — Nhận dạng bài hát từ video/audio.

Ưu tiên:
  1. AudD API (nếu có API key)
  2. Shazam (shazamio — miễn phí, không cần key)

Yêu cầu: ffmpeg trong PATH để trích xuất audio từ video.
"""

import os
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


# ── FFmpeg path (tự tìm kể cả khi chưa add vào PATH) ────────────────────────

_FFMPEG_EXTRA_PATHS = [
    # WinGet install path
    r"C:\Users\Admin\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
]


def _find_ffmpeg() -> str:
    """Trả về lệnh ffmpeg có thể dùng được (tên hoặc đường dẫn đầy đủ)."""
    import glob

    # 1. Thử PATH trước
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return "ffmpeg"
    except Exception:
        pass

    # 2. Tìm trong các đường dẫn cứng
    for p in _FFMPEG_EXTRA_PATHS:
        if Path(p).exists():
            return p

    # 3. Glob tìm trong WinGet packages
    patterns = [
        r"C:\Users\*\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\ffmpeg.exe",
        r"C:\ProgramData\**\ffmpeg.exe",
    ]
    for pat in patterns:
        found = glob.glob(pat, recursive=True)
        if found:
            return found[0]

    return "ffmpeg"  # fallback — sẽ báo lỗi rõ ràng hơn


_FFMPEG = _find_ffmpeg()


# ── Audio extraction ──────────────────────────────────────────────────────────

def _ffmpeg_available() -> bool:
    try:
        subprocess.run(
            [_FFMPEG, "-version"],
            capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def extract_audio(video_path: str, duration: int = 20) -> Optional[str]:
    """
    Trích 20 giây đầu của video ra file mp3 tạm.
    Trả về đường dẫn file tạm, hoặc None nếu thất bại.
    """
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()

        cmd = [
            _FFMPEG, "-y",
            "-i", str(video_path),
            "-t", str(duration),
            "-vn",
            "-acodec", "libmp3lame",
            "-ar", "44100",
            "-ab", "128k",
            tmp.name,
            "-loglevel", "quiet",
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode == 0 and Path(tmp.name).stat().st_size > 0:
            return tmp.name
    except Exception:
        pass
    return None


# ── AudD API ──────────────────────────────────────────────────────────────────

def _recognize_audd(audio_path: str, api_key: str) -> dict:
    """Nhận dạng qua AudD REST API (api.audd.io)."""
    try:
        import requests
        with open(audio_path, "rb") as f:
            resp = requests.post(
                "https://api.audd.io/",
                data={"api_token": api_key},
                files={"file": ("audio.mp3", f, "audio/mpeg")},
                timeout=30,
            )
        r = resp.json()
        if r.get("status") == "success" and r.get("result"):
            res = r["result"]
            return {
                "success": True,
                "title": res.get("title", ""),
                "artist": res.get("artist", ""),
                "album": res.get("album", ""),
            }
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": False, "error": "AudD: không nhận dạng được"}


# ── Shazam (shazamio) ─────────────────────────────────────────────────────────

def _recognize_shazam(audio_path: str) -> dict:
    """Nhận dạng qua Shazam (không cần API key)."""
    try:
        from shazamio import Shazam  # type: ignore

        async def _run():
            shazam = Shazam()
            # Thử method recognize (v0.4+) trước, fallback recognize_song (v0.3)
            try:
                return await shazam.recognize(audio_path)
            except AttributeError:
                return await shazam.recognize_song(audio_path)

        # Tạo event loop mới cho thread này (tránh conflict với thread khác)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_run())
        finally:
            loop.close()

        if result and result.get("track"):
            track = result["track"]
            return {
                "success": True,
                "title": track.get("title", ""),
                "artist": track.get("subtitle", ""),
                "album": "",
            }
    except ImportError:
        return {"success": False, "error": "Chưa cài shazamio (pip install shazamio)"}
    except Exception as e:
        return {"success": False, "error": f"Shazam: {e}"}
    return {"success": False, "error": "Shazam: không nhận dạng được"}


# ── Main entry ────────────────────────────────────────────────────────────────

def recognize(video_path: str, audd_api_key: str = "") -> dict:
    """
    Nhận dạng bài hát từ file video.

    Returns:
        {
            "success": bool,
            "title": str,       # Tên bài hát
            "artist": str,      # Ca sĩ / nhóm
            "album": str,
            "error": str,       # chỉ có khi success=False
            "source": str,      # "audd" | "shazam"
        }
    """
    audio_path = None
    cleanup = False

    try:
        # 1. Trích audio nếu ffmpeg có sẵn
        if _ffmpeg_available():
            audio_path = extract_audio(str(video_path))
            if audio_path:
                cleanup = True
        else:
            # Fallback: truyền thẳng file video cho shazamio
            audio_path = str(video_path)

        if not audio_path:
            return {"success": False, "error": "Không trích được audio (ffmpeg lỗi)"}

        # 2. Thử AudD trước nếu có key
        if audd_api_key:
            result = _recognize_audd(audio_path, audd_api_key)
            if result.get("success"):
                result["source"] = "audd"
                return result

        # 3. Shazam
        result = _recognize_shazam(audio_path)
        if result.get("success"):
            result["source"] = "shazam"
            return result

        return result

    finally:
        if cleanup and audio_path and Path(audio_path).exists():
            try:
                os.unlink(audio_path)
            except Exception:
                pass


def song_display(info: dict) -> str:
    """Tạo chuỗi hiển thị 'Tên - Ca sĩ' từ kết quả nhận dạng."""
    if not info.get("success"):
        return ""
    title = info.get("title", "").strip()
    artist = info.get("artist", "").strip()
    if title and artist:
        return f"{title} - {artist}"
    return title or artist
