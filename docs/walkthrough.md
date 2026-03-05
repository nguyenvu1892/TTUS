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
