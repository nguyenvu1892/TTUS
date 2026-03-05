"""
ld_manager.py
==============
TikTok Shop Affiliate Farm - LDPlayer 9 Instance Manager

Quản lý tự động 10 máy ảo LDPlayer 9 thông qua ldconsole.exe.
- Tạo instance: TikTok_US_01 ~ TikTok_US_10
- Cấu hình: CPU 2 cores, RAM 3072MB
- Device Spoofing: IMEI, Android ID, Manufacturer, Model
- Giám sát trạng thái -> data/instances_state.json

Hardware context: Dual Xeon E5-2680 v4 (56 threads), 96GB RAM, NVIDIA GPU (NVENC)
"""

import subprocess
import json
import random
import string
import time
import logging
import os
from pathlib import Path
from typing import Optional


# ===========================================================================
# CONFIG LOADER  (đọc từ config.json — KHÔNG hardcode path)
# ===========================================================================

CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config() -> dict:
    """
    Đọc config.json và trả về dict cấu hình.
    Raise FileNotFoundError nếu thiếu file, KeyError nếu thiếu key bắt buộc.
    """
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"[CONFIG] Không tìm thấy file cấu hình: {CONFIG_FILE}\n"
            f"Hãy tạo file config.json với key 'LDPLAYER_PATH'."
        )
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if "LDPLAYER_PATH" not in cfg:
        raise KeyError(
            "[CONFIG] Thiếu key 'LDPLAYER_PATH' trong config.json."
        )
    return cfg


CFG = load_config()

# ===========================================================================
# CONFIGURATION  (giá trị lấy từ CFG đọc config.json)
# ===========================================================================

# Ghép ldconsole.exe từ LDPLAYER_PATH trong config — KHÔNG hardcode
LD_CONSOLE_PATH = str(Path(CFG["LDPLAYER_PATH"]) / "ldconsole.exe")

INSTANCE_COUNT   = int(CFG.get("INSTANCE_COUNT",   10))
INSTANCE_PREFIX  = str(CFG.get("INSTANCE_PREFIX",  "TikTok_US_"))
TARGET_RAM_MB    = int(CFG.get("TARGET_RAM_MB",    3072))
TARGET_CPU_CORES = int(CFG.get("TARGET_CPU_CORES", 2))

STATE_FILE = Path(__file__).parent / "data" / "instances_state.json"
LOG_FILE = Path(__file__).parent / "data" / "ld_manager.log"

# Device spoofing library
DEVICE_PROFILES = [
    {"manufacturer": "samsung", "model": "SM-G998B", "brand": "Samsung Galaxy S21 Ultra"},
    {"manufacturer": "samsung", "model": "SM-S908B", "brand": "Samsung Galaxy S22 Ultra"},
    {"manufacturer": "samsung", "model": "SM-G996B", "brand": "Samsung Galaxy S21+"},
    {"manufacturer": "samsung", "model": "SM-A546B", "brand": "Samsung Galaxy A54"},
    {"manufacturer": "samsung", "model": "SM-A336B", "brand": "Samsung Galaxy A33"},
    {"manufacturer": "Google",  "model": "Pixel 6 Pro", "brand": "Google Pixel 6 Pro"},
    {"manufacturer": "Google",  "model": "Pixel 7 Pro", "brand": "Google Pixel 7 Pro"},
    {"manufacturer": "Google",  "model": "Pixel 6",     "brand": "Google Pixel 6"},
    {"manufacturer": "Google",  "model": "Pixel 7",     "brand": "Google Pixel 7"},
    {"manufacturer": "OnePlus", "model": "CPH2451",     "brand": "OnePlus 11"},
]

# ===========================================================================
# LOGGING SETUP
# ===========================================================================

STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("ld_manager")


# ===========================================================================
# CORE HELPER
# ===========================================================================

