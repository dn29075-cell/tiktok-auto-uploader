"""
core/ai_caption.py — Tích hợp OpenAI API tạo caption TikTok.

Flow:
  1. detect_song_from_filename()  → tên bài hát từ tên file
  2. generate_caption_ai()        → gọi GPT viết caption viral
  3. build_caption_with_ai()      → gộp 2 bước, fallback về template nếu lỗi

Yêu cầu:
  pip install openai
  API key từ: https://platform.openai.com/api-keys
  (Tài khoản ChatGPT Plus cũng có thể tạo key tại đây)
"""

import re
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
# 1. Trích tên bài hát từ tên file
# ══════════════════════════════════════════════════════════════════════════════

def detect_song_from_filename(filepath: str) -> str:
    """
    Cố gắng trích tên bài hát từ tên file video.

    Ví dụ:
      "Son Tung - Chung Ta Cua Hien Tai.mp4"  → "Chung Ta Cua Hien Tai"
      "Taylor Swift - Shake It Off [MV].mp4"  → "Shake It Off"
      "01. Bai hat dep.mp4"                   → "Bai hat dep"
      "video_20240101_abc.mp4"                → "video_20240101_abc"
    """
    name = Path(filepath).stem  # bỏ .mp4

    # Xóa [Official MV], (Lyric Video), v.v.
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'\(.*?\)', '', name)

    # Prefix số thứ tự "01. " "1 - "
    name = re.sub(r'^\d+[\.\-\s]+', '', name)

    name = name.strip()

    # Nếu có " - " → format "Artist - Title" hoặc "Title - Artist"
    # Lấy phần DÀI HƠN (thường là tên bài hát)
    if ' - ' in name:
        parts = [p.strip() for p in name.split(' - ', 1)]
        name = parts[1] if len(parts[1]) >= len(parts[0]) else parts[0]

    return name.strip() or Path(filepath).stem


# ══════════════════════════════════════════════════════════════════════════════
# 2. Gọi OpenAI API
# ══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """Bạn là chuyên gia viết caption TikTok viral cho thị trường Việt Nam.
Quy tắc bắt buộc:
- Caption ngắn gọn, cuốn hút (tối đa 150 ký tự nội dung chính)
- Kèm 4-6 hashtag trending TikTok Việt (#FYP #nhachay #xuhuong #viral ...)
- Thêm emoji phù hợp tâm trạng bài hát
- Tiếng Việt hoặc mix Anh-Việt tự nhiên
- CHỈ trả về caption hoàn chỉnh, không giải thích, không ngoặc kép"""


def generate_caption_ai(
    song_title: str,
    api_key: str,
    model: str = "gpt-4o-mini",
) -> dict:
    """
    Gọi OpenAI tạo caption TikTok cho bài hát.

    Trả về:
      {"success": True,  "caption": "...", "model": "..."}
      {"success": False, "error":   "...", "caption": ""}
    """
    if not api_key or not api_key.strip():
        return {"success": False, "error": "Chưa nhập API key", "caption": ""}

    key = api_key.strip()
    if not (key.startswith("sk-") or key.startswith("sk-proj-")):
        return {"success": False,
                "error": "API key không hợp lệ (phải bắt đầu bằng sk-)",
                "caption": ""}

    try:
        import openai  # pip install openai
    except ImportError:
        return {"success": False,
                "error": "Chưa cài: pip install openai",
                "caption": ""}

    try:
        client = openai.OpenAI(api_key=key, timeout=25.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": f"Bài hát: {song_title}"},
            ],
            max_tokens=250,
            temperature=0.85,
        )
        caption = resp.choices[0].message.content.strip()
        # Xóa ngoặc kép nếu GPT tự thêm
        caption = caption.strip('"').strip("'")
        return {"success": True, "caption": caption, "model": model}

    except Exception as e:
        err = str(e)
        if "401" in err or "Incorrect API key" in err or "invalid_api_key" in err:
            return {"success": False, "error": "API key sai hoặc hết hạn", "caption": ""}
        if "429" in err:
            return {"success": False, "error": "Rate limit — thử lại sau ít giây", "caption": ""}
        if "model" in err.lower() and "not found" in err.lower():
            return {"success": False, "error": f"Model '{model}' không tồn tại", "caption": ""}
        return {"success": False, "error": err[:150], "caption": ""}


# ══════════════════════════════════════════════════════════════════════════════
# 3. Hàm tổng hợp — dùng trong pipeline và UI
# ══════════════════════════════════════════════════════════════════════════════

def build_caption_with_ai(
    filepath: str,
    api_key: str,
    tpl_idx: int = 0,
    model: str = "gpt-4o-mini",
) -> dict:
    """
    Detect song → gọi AI → trả về caption.
    Fallback về template nếu không có key hoặc AI lỗi.

    Trả về:
      {
        "caption": str,
        "song":    str,
        "source":  "ai" | "template" | "error",
        "error":   str,
      }
    """
    song = detect_song_from_filename(filepath)

    if api_key and api_key.strip():
        result = generate_caption_ai(song, api_key, model)
        if result["success"]:
            return {
                "caption": result["caption"],
                "song":    song,
                "source":  "ai",
                "error":   "",
            }
        # AI thất bại → fallback + thông báo lỗi
        from core.pipeline import build_caption as _tpl
        return {
            "caption": _tpl(song, tpl_idx),
            "song":    song,
            "source":  "error",
            "error":   result["error"],
        }

    # Không có key → template bình thường
    from core.pipeline import build_caption as _tpl
    return {
        "caption": _tpl(song, tpl_idx),
        "song":    song,
        "source":  "template",
        "error":   "",
    }


def check_openai_installed() -> bool:
    """Kiểm tra đã cài openai chưa."""
    try:
        import openai  # noqa
        return True
    except ImportError:
        return False
