# TikTok Shop Affiliate Farm - Project Walkthrough

## Task 1: LDPlayer Instance Management (Hoàn tất)
**Branch:** `feature/task1-ldplayer-setup`
**File chính:** `ld_manager.py`

### Cấu trúc dự án
```
TikTokUS/
├── ld_manager.py             # Core automation script
├── config.json               # Cấu hình đường dẫn & thông số (SOURCE OF TRUTH)
├── requirements.txt          # Deps (stdlib only)
├── .gitignore
├── data/
│   ├── instances_state.json  # Trạng thái runtime của 10 instance
│   └── ld_manager.log        # Log vận hành (auto-generated)
└── docs/
    ├── walkthrough.md          # File này (tóm tắt kiến trúc)
    └── walkthrough.md.resolved # Log chi tiết theo task
```

### Luồng hoạt động `ld_manager.py`
1. **`load_config()`** — Đọc `config.json`, validate key `LDPLAYER_PATH`, build `LD_CONSOLE_PATH` = `LDPLAYER_PATH + \ldconsole.exe`
2. **`create_instances()`** — Dùng `ldconsole copy` để clone instance từ index 0 với tên `TikTok_US_01..10`
3. **`configure_instance(index, name)`** — Dùng `ldconsole modify` để set CPU=2, RAM=3072MB; `ldconsole property put` để spoof manufacturer/model
4. **`get_instance_status(index)`** — Dùng `ldconsole isrunning --index n` → trả về `running/stopped/error`
5. **`save_state()`** — Ghi dict trạng thái vào `data/instances_state.json`

### config.json (Source of Truth)
```json
{
  "LDPLAYER_PATH": "C:\\LDPlayer\\LDPlayer9",
  "INSTANCE_COUNT": 10,
  "INSTANCE_PREFIX": "TikTok_US_",
  "TARGET_RAM_MB": 3072,
  "TARGET_CPU_CORES": 2
}
```

> **Luật:** Mọi thay đổi đường dẫn LDPlayer chỉ được sửa tại `config.json`. Script KHÔNG hardcode bất kỳ path nào.

### Device Library
- Samsung: SM-G998B (S21U), SM-S908B (S22U), SM-G996B (S21+), SM-A546B (A54), SM-A336B (A33)
- Google: Pixel 6, Pixel 6 Pro, Pixel 7, Pixel 7 Pro
- OnePlus: CPH2451 (OP11)

### Sử dụng
```bash
python ld_manager.py setup      # Full: Create + Configure + Status
python ld_manager.py status     # Chỉ query trạng thái
python ld_manager.py list       # Liệt kê instance
```

> ✅ **Task 1 NGHIỆM THU** — Đã merge vào `main` tại commit `1abffc8`. Syntax clean, state file hợp lệ, ldconsole.exe verified.

---

## Task 2: Proxy Setup (In Progress)
**Branch:** `feature/task2-proxy-setup`
**File mới:** `proxy_manager.py`, `data/proxies_list.txt`

### Mục tiêu
Ép 10 VM chạy qua 10 IP US độc lập bằng SocksDroid + ADB Intent.

### App được chọn: SocksDroid (bndeff/socksdroid, GPL-3.0)
- Lý do: Duy nhất trong 3 ứng dụng có ADB Intent API được tài liệu hóa
- Cấu hình qua: `am start -n net.typeblog.socks/.MainActivity --es intent_ip <IP> --ei intent_port <PORT> --es intent_user <USER> --es intent_pass <PASS> --ez intent_start true`

### Dependencies (chốt cuối)
| Thư viện | Version | Lý do |
|---|---|---|
| `requests` | ≥2.28.0 | HTTP streaming download APK, retry, timeout — vượt trội urllib |
| `colorama` | ≥0.4.6 | ANSI color trên Windows PowerShell/CMD (không có → in ký tự raw) |
> Không dùng `adbutils`/`pure-python-adb` — `ldconsole adb` đã là ADB bridge; thêm lib sẽ tăng độ phức tạp vô ích.

### Luồng hoạt động `proxy_manager.py`
1. **`_bootstrap_deps()`** — Tự kiểm tra & `pip install` nếu thiếu `requests`/`colorama`
2. **`download_apk()`** — Gọi GitHub API → lấy URL bản mới nhất → streaming download với progress bar; fallback về URL v1.0.4 nếu API lỗi
3. **`load_proxies()`** — Đọc `data/proxies_list.txt`, parse 10 dòng `IP:Port:User:Pass`
4. **`install_app(index)`** — `ldconsole installapp --index <i> --filename <apk>` → cài SocksDroid vào VM
5. **`configure_proxy(index, proxy)`** — `ldconsole adb --index <i> --command "am start ..."` → bắn Intent cấu hình proxy
6. **`setup_all()`** — Pipeline đầy đủ: download → install → colorama VPN warning → configure

### Sử dụng
```bash
python proxy_manager.py setup      # Full: tải APK + cài + configure
python proxy_manager.py download   # Chỉ tải APK từ GitHub
python proxy_manager.py install    # Chỉ cài APK (APK phải có sẵn)
python proxy_manager.py configure  # Chỉ cấu hình proxy qua ADB Intent
```

> ✅ **Task 2 HOÀN TẤT & VERIFIED** — Đã merge vào `main`. Syntax clean, deps documented.
