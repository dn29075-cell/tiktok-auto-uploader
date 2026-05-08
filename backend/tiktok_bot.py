"""
tiktok_bot.py — Tự động upload video lên TikTok Studio bằng Playwright.

Flow: Mở TikTok Studio → chọn file → chờ xử lý → điền caption → Post.
Hỗ trợ nhiều tài khoản qua persistent profile folder riêng biệt.
"""

import os
import asyncio
import subprocess
import time
from pathlib import Path
from typing import Optional, Callable


# ─── Constants ───────────────────────────────────────────────────────────────

UPLOAD_URL      = "https://www.tiktok.com/tiktokstudio/upload"
UPLOAD_TIMEOUT  = 300_000   # 5 phút cho upload + xử lý video
POST_TIMEOUT    = 60_000    # 1 phút cho nút Post
NAV_TIMEOUT     = 60_000    # 1 phút cho navigation

# Anti-bot args — giả lập trình duyệt thật, tránh bị TikTok detect
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--start-maximized",
    "--lang=vi-VN",
    "--accept-lang=vi-VN,vi;q=0.9,en-US;q=0.8",
]

# User agent của Chrome thật trên Windows
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _log(msg: str, fn: Optional[Callable]) -> None:
    if fn:
        try:
            fn(msg)
        except (UnicodeEncodeError, UnicodeDecodeError):
            # Windows console (cp1252) không support tiếng Việt → fallback ASCII
            fn(msg.encode("ascii", errors="replace").decode("ascii"))


def _find_chrome() -> Optional[str]:
    """
    Tìm đường dẫn Chrome.exe / Google Chrome trên Windows và Mac.
    Trả về None nếu không tìm thấy.
    """
    import platform
    system = platform.system()

    if system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
        ]
    elif system == "Darwin":  # macOS
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:  # Linux
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ]

    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _close_chrome_with_profile(profile_path: str, log=None) -> None:
    """
    Đóng Chrome process đang dùng profile_path (nếu có).
    Tránh lỗi 'Opening in current browser session' khi Playwright
    cố mở Chrome với profile đang bị lock bởi instance khác.
    """
    profile_path = str(Path(profile_path).resolve()).lower()
    try:
        import psutil
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if "chrome" not in name:
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if profile_path.replace("\\", "/") in cmdline.replace("\\", "/"):
                    _log(f"[TikTok] Dong Chrome dang dung profile: PID {proc.pid}", log)
                    proc.terminate()
            except Exception:
                pass
    except ImportError:
        pass  # psutil chưa cài — bỏ qua


def kill_existing_chromium() -> None:
    """Kill CHỈ Playwright Chromium (ms-playwright), KHÔNG kill Chrome thường của user."""
    try:
        import psutil
        for proc in psutil.process_iter(['name', 'exe']):
            try:
                exe  = (proc.info.get('exe')  or '').lower().replace('\\', '/')
                name = (proc.info.get('name') or '').lower()
                # Chỉ kill process trong thư mục ms-playwright (Playwright bundled)
                if 'ms-playwright' in exe and 'chrome' in name:
                    proc.kill()
            except Exception:
                pass
    except ImportError:
        # psutil không cài → chỉ kill chromium.exe (không phải chrome.exe người dùng)
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chromium.exe"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass


# ─── Login setup ─────────────────────────────────────────────────────────────

async def _apply_stealth(page) -> None:
    """Inject JS để ẩn dấu hiệu automation khỏi TikTok."""
    await page.add_init_script("""
        // Xóa webdriver flag
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // Fake plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });

        // Fake languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['vi-VN', 'vi', 'en-US', 'en'],
        });

        // Fake chrome runtime
        window.chrome = { runtime: {} };

        // Fake permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);
    """)


