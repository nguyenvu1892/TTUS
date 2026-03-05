# -*- coding: utf-8 -*-
"""
ld_manager.py
==============
TikTok Shop Affiliate Farm - LDPlayer 9 Instance Manager

Quan ly tu dong 10 may ao LDPlayer 9 thong qua ldconsole.exe.
- Tao instance: TikTok_US_01 ~ TikTok_US_10
- Cau hinh: CPU 2 cores, RAM 3072MB, Resolution 720x1280, Root ON, ADB ON
- Device Spoofing: IMEI, Android ID, Manufacturer, Model
- Giam sat trang thai -> data/instances_state.json

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
# CONFIG LOADER
# ===========================================================================

CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config() -> dict:
    """Doc config.json va tra ve dict cau hinh."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"[CONFIG] Khong tim thay file cau hinh: {CONFIG_FILE}\n"
            f"Hay tao file config.json voi key 'LDPLAYER_PATH'."
        )
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if "LDPLAYER_PATH" not in cfg:
        raise KeyError("[CONFIG] Thieu key 'LDPLAYER_PATH' trong config.json.")
    return cfg


CFG = load_config()

# ===========================================================================
# CONFIGURATION
# ===========================================================================

LD_CONSOLE_PATH  = str(Path(CFG["LDPLAYER_PATH"]) / "ldconsole.exe")
INSTANCE_COUNT   = int(CFG.get("INSTANCE_COUNT",   10))
INSTANCE_PREFIX  = str(CFG.get("INSTANCE_PREFIX",  "TikTok_US_"))
TARGET_RAM_MB    = int(CFG.get("TARGET_RAM_MB",    3072))
TARGET_CPU_CORES = int(CFG.get("TARGET_CPU_CORES", 2))

STATE_FILE = Path(__file__).parent / "data" / "instances_state.json"
LOG_FILE   = Path(__file__).parent / "data" / "ld_manager.log"