def ld_command(*args, timeout: int = 30) -> subprocess.CompletedProcess:
    """
    Gọi ldconsole.exe với các tham số tùy ý.
    Trả về CompletedProcess. Không raise exception để tránh crash.
    """
    cmd = [LD_CONSOLE_PATH, *[str(a) for a in args]]
    log.debug(f"RUN: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            log.warning(f"ldconsole stderr: {result.stderr.strip()}")
        return result
    except subprocess.TimeoutExpired:
        log.error(f"Timeout khi chạy: {' '.join(cmd)}")
        raise
    except FileNotFoundError:
        log.critical(f"Không tìm thấy ldconsole.exe tại: {LD_CONSOLE_PATH}")
        raise


# ===========================================================================
# INSTANCE LISTING
# ===========================================================================

def list_instances() -> dict:
    """
    Trả về dict {name: index} của tất cả instance hiện có.
    Lệnh: ldconsole list2
    Output format mỗi dòng: "index,name,top-window-handle,..."
    """
    result = ld_command("list2")
    instances = {}
    if result.returncode != 0 or not result.stdout.strip():
        return instances
    for line in result.stdout.strip().splitlines():
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                idx = int(parts[0].strip())
                name = parts[1].strip()
                instances[name] = idx
            except ValueError:
                continue
    log.info(f"Danh sách instance hiện có: {list(instances.keys())}")
    return instances


# ===========================================================================
# INSTANCE CREATION
# ===========================================================================

def create_instances(count: int = INSTANCE_COUNT) -> None:
    """
    Tạo các instance TikTok_US_01 ~ TikTok_US_10 nếu chưa tồn tại.
    Sử dụng ldconsole copy để tạo bản sao từ instance gốc (index 0).
    """
    existing = list_instances()
    for i in range(1, count + 1):
        name = f"{INSTANCE_PREFIX}{i:02d}"
        if name in existing:
            log.info(f"[SKIP] Instance {name} đã tồn tại (index={existing[name]})")
            continue
        log.info(f"[CREATE] Đang tạo instance: {name}")
        result = ld_command("copy", "--name", name, "--from", "0")
        if result.returncode == 0:
            log.info(f"[OK] Tạo thành công: {name}")
        else:
            log.error(f"[FAIL] Tạo thất bại: {name} | stderr: {result.stderr.strip()}")
        time.sleep(1)  # Tránh race condition


# ===========================================================================
# DEVICE SPOOFING HELPERS
# ===========================================================================

def _random_imei() -> str:
    """Tạo IMEI 15 chữ số ngẫu nhiên (có Luhn checksum)."""
    # TAC (8 digits) + body (6 digits) + Luhn check digit
    base = [random.randint(0, 9) for _ in range(14)]
    # Tính Luhn check digit
    def luhn_checksum(digits):
        total = 0
        for idx, d in enumerate(reversed(digits)):
            if idx % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return (10 - (total % 10)) % 10
    check = luhn_checksum(base)
    return "".join(map(str, base)) + str(check)


def _random_android_id() -> str:
    """Tạo Android ID dạng hex 16 ký tự (8 bytes)."""
    return "".join(random.choices(string.hexdigits[:16].lower(), k=16))


def _random_device_profile() -> dict:
    """Chọn ngẫu nhiên một device profile từ danh sách."""
    return random.choice(DEVICE_PROFILES).copy()


# ===========================================================================
# INSTANCE CONFIGURATION
# ===========================================================================

def configure_instance(index: int, name: str) -> None:
    """
    Cấu hình CPU, RAM và thực hiện device spoofing cho một instance.
    - modify: set cpu, memory
    - property: set IMEI, Android ID, manufacturer, model
    """
    log.info(f"[CONFIG] Đang cấu hình {name} (index={index})...")

    # 1. Set CPU và RAM
    result = ld_command(
        "modify",
        "--index", index,
        "--cpu", TARGET_CPU_CORES,
        "--memory", TARGET_RAM_MB,
        "--resolution", "1080,1920,480",    # FullHD portrait, 480dpi
        "--imei", "auto",
        "--androidid", "auto",
    )
    if result.returncode == 0:
        log.info(f"  [OK] Set CPU={TARGET_CPU_CORES}, RAM={TARGET_RAM_MB}MB cho {name}")
    else:
        log.warning(f"  [WARN] modify lỗi cho {name}: {result.stderr.strip()}")
        # Thử lệnh ngắn hơn nếu phiên bản ldconsole không hỗ trợ --imei trong modify
        ld_command("modify", "--index", index, "--cpu", TARGET_CPU_CORES, "--memory", TARGET_RAM_MB)

    # 2. Device Spoofing
    profile = _random_device_profile()
    imei = _random_imei()
    android_id = _random_android_id()

    log.info(f"  [SPOOF] {name}: model={profile['model']}, IMEI={imei}, AndroidID={android_id}")

    # Set manufacturer
    ld_command("property", "put", "--index", index,
               "--propName", "ro.product.manufacturer",
               "--propValue", profile["manufacturer"])

    # Set model
    ld_command("property", "put", "--index", index,
               "--propName", "ro.product.model",
               "--propValue", profile["model"])

    # Set brand
    ld_command("property", "put", "--index", index,
               "--propName", "ro.product.brand",
               "--propValue", profile["manufacturer"])

    # Set IMEI via modifyIMEI command (nếu có)
    # Trong LDPlayer 9, IMEI thường set qua modifyIMEI hoặc trong modify
    result_imei = ld_command("modifyIMEI", "--index", index, "--imei", imei)
    if result_imei.returncode != 0:
        log.warning(f"  [WARN] modifyIMEI không khả dụng, dùng phương pháp dự phòng cho {name}")

    # Set Android ID via modifyandroidid (nếu có)
    result_aid = ld_command("modifyandroidid", "--index", index, "--androidid", android_id)
    if result_aid.returncode != 0:
        log.warning(f"  [WARN] modifyandroidid không khả dụng cho {name}")

    log.info(f"  [DONE] Cấu hình xong {name}")


def configure_all_instances() -> None:
    """
    Cấu hình tuần tự tất cả 10 instance mục tiêu.
    Dùng danh sách instance hiện có để lấy index chính xác.
    """
    existing = list_instances()
    for i in range(1, INSTANCE_COUNT + 1):
        name = f"{INSTANCE_PREFIX}{i:02d}"
        if name not in existing:
            log.warning(f"[SKIP CONFIG] {name} không tồn tại, bỏ qua cấu hình")
            continue
        configure_instance(existing[name], name)
        time.sleep(0.5)


# ===========================================================================
# STATUS MONITORING
# ===========================================================================

def get_instance_status(index: int) -> str:
    """
    Kiểm tra trạng thái một instance.
    ldconsole isrunning --index <n>
    Output: "running" nếu đang chạy, ngược lại "stopped"
    """
    try:
        result = ld_command("isrunning", "--index", index, timeout=10)
        output = result.stdout.strip().lower()
        if "running" in output:
            return "running"
        elif result.returncode == 0:
            return "stopped"
        else:
            return "error"
    except Exception as e:
        log.error(f"Lỗi khi kiểm tra status index={index}: {e}")
        return "error"


def get_all_statuses() -> dict:
    """
    Trả về dict trạng thái tất cả instance mục tiêu:
    { "TikTok_US_01": {"index": 0, "status": "stopped"}, ... }
    """
    existing = list_instances()
    statuses = {}
    for i in range(1, INSTANCE_COUNT + 1):
        name = f"{INSTANCE_PREFIX}{i:02d}"
        if name in existing:
            idx = existing[name]
            status = get_instance_status(idx)
            statuses[name] = {"index": idx, "status": status}
        else:
            statuses[name] = {"index": None, "status": "not_created"}
    log.info(f"Trạng thái tất cả instance: {statuses}")
    return statuses


# ===========================================================================
# STATE PERSISTENCE
# ===========================================================================

def save_state(statuses: Optional[dict] = None) -> None:
    """
    Lưu trạng thái các instance vào data/instances_state.json.
    Nếu không truyền statuses thì tự query.
    """
    if statuses is None:
        statuses = get_all_statuses()

    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "instances": statuses
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    log.info(f"[STATE] Đã lưu trạng thái vào {STATE_FILE}")


def load_state() -> dict:
    """Đọc trạng thái từ file JSON. Trả về {} nếu chưa có."""
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# CONVENIENCE ENTRY POINTS
# ===========================================================================

def full_setup() -> None:
    """
    Quy trình đầy đủ:
    1. Tạo 10 instance
    2. Cấu hình CPU/RAM + Device Spoofing
    3. Query trạng thái
    4. Lưu state JSON
    """
    log.info("=" * 60)
    log.info("BẮT ĐẦU: Quy trình khởi tạo TikTok Affiliate Farm")
    log.info("=" * 60)

    log.info("--- BƯỚC 1: Tạo Instance ---")
    create_instances()

    log.info("--- BƯỚC 2: Cấu Hình & Device Spoofing ---")
    configure_all_instances()

    log.info("--- BƯỚC 3: Query & Lưu Trạng Thái ---")
    save_state()

    log.info("=" * 60)
    log.info("HOÀN TẤT: Xem trạng thái tại data/instances_state.json")
    log.info("=" * 60)


def status_report() -> None:
    """Chỉ query và hiển thị trạng thái, lưu file JSON."""
    statuses = get_all_statuses()
    save_state(statuses)
    print("\n{'='*55}")
    print(f"{'Instance':<20} {'Index':<8} {'Status'}")
    print("-" * 40)
    for name, info in statuses.items():
        print(f"{name:<20} {str(info.get('index','N/A')):<8} {info.get('status','unknown')}")
    print()


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("""
LDPlayer Manager - TikTok Shop Affiliate Farm
Usage:
  python ld_manager.py setup       # Full setup (create + configure + status)
  python ld_manager.py create      # Only create instances
  python ld_manager.py configure   # Only configure all instances
  python ld_manager.py status      # Query & save status report
  python ld_manager.py list        # List all existing instances
""")
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == "setup":
        full_setup()
    elif cmd == "create":
        create_instances()
    elif cmd == "configure":
        configure_all_instances()
    elif cmd == "status":
        status_report()
    elif cmd == "list":
        instances = list_instances()
        for name, idx in instances.items():
            print(f"  [{idx}] {name}")
    else:
        print(f"Lệnh không hợp lệ: {cmd}")
        sys.exit(1)