def setup_login_direct(profile_path: str, log: Optional[Callable] = None) -> None:
    """
    Mở Chrome THẬT qua subprocess — KHÔNG dùng Playwright/CDP.

    Tại sao subprocess thay vì Playwright channel='chrome'?
    - Playwright kết nối Chrome qua DevTools Protocol (CDP)
    - CDP để lại fingerprint trong JS (performance.memory, navigator.webdriver...)
    - TikTok phát hiện CDP → hiện QR "độc" → scan không được
    - subprocess.Popen mở Chrome y hệt người dùng click icon → KHÔNG có CDP
    - TikTok thấy Chrome bình thường → QR hoạt động 100%
    """
    profile_dir = str(Path(profile_path))
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    chrome_exe = _find_chrome()

    if chrome_exe:
        _log(f"[TikTok] Mo Chrome: {chrome_exe}", log)
        _log("[TikTok] >>> Scan QR bang app TikTok tren dien thoai <<<", log)
        _log("[TikTok] Dong Chrome sau khi dang nhap xong!", log)

        cmd = [
            chrome_exe,
            f"--user-data-dir={profile_dir}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "--lang=vi-VN",
            # KHÔNG có --disable-blink-features hay --remote-debugging-port
            # → Chrome hoàn toàn sạch, TikTok không phát hiện được
            "https://www.tiktok.com/login/qrcode",
        ]

        try:
            proc = subprocess.Popen(cmd)
            proc.wait()  # Chờ user đóng Chrome
            _log("[TikTok] Chrome da dong. Dang nhap hoan thanh!", log)
        except Exception as e:
            _log(f"[TikTok] Loi mo Chrome: {e}", log)
            raise

    else:
        # Chrome không tìm thấy → thông báo rõ ràng
        _log("[TikTok] KHONG TIM THAY Chrome.exe!", log)
        _log("[TikTok] Cai Google Chrome tai: https://www.google.com/chrome/", log)
        raise RuntimeError(
            "Không tìm thấy Google Chrome trên máy này.\n"
            "Tải và cài Chrome tại: https://www.google.com/chrome/"
        )


async def setup_login(profile_path: str, log: Optional[Callable] = None) -> None:
    """Async wrapper — gọi setup_login_direct trong thread riêng."""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, setup_login_direct, profile_path, log)


# ─── Upload ──────────────────────────────────────────────────────────────────

