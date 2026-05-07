# Hướng dẫn Setup GitHub Auto-Update

## Bước 1: Tạo GitHub repo

1. Vào https://github.com/new
2. Đặt tên repo: `tiktok-auto-uploader`
3. Chọn **Private** (chỉ mày xem được)
4. Bấm **Create repository**

---

## Bước 2: Cập nhật package.json

Mở file `electron/package.json`, sửa phần `publish`:

```json
"publish": {
  "provider": "github",
  "owner": "TÊN_GITHUB_CỦA_MAY",    ← sửa chỗ này
  "repo": "tiktok-auto-uploader"
}
```

---

## Bước 3: Upload code lên GitHub

Chạy trong thư mục `K:\AUTO_GEN_AI\APP_AUTO_UPLOAD_V2\`:

```bash
git init
git add .
git commit -m "Initial release v2.0.0"
git branch -M main
git remote add origin https://github.com/TÊN_GITHUB/tiktok-auto-uploader.git
git push -u origin main
```

---

## Bước 4: Tạo Release đầu tiên (build installer)

```bash
git tag v2.0.0
git push origin v2.0.0
```

→ GitHub Actions tự động:
1. Build `backend.exe` bằng PyInstaller
2. Build `TikTok Auto Uploader Setup.exe` bằng electron-builder
3. Tạo Release và đính kèm file installer

Xem progress tại: `github.com/TÊN_GITHUB/tiktok-auto-uploader/actions`

---

## Bước 5: Khi muốn update (release bản mới)

```bash
# Sửa version trong electron/package.json
# "version": "2.0.1"

git add .
git commit -m "Update v2.0.1: mô tả thay đổi"
git tag v2.0.1
git push origin main
git push origin v2.0.1
```

→ App của người dùng tự nhận thông báo update và hỏi có muốn tải về không.

---

## Cấu trúc Release tự động

```
GitHub Release v2.0.1
├── TikTok Auto Uploader Setup 2.0.1.exe   ← installer cho user
├── TikTok Auto Uploader Setup 2.0.1.exe.blockmap
└── latest.yml                              ← electron-updater đọc file này
```

---

## Lưu ý quan trọng

- **GITHUB_TOKEN** được tạo tự động bởi GitHub Actions — không cần config gì thêm
- Repo **Private** vẫn hoạt động với auto-update (miễn phí)
- Build mất khoảng **8-15 phút** mỗi lần (Windows runner chậm hơn)
- File installer khoảng **200-350MB**