DEVICE_PROFILES = [
    {"manufacturer": "samsung", "model": "SM-G998B",  "brand": "samsung"},
    {"manufacturer": "samsung", "model": "SM-S908B",  "brand": "samsung"},
    {"manufacturer": "samsung", "model": "SM-G996B",  "brand": "samsung"},
    {"manufacturer": "samsung", "model": "SM-A546B",  "brand": "samsung"},
    {"manufacturer": "samsung", "model": "SM-A336B",  "brand": "samsung"},
    {"manufacturer": "Google",  "model": "Pixel 6 Pro", "brand": "google"},
    {"manufacturer": "Google",  "model": "Pixel 7 Pro", "brand": "google"},
    {"manufacturer": "Google",  "model": "Pixel 6",     "brand": "google"},
    {"manufacturer": "Google",  "model": "Pixel 7",     "brand": "google"},
    {"manufacturer": "OnePlus", "model": "CPH2451",     "brand": "oneplus"},
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
    """Goi ldconsole.exe voi cac tham so tuy y."""
    cmd = [LD_CONSOLE_PATH, *[str(a) for a in args]]
    log.debug(f"RUN: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            log.warning(f"ldconsole stderr: {result.stderr.strip()}")
        return result
    except subprocess.TimeoutExpired:
        log.error(f"Timeout khi chay: {' '.join(cmd)}")
        raise
    except FileNotFoundError:
        log.critical(f"Khong tim thay ldconsole.exe tai: {LD_CONSOLE_PATH}")
        raise


# ===========================================================================
# INSTANCE LISTING
# ===========================================================================

def list_instances() -> dict:
    """Tra ve dict {name: index} cua tat ca instance hien co."""
    result = ld_command("list2")
    instances = {}
    if result.returncode != 0 or not result.stdout.strip():
        return instances
    for line in result.stdout.strip().splitlines():
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                idx  = int(parts[0].strip())
                name = parts[1].strip()
                instances[name] = idx
            except ValueError:
                continue
    log.info(f"Danh sach instance hien co: {list(instances.keys())}")
    return instances


# ===========================================================================
# INSTANCE CREATION
# ===========================================================================

def create_instances(count: int = INSTANCE_COUNT) -> None:
    """Tao cac instance TikTok_US_01 ~ TikTok_US_10 neu chua ton tai."""
    existing = list_instances()
    for i in range(1, count + 1):
        name = f"{INSTANCE_PREFIX}{i:02d}"
        if name in existing:
            log.info(f"[SKIP] Instance {name} da ton tai (index={existing[name]})")
            continue
        log.info(f"[CREATE] Dang tao instance: {name}")
        result = ld_command("copy", "--name", name, "--from", "0")
        if result.returncode == 0:
            log.info(f"[OK] Tao thanh cong: {name}")
        else:
            log.error(f"[FAIL] Tao that bai: {name} | stderr: {result.stderr.strip()}")
        time.sleep(1)


# ===========================================================================
# DEVICE SPOOFING HELPERS
# ===========================================================================

def _random_imei() -> str:
    """Tao IMEI 15 chu so ngau nhien (co Luhn checksum)."""
    base = [random.randint(0, 9) for _ in range(14)]
    def luhn_checksum(digits):
        total = 0
        for idx, d in enumerate(reversed(digits)):
            if idx % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return (10 - (total % 10)) % 10
    return "".join(map(str, base)) + str(luhn_checksum(base))


def _random_android_id() -> str:
    """Tao Android ID dang hex 16 ky tu (8 bytes)."""
    return "".join(random.choices(string.hexdigits[:16].lower(), k=16))


def _random_device_profile() -> dict:
    """Chon ngau nhien mot device profile tu danh sach."""
    return random.choice(DEVICE_PROFILES).copy()


# ===========================================================================
# INSTANCE CONFIGURATION
# ===========================================================================

def configure_instance(index: int, name: str) -> None:
    """
    Cau hinh CPU, RAM, Resolution, Root, ADB va device spoofing cho mot instance.

    Cac tham so quan trong:
      --root 1    : Bat quyen Root trong may ao (bat buoc cho ADB su -c)
      --adb 1     : Bat ket noi ADB qua mang noi bo (bat buoc cho proxy_manager.py)
      --resolution: Ep may ao ve Mobile 9:16 720x1280 320dpi cho toa do ADB chinh xac
    """
    log.info(f"[CONFIG] Dang cau hinh {name} (index={index})...")

    # 1. Set CPU, RAM, Resolution, Root, ADB
    result = ld_command(
        "modify",
        "--index",      index,
        "--cpu",        TARGET_CPU_CORES,
        "--memory",     TARGET_RAM_MB,
        "--resolution", "720,1280,320",  # Mobile 9:16 portrait, 320dpi
        "--root",       "1",             # BAT ROOT -- bat buoc cho ADB su -c
        "--adb",        "1",             # BAT ADB mang noi bo -- bat buoc cho proxy/farmer
        "--imei",       "auto",
        "--androidid",  "auto",
    )
    if result.returncode == 0:
        log.info(
            f"  [OK] Set CPU={TARGET_CPU_CORES}, RAM={TARGET_RAM_MB}MB, "
            f"Resolution=720x1280, Root=ON, ADB=ON cho {name}"
        )
    else:
        log.warning(f"  [WARN] modify loi cho {name}: {result.stderr.strip()}")
        # Fallback: lenh ngan hon cho ldconsole phien ban cu
        ld_command(
            "modify",
            "--index",  index,
            "--cpu",    TARGET_CPU_CORES,
            "--memory", TARGET_RAM_MB,
            "--root",   "1",
            "--adb",    "1",
        )

    # 2. Device Spoofing
    profile    = _random_device_profile()
    imei       = _random_imei()
    android_id = _random_android_id()

    log.info(f"  [SPOOF] {name}: model={profile['model']}, IMEI={imei}, AndroidID={android_id}")

    ld_command("property", "put", "--index", index,
               "--propName", "ro.product.manufacturer",
               "--propValue", profile["manufacturer"])
    ld_command("property", "put", "--index", index,
               "--propName", "ro.product.model",
               "--propValue", profile["model"])
    ld_command("property", "put", "--index", index,
               "--propName", "ro.product.brand",
               "--propValue", profile["brand"])

    result_imei = ld_command("modifyIMEI", "--index", index, "--imei", imei)
    if result_imei.returncode != 0:
        log.warning(f"  [WARN] modifyIMEI khong kha dung cho {name}")

    result_aid = ld_command("modifyandroidid", "--index", index, "--androidid", android_id)
    if result_aid.returncode != 0:
        log.warning(f"  [WARN] modifyandroidid khong kha dung cho {name}")

    log.info(f"  [DONE] Cau hinh xong {name}")


def configure_all_instances() -> None:
    """Cau hinh tuan tu tat ca 10 instance muc tieu."""
    existing = list_instances()
    for i in range(1, INSTANCE_COUNT + 1):
        name = f"{INSTANCE_PREFIX}{i:02d}"
        if name not in existing:
            log.warning(f"[SKIP CONFIG] {name} khong ton tai, bo qua cau hinh")
            continue
        configure_instance(existing[name], name)
        time.sleep(0.5)


# ===========================================================================
# STATUS MONITORING
# ===========================================================================

def get_instance_status(index: int) -> str:
    """Kiem tra trang thai mot instance."""
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
        log.error(f"Loi khi kiem tra status index={index}: {e}")
        return "error"


def get_all_statuses() -> dict:
    """Tra ve dict trang thai tat ca instance muc tieu."""
    existing = list_instances()
    statuses = {}
    for i in range(1, INSTANCE_COUNT + 1):
        name = f"{INSTANCE_PREFIX}{i:02d}"
        if name in existing:
            idx    = existing[name]
            status = get_instance_status(idx)
            statuses[name] = {"index": idx, "status": status}
        else:
            statuses[name] = {"index": None, "status": "not_created"}
    log.info(f"Trang thai tat ca instance: {statuses}")
    return statuses


# ===========================================================================
# STATE PERSISTENCE
# ===========================================================================

def save_state(statuses: Optional[dict] = None) -> None:
    """Luu trang thai cac instance vao data/instances_state.json."""
    if statuses is None:
        statuses = get_all_statuses()
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "instances": statuses
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    log.info(f"[STATE] Da luu trang thai vao {STATE_FILE}")


def load_state() -> dict:
    """Doc trang thai tu file JSON. Tra ve {} neu chua co."""
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# CONVENIENCE ENTRY POINTS
# ===========================================================================

def full_setup() -> None:
    """Quy trinh day du: Tao + Cau hinh + Query + Luu state."""
    log.info("=" * 60)
    log.info("BAT DAU: Quy trinh khoi tao TikTok Affiliate Farm")
    log.info("=" * 60)

    log.info("--- BUOC 1: Tao Instance ---")
    create_instances()

    log.info("--- BUOC 2: Cau Hinh & Device Spoofing ---")
    configure_all_instances()

    log.info("--- BUOC 3: Query & Luu Trang Thai ---")
    save_state()

    log.info("=" * 60)
    log.info("HOAN TAT: Xem trang thai tai data/instances_state.json")
    log.info("=" * 60)


def status_report() -> None:
    """Chi query va hien thi trang thai, luu file JSON."""
    statuses = get_all_statuses()
    save_state(statuses)
    print("\n" + "=" * 55)
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
        print(f"Lenh khong hop le: {cmd}")
        sys.exit(1)