async def upload_video(
    video_path:   str,
    caption:      str,
    profile_path: str,
    upload_url:   str = UPLOAD_URL,
    log: Optional[Callable] = None,
    headless:     bool = False,
) -> bool:
    """
    Upload video_path lên TikTok Studio với caption đã cho.

    Args:
        video_path   : đường dẫn file video cần upload
        caption      : caption tiếng Việt
        profile_path : thư mục profile Playwright (persistent context)
        upload_url   : URL TikTok Studio upload
        log          : callback ghi log
        headless     : True = Chrome chạy ẩn (không thấy cửa sổ) — dùng cho auto mode

    Returns:
        True nếu thành công, False nếu thất bại.
    """
    from playwright.async_api import async_playwright

    video_path   = str(Path(video_path).resolve())
    profile_path = str(Path(profile_path))
    Path(profile_path).mkdir(parents=True, exist_ok=True)

    mode_str = "ẩn (headless)" if headless else "hiển thị"
    _log(f"[TikTok] Bắt đầu upload: {Path(video_path).name} [{mode_str}]", log)

    async with async_playwright() as p:
        # ── QUAN TRỌNG: Phải dùng Chrome THẬT, KHÔNG dùng Playwright Chromium ──
        # Lý do:
        #   - Profile được tạo bởi Chrome thật (subprocess login)
        #   - Playwright Chromium (ms-playwright) khác version → profile bị conflict → crash
        #   - ignore_default_args=["--enable-automation"] giữ lại --remote-debugging-pipe
        #     (kênh Playwright dùng để điều khiển Chrome) nhưng bỏ flag automation

        chrome_exe = _find_chrome()
        if not chrome_exe:
            raise RuntimeError(
                "Không tìm thấy Google Chrome!\n"
                "Tải tại: https://www.google.com/chrome/"
            )

        _log(f"[TikTok] Dung Chrome: {chrome_exe}", log)

        # ── Đóng Chrome đang chạy cùng profile (tránh conflict) ──────────────
        _close_chrome_with_profile(profile_path, log)
        await asyncio.sleep(1.5)

        # ── Off-screen mode (thay thế headless) ──────────────────────────────
        # TikTok chặn Chrome --headless (trả về 403 Forbidden)
        # Giải pháp: đặt cửa sổ ở vị trí âm (-2400, 0) → ẩn khỏi màn hình
        # Chrome chạy bình thường, TikTok không phát hiện được, user không thấy
        if headless:
            window_args = [
                "--window-position=-2400,0",   # Đẩy ra ngoài màn hình bên trái
                "--window-size=1280,900",       # Kích thước bình thường
            ]
            _log("[TikTok] Chế độ ẩn: Chrome chạy ngoài màn hình", log)
        else:
            window_args = ["--start-maximized"]

        browser = await p.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False,   # LUÔN False — TikTok block headless=True (403)
            channel="chrome",
            args=[
                *window_args,
                "--lang=vi-VN",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
            no_viewport=True,
            slow_mo=500,
            # CHỈ xóa --enable-automation, GIỮ --remote-debugging-pipe
            ignore_default_args=["--enable-automation"],
        )

        try:
            page = browser.pages[0] if browser.pages else await browser.new_page()
            await _apply_stealth(page)

            # ── 1. Mở trang upload ────────────────────────────────────────────
            _log(f"[TikTok] Mở {upload_url}...", log)
            await page.goto(upload_url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # ── 2. Chọn file ─────────────────────────────────────────────────
            _log("[TikTok] Chon file video...", log)

            upload_success = False

            # Bước 2a: Thử set_input_files trực tiếp vào input[type=file]
            # (input này thường bị ẩn nhưng vẫn nhận file)
            for ctx in [page, *page.frames]:
                if upload_success:
                    break
                try:
                    fi = ctx.locator("input[type='file']").first
                    await fi.wait_for(state="attached", timeout=8_000)
                    await fi.set_input_files(video_path)
                    upload_success = True
                    _log("[TikTok] Da chon file qua input[type=file].", log)
                except Exception:
                    pass

            # Bước 2b: Nếu không được → click nút "Chọn video" / "Select file" / v.v.
            if not upload_success:
                _log("[TikTok] Thu click nut Chon video...", log)

                # TikTok Studio VN dùng "Chọn video", EN dùng "Select video" / "Upload"
                btn_texts = [
                    "Chọn video", "Chon video",
                    "Select video", "Select file",
                    "Upload video", "Upload",
                    "Tải lên", "Tai len",
                ]
                clicked = False
                for txt in btn_texts:
                    try:
                        btn = page.get_by_text(txt, exact=False).first
                        if await btn.is_visible():
                            await btn.click()
                            _log(f"[TikTok] Da click nut: '{txt}'", log)
                            await page.wait_for_timeout(1500)
                            clicked = True
                            break
                    except Exception:
                        pass

                # Sau khi click → set file vào input vừa xuất hiện
                if clicked:
                    for ctx in [page, *page.frames]:
                        try:
                            fi = ctx.locator("input[type='file']").first
                            await fi.wait_for(state="attached", timeout=5_000)
                            await fi.set_input_files(video_path)
                            upload_success = True
                            _log("[TikTok] Da chon file sau khi click nut.", log)
                            break
                        except Exception:
                            pass

            if not upload_success:
                await _save_screenshot(page, "tiktok_select_fail", log)
                raise RuntimeError(
                    "Khong tim duoc o chon file tren TikTok Studio.\n"
                    "Co the TikTok chua dang nhap hoac giao dien da thay doi.\n"
                    "Xem screenshot trong thu muc debug_screenshots."
                )

            _log("[TikTok] File da chon, cho TikTok xu ly...", log)

            # ── 3. Chờ video xử lý xong ──────────────────────────────────────
            # TikTok hiện thanh progress khi upload + process
            # Đợi nút "Post" hoặc "Đăng" xuất hiện và enable
            post_selector = _find_post_button_selector()

            _log("[TikTok] Đang chờ TikTok xử lý video (tối đa 5 phút)...", log)
            start = time.monotonic()

            post_btn = None
            deadline = time.monotonic() + UPLOAD_TIMEOUT / 1000

            while time.monotonic() < deadline:
                await page.wait_for_timeout(3000)
                elapsed = int(time.monotonic() - start)

                # Kiểm tra trong trang chính + iframe
                btn = await _find_post_button(page)
                if btn:
                    post_btn = btn
                    _log(f"[TikTok] Video đã xử lý xong sau {elapsed}s!", log)
                    break

                # Log tiến trình
                pct = await _get_upload_progress(page)
                if pct:
                    _log(f"[TikTok] Đang xử lý {pct}... ({elapsed}s)", log)
                else:
                    _log(f"[TikTok] Đang xử lý... ({elapsed}s)", log)

            if not post_btn:
                await _save_screenshot(page, "tiktok_timeout", log)
                raise TimeoutError("TikTok không xử lý xong video trong thời gian chờ.")

            # ── 4. Điền caption ───────────────────────────────────────────────
            _log("[TikTok] Điền caption...", log)
            await _fill_caption(page, caption, log)

            # ── 5. Nhấn Post ─────────────────────────────────────────────────
            _log("[TikTok] Nhấn Post...", log)
            # Dismiss bất kỳ modal nào đang chặn trước khi click
            await _dismiss_all_overlays(page, log)
            await page.wait_for_timeout(500)
            try:
                await post_btn.click()
            except Exception:
                # Nếu vẫn bị chặn → force click qua JS
                _log("[TikTok] Post bị chặn → thử force click...", log)
                await post_btn.evaluate("el => el.click()")
            await page.wait_for_timeout(2000)

            # ── 5b. Xử lý dialog sau Post ────────────────────────────────────
            # TikTok đôi khi hiện thêm dialog "Post Now" / "Schedule" sau click
            await _handle_post_dialogs(page, log)

            # ── 5c. Chờ và xác nhận ──────────────────────────────────────────
            # Chờ tối đa 30s để URL đổi hoặc toast xuất hiện
            posted = False
            for _ in range(10):   # 10 × 3s = 30s
                await page.wait_for_timeout(3000)
                if await _confirm_post_success(page, log):
                    posted = True
                    break
                # Thử xử lý dialog lại nếu chưa đăng
                await _handle_post_dialogs(page, log)

            if posted:
                _log(f"[TikTok] ✅ Upload thành công: {Path(video_path).name}", log)
            else:
                # Chụp screenshot để debug
                await _save_screenshot(page, "tiktok_post_check", log)
                _log(f"[TikTok] ⚠️ Không xác nhận được — xem screenshot trong data/debug_screenshots", log)

            await page.wait_for_timeout(2000)
            return posted

        except Exception as e:
            await _save_screenshot(page, "tiktok_error", log)
            _log(f"[TikTok] ❌ Lỗi upload: {e}", log)
            raise

        finally:
            await browser.close()


# ─── Clipboard helper (Windows — Unicode/tiếng Việt an toàn) ─────────────────

def _set_clipboard_text(text: str) -> None:
    """
    Đặt text vào Windows clipboard dùng ctypes.
    Hỗ trợ đầy đủ Unicode / tiếng Việt — không cần thư viện ngoài.
    """
    import ctypes
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE  = 0x0002

    raw  = (text + "\0").encode("utf-16-le")
    hMem = ctypes.windll.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
    ptr  = ctypes.windll.kernel32.GlobalLock(hMem)
    ctypes.memmove(ptr, raw, len(raw))
    ctypes.windll.kernel32.GlobalUnlock(hMem)

    if ctypes.windll.user32.OpenClipboard(None):
        ctypes.windll.user32.EmptyClipboard()
        ctypes.windll.user32.SetClipboardData(CF_UNICODETEXT, hMem)
        ctypes.windll.user32.CloseClipboard()
    else:
        raise RuntimeError("Không mở được clipboard")


# ─── Caption helpers ──────────────────────────────────────────────────────────

async def _dismiss_tux_modal(page, log: Optional[Callable]) -> bool:
    """
    Đóng TUXModal-overlay — loại modal mới của TikTok (2025+).
    Modal này dùng data-floating-ui-portal và chặn toàn bộ pointer events.
    """
    try:
        modal = page.locator(".TUXModal-overlay, [class*='TUXModal']").first
        if not await modal.is_visible():
            return False

        _log("[TikTok] Phát hiện TUXModal — đang xử lý...", log)

        # 1. Thử click các nút xác nhận bên trong modal
        confirm_texts = [
            "Post now", "Đăng ngay", "Đăng",
            "Continue", "Tiếp tục",
            "Confirm", "Xác nhận",
            "OK", "Got it", "Hiểu rồi",
            "Proceed", "Đồng ý",
            "Upload", "Tải lên",
            "Next", "Tiếp theo",
            "Allow", "Cho phép",
            "Done", "Xong",
        ]
        for text in confirm_texts:
            try:
                btn = modal.get_by_role("button", name=text, exact=False).first
                if await btn.is_visible():
                    _log(f"[TikTok] TUXModal — click '{text}'", log)
                    await btn.click()
                    await page.wait_for_timeout(1000)
                    return True
            except Exception:
                pass

        # 2. Thử click bất kỳ button visible nào trong modal
        try:
            btns = await modal.locator("button").all()
            for btn in reversed(btns):   # reversed: nút chính thường ở cuối
                try:
                    if await btn.is_visible() and await btn.is_enabled():
                        txt = (await btn.inner_text()).strip()
                        _log(f"[TikTok] TUXModal — click button: '{txt}'", log)
                        await btn.click()
                        await page.wait_for_timeout(1000)
                        return True
                except Exception:
                    pass
        except Exception:
            pass

        # 3. Thử Escape
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(600)

        # 4. JS ẩn cứng modal (last resort)
        await page.evaluate("""
            document.querySelectorAll(
                '.TUXModal-overlay, [class*="TUXModal"], [data-floating-ui-portal]'
            ).forEach(e => {
                e.style.display = 'none';
                e.style.pointerEvents = 'none';
                e.style.visibility = 'hidden';
            });
            document.body.style.overflow = '';
        """)
        await page.wait_for_timeout(400)
        _log("[TikTok] TUXModal — đã ẩn qua JS.", log)
        return True

    except Exception:
        return False


async def _dismiss_all_overlays(page, log: Optional[Callable]) -> None:
    """Đóng TẤT CẢ các overlay/modal đang chặn (TUX + joyride + generic)."""
    # TUXModal (mới nhất)
    await _dismiss_tux_modal(page, log)

    # Generic floating portals
    try:
        await page.evaluate("""
            document.querySelectorAll('[data-floating-ui-portal]').forEach(el => {
                const style = getComputedStyle(el);
                if (style.pointerEvents !== 'none') {
                    el.style.pointerEvents = 'none';
                    el.style.display = 'none';
                }
            });
        """)
    except Exception:
        pass


async def _dismiss_tutorial_overlay(page, log: Optional[Callable]) -> None:
    """
    Đóng popup hướng dẫn TikTok Studio (react-joyride overlay).
    Overlay này chặn click vào ô caption — phải xóa trước khi điền.
    """
    # Xử lý TUXModal trước (ưu tiên cao hơn)
    await _dismiss_tux_modal(page, log)

    try:
        overlay = page.locator("[data-test-id='overlay'], .react-joyride__overlay").first
        is_visible = await overlay.is_visible()
        if is_visible:
            _log("[TikTok] Phát hiện tutorial overlay — đang đóng...", log)

            # 1. Thử nhấn Escape
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)

            # 2. Thử click nút Skip/Close trong joyride nếu có
            for skip_text in ["Skip", "Bỏ qua", "Close", "×", "Got it", "Hiểu rồi"]:
                try:
                    btn = page.get_by_text(skip_text, exact=False).first
                    if await btn.is_visible():
                        await btn.click()
                        await page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            # 3. Dùng JavaScript xóa cứng overlay khỏi DOM
            await page.evaluate("""
                const portal = document.getElementById('react-joyride-portal');
                if (portal) portal.remove();
                document.querySelectorAll('[data-test-id="overlay"], .react-joyride__overlay').forEach(e => e.remove());
                document.body.style.pointerEvents = '';
                document.body.style.overflow = '';
            """)
            await page.wait_for_timeout(500)
            _log("[TikTok] Đã đóng tutorial overlay.", log)
    except Exception:
        pass


async def _fill_caption(page, caption: str, log: Optional[Callable]) -> None:
    """
    Điền caption vào TikTok Studio — hỗ trợ đầy đủ tiếng Việt.

    KHÔNG dùng type() vì keyboard simulation đi qua IME của Windows
    → tiếng Việt bị vỡ font (ê→e^, ă→a^, ơ→o+, v.v.)

    Ưu tiên:
      1. JS execCommand('insertText') — nhét trực tiếp Unicode vào DOM
      2. Clipboard Ctrl+V         — copy qua ctypes, paste vào editor
      3. fill()                   — fallback cho textarea thường
    """
    import json as _json

    # Đóng tutorial overlay trước khi điền (nếu có)
    await _dismiss_tutorial_overlay(page, log)

    selectors = [
        "[data-testid='caption-input']",
        "div[contenteditable='true']",
        "div[placeholder*='mô tả']",
        "div[placeholder*='Mô tả']",
        "div[placeholder*='caption']",
        "div[placeholder*='Caption']",
        "div[placeholder*='nội dung']",
        "textarea[placeholder*='caption']",
        "textarea[placeholder*='Caption']",
        "textarea[placeholder*='description']",
        ".DraftEditor-root",
        ".public-DraftEditor-content",
        "[contenteditable='true']",
    ]

    caption_field = None

    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.wait_for(timeout=5000)
            caption_field = el
            break
        except Exception:
            continue

    if not caption_field:
        for frame in page.frames:
            for sel in selectors:
                try:
                    el = frame.locator(sel).first
                    await el.wait_for(timeout=3000)
                    caption_field = el
                    break
                except Exception:
                    continue
            if caption_field:
                break

    if not caption_field:
        _log("[TikTok] ⚠️ Không tìm được ô caption — tiếp tục không có caption.", log)
        return

    # ── Focus ─────────────────────────────────────────────────────────────────
    try:
        await caption_field.evaluate("el => el.click()")
    except Exception:
        await caption_field.click(force=True)
    await page.wait_for_timeout(400)

    # ── Xóa nội dung cũ ───────────────────────────────────────────────────────
    try:
        await caption_field.evaluate("""el => {
            el.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);
        }""")
    except Exception:
        await caption_field.press("Control+a")
        await page.wait_for_timeout(150)
        await caption_field.press("Delete")
    await page.wait_for_timeout(200)

    # ── Method 1: JS execCommand('insertText') ────────────────────────────────
    # json.dumps() escape Unicode an toàn → không bị lỗi quote hay ký tự đặc biệt
    inserted = False
    try:
        caption_json = _json.dumps(caption)   # → "Người đẹp..."
        ok = await caption_field.evaluate(
            f"el => {{ el.focus(); return document.execCommand('insertText', false, {caption_json}); }}"
        )
        if ok:
            inserted = True
            _log(f"[TikTok] ✅ Caption OK [execCommand]: {caption[:60]}...", log)
    except Exception as exc:
        _log(f"[TikTok] execCommand lỗi ({exc}) — thử clipboard...", log)

    # ── Method 2: Clipboard Ctrl+V ────────────────────────────────────────────
    if not inserted:
        try:
            _set_clipboard_text(caption)         # copy vào Windows clipboard
            await caption_field.focus()
            await page.wait_for_timeout(200)
            await page.keyboard.press("Control+v")
            await page.wait_for_timeout(400)
            inserted = True
            _log(f"[TikTok] ✅ Caption OK [clipboard]: {caption[:60]}...", log)
        except Exception as exc:
            _log(f"[TikTok] Clipboard lỗi ({exc}) — thử fill()...", log)

    # ── Method 3: fill() (textarea thường) ───────────────────────────────────
    if not inserted:
        try:
            await caption_field.fill(caption)
            inserted = True
            _log(f"[TikTok] ✅ Caption OK [fill]: {caption[:60]}...", log)
        except Exception as exc:
            _log(f"[TikTok] fill() lỗi ({exc})", log)

    if not inserted:
        _log("[TikTok] ⚠️ Không điền được caption (tất cả method đều lỗi).", log)


# ─── Post button helpers ──────────────────────────────────────────────────────

def _find_post_button_selector() -> str:
    return "button:has-text('Post'), button:has-text('Đăng'), button:has-text('Publish')"


async def _find_post_button(page):
    """
    Tìm nút đăng bài (enabled) trong trang hoặc iframe.
    TikTok VN: "Đăng" | TikTok EN: "Post" | "Publish"
    """
    # Thứ tự ưu tiên: Đăng (VN) trước, rồi EN
    texts = ["Đăng", "Post", "Publish", "Đăng video", "Post video"]

    for ctx in [page, *page.frames]:
        for text in texts:
            try:
                # Tìm button chính xác
                btn = ctx.get_by_role("button", name=text, exact=True).last
                if await btn.is_visible() and await btn.is_enabled():
                    return btn
            except Exception:
                pass
            try:
                # Tìm button chứa text (không exact)
                btn = ctx.locator(f"button:has-text('{text}')").last
                if await btn.is_visible() and await btn.is_enabled():
                    return btn
            except Exception:
                pass

    return None


async def _get_upload_progress(page) -> str:
    """Lấy % tiến trình upload nếu có."""
    try:
        # Tìm element hiển thị phần trăm
        el = page.locator("[class*='progress'], [class*='percent'], [class*='uploading']").first
        text = await el.inner_text()
        if "%" in text:
            return text.strip()
    except Exception:
        pass
    return ""


async def _handle_post_dialogs(page, log: Optional[Callable]) -> None:
    """
    Xử lý các dialog/popup xuất hiện sau khi click Post:
      - TUXModal-overlay (mới nhất — ưu tiên số 1)
      - "Post now" vs "Schedule" dialog
      - Copyright warning → click Continue
      - "Publish now" confirmation button
    """
    # Ưu tiên 1: TUXModal mới (chặn pointer events toàn trang)
    dismissed = await _dismiss_tux_modal(page, log)
    if dismissed:
        return

    # Ưu tiên 2: Generic confirm buttons (text-based)
    confirm_texts = [
        "Post now", "Đăng ngay", "Publish now",
        "Continue", "Tiếp tục",
        "Confirm", "Xác nhận",
        "OK", "Got it", "Hiểu rồi",
        "Proceed", "Đồng ý",
    ]
    for ctx in [page, *page.frames]:
        for text in confirm_texts:
            try:
                btn = ctx.get_by_role("button", name=text, exact=False).first
                if await btn.is_visible():
                    _log(f"[TikTok] Dialog '{text}' — đang click...", log)
                    await btn.click()
                    await page.wait_for_timeout(1000)
                    return
            except Exception:
                pass


async def _confirm_post_success(page, log: Optional[Callable]) -> bool:
    """Kiểm tra xem post có thành công không."""
    try:
        current_url = page.url

        # 1. URL đổi sang trang content (redirect sau đăng thành công)
        if "/content" in current_url or "success" in current_url:
            _log(f"[TikTok] URL redirect → {current_url[:60]}", log)
            return True

        # 2. Không còn trên trang upload nữa
        if "/upload" not in current_url and "tiktokstudio" in current_url:
            _log(f"[TikTok] Rời trang upload → {current_url[:60]}", log)
            return True

        # 3. Toast / text thành công
        success_signs = [
            "Video posted", "Đã đăng", "successfully",
            "thành công", "Your video", "Video của bạn",
            "is now live", "đã được đăng",
        ]
        for sign in success_signs:
            try:
                el = page.get_by_text(sign, exact=False).first
                if await el.is_visible():
                    _log(f"[TikTok] Thấy thông báo: '{sign}'", log)
                    return True
            except Exception:
                pass

    except Exception:
        pass
    return False


async def _save_screenshot(page, prefix: str, log: Optional[Callable]) -> None:
    """Chụp màn hình khi có lỗi — lưu vào data/debug/ cạnh app."""
    try:
        # Tìm APP_DIR portable (không hardcode ổ đĩa)
        try:
            from core.config import DATA_DIR
            screenshot_dir = DATA_DIR / "debug_screenshots"
        except ImportError:
            # Fallback nếu chạy standalone
            screenshot_dir = Path(__file__).parent / "data" / "debug_screenshots"

        screenshot_dir.mkdir(parents=True, exist_ok=True)
        ts   = int(time.time())
        path = str(screenshot_dir / f"{prefix}_{ts}.png")
        await page.screenshot(path=path, full_page=True)
        _log(f"[TikTok] Screenshot lưu tại: {path}", log)
    except Exception as e:
        _log(f"[TikTok] Không chụp được screenshot: {e}", log)


# ─── Sync wrappers ────────────────────────────────────────────────────────────

def upload_video_sync(
    video_path:   str,
    caption:      str,
    profile_path: str,
    upload_url:   str = UPLOAD_URL,
    log: Optional[Callable] = None,
    headless:     bool = False,
) -> bool:
    """
    Bản đồng bộ (synchronous) của upload_video — dùng trong thread.

    headless=True  → Chrome chạy ẩn (không thấy cửa sổ), phù hợp auto mode
    headless=False → Chrome hiển thị bình thường, phù hợp manual upload để debug
    """
    kill_existing_chromium()
    return asyncio.run(
        upload_video(video_path, caption, profile_path, upload_url, log, headless)
    )


def setup_login_sync(profile_path: str, log: Optional[Callable] = None) -> None:
    """
    Mở Chrome thật để đăng nhập TikTok (không dùng Playwright).
    QR code hoạt động bình thường vì không có CDP fingerprint.
    """
    setup_login_direct(profile_path, log)
