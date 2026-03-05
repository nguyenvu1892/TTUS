# -*- coding: utf-8 -*-

"""
ld_manager.py
==============
TikTok Shop Affiliate Farm - LDPlayer 9 Instance Manager

Quáº£n lÃ½ tá»± Ä‘á»™ng 10 mÃ¡y áº£o LDPlayer 9 thÃ´ng qua ldconsole.exe.
- Táº¡o instance: TikTok_US_01 ~ TikTok_US_10
- Cáº¥u hÃ¬nh: CPU 2 cores, RAM 3072MB
- Device Spoofing: IMEI, Android ID, Manufacturer, Model
- GiÃ¡m sÃ¡t tráº¡ng thÃ¡i -> data/instances_state.json

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
# CONFIG LOADER  (Ä‘á»-c tá»« config.json â€” KHÃ”NG hardcode path)
# ===========================================================================

CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config() -> dict:
    """
    Ä-á»-c config.json vÃ  tráº£ vá»- dict cáº¥u hÃ¬nh.
    Raise FileNotFoundError náº¿u thiáº¿u file, KeyError náº¿u thiáº¿u key báº¯t buá»™c.
    """
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"[CONFIG] KhÃ´ng tÃ¬m tháº¥y file cáº¥u hÃ¬nh: {CONFIG_FILE}\n"
            f"HÃ£y táº¡o file config.json vá»›i key 'LDPLAYER_PATH'."
        )
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if "LDPLAYER_PATH" not in cfg:
        raise KeyError(
            "[CONFIG] Thiáº¿u key 'LDPLAYER_PATH' trong config.json."
        )
    return cfg


CFG = load_config()

# ===========================================================================
# CONFIGURATION  (giÃ¡ trá»‹ láº¥y tá»« CFG Ä‘á»-c config.json)
# ===========================================================================

# GhÃ©p ldconsole.exe tá»« LDPLAYER_PATH trong config â€” KHÃ”NG hardcode
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
    Gá»-i ldconsole.exe vá»›i cÃ¡c tham sá»‘ tÃ¹y Ã½.
    Tráº£ vá»- CompletedProcess. KhÃ´ng raise exception Ä‘á»ƒ trÃ¡nh crash.
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
        log.error(f"Timeout khi cháº¡y: {' '.join(cmd)}")
        raise
    except FileNotFoundError:
        log.critical(f"KhÃ´ng tÃ¬m tháº¥y ldconsole.exe táº¡i: {LD_CONSOLE_PATH}")
        raise


# ===========================================================================
# INSTANCE LISTING
# ===========================================================================

def list_instances() -> dict:
    """
    Tráº£ vá»- dict {name: index} cá»§a táº¥t cáº£ instance hiá»‡n cÃ³.
    Lá»‡nh: ldconsole list2
    Output format má»--i dÃ²ng: "index,name,top-window-handle,..."
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
    log.info(f"Danh sÃ¡ch instance hiá»‡n cÃ³: {list(instances.keys())}")
    return instances


# ===========================================================================
# INSTANCE CREATION
# ===========================================================================

def create_instances(count: int = INSTANCE_COUNT) -> None:
    """
    Táº¡o cÃ¡c instance TikTok_US_01 ~ TikTok_US_10 náº¿u chÆ°a tá»“n táº¡i.
    Sá»­ dá»¥ng ldconsole copy Ä‘á»ƒ táº¡o báº£n sao tá»« instance gá»‘c (index 0).
    """
    existing = list_instances()
    for i in range(1, count + 1):
        name = f"{INSTANCE_PREFIX}{i:02d}"
        if name in existing:
            log.info(f"[SKIP] Instance {name} Ä‘Ã£ tá»“n táº¡i (index={existing[name]})")
            continue
        log.info(f"[CREATE] Ä-ang táº¡o instance: {name}")
        result = ld_command("copy", "--name", name, "--from", "0")
        if result.returncode == 0:
            log.info(f"[OK] Táº¡o thÃ nh cÃ´ng: {name}")
        else:
            log.error(f"[FAIL] Táº¡o tháº¥t báº¡i: {name} | stderr: {result.stderr.strip()}")
        time.sleep(1)  # TrÃ¡nh race condition


# ===========================================================================
# DEVICE SPOOFING HELPERS
# ===========================================================================

def _random_imei() -> str:
    """Táº¡o IMEI 15 chá»¯ sá»‘ ngáº«u nhiÃªn (cÃ³ Luhn checksum)."""
    # TAC (8 digits) + body (6 digits) + Luhn check digit
    base = [random.randint(0, 9) for _ in range(14)]
    # TÃ­nh Luhn check digit
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
    """Táº¡o Android ID dáº¡ng hex 16 kÃ½ tá»± (8 bytes)."""
    return "".join(random.choices(string.hexdigits[:16].lower(), k=16))


def _random_device_profile() -> dict:
    """Chá»-n ngáº«u nhiÃªn má»™t device profile tá»« danh sÃ¡ch."""
    return random.choice(DEVICE_PROFILES).copy()


# ===========================================================================
# INSTANCE CONFIGURATION
# ===========================================================================

def configure_instance(index: int, name: str) -> None:
    """
    Cáº¥u hÃ¬nh CPU, RAM vÃ  thá»±c hiá»‡n device spoofing cho má»™t instance.
    - modify: set cpu, memory
    - property: set IMEI, Android ID, manufacturer, model
    """
    log.info(f"[CONFIG] Ä-ang cáº¥u hÃ¬nh {name} (index={index})...")

    # 1. Set CPU vÃ  RAM
    result = ld_command(
        "modify",
        "--index", index,
        "--cpu", TARGET_CPU_CORES,
        "--memory", TARGET_RAM_MB,
        "--resolution", "720,1280,320",     # Mobile 9:16 portrait, 320dpi ï¿½ khop voi toa do ADB Task 3
        "--imei", "auto",
        "--androidid", "auto",
    )
    if result.returncode == 0:
        log.info(f"  [OK] Set CPU={TARGET_CPU_CORES}, RAM={TARGET_RAM_MB}MB cho {name}")
    else:
        log.warning(f"  [WARN] modify lá»--i cho {name}: {result.stderr.strip()}")
        # Thá»­ lá»‡nh ngáº¯n hÆ¡n náº¿u phiÃªn báº£n ldconsole khÃ´ng há»-- trá»£ --imei trong modify
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

    # Set IMEI via modifyIMEI command (náº¿u cÃ³)
    # Trong LDPlayer 9, IMEI thÆ°á»-ng set qua modifyIMEI hoáº·c trong modify
    result_imei = ld_command("modifyIMEI", "--index", index, "--imei", imei)
    if result_imei.returncode != 0:
        log.warning(f"  [WARN] modifyIMEI khÃ´ng kháº£ dá»¥ng, dÃ¹ng phÆ°Æ¡ng phÃ¡p dá»± phÃ²ng cho {name}")

    # Set Android ID via modifyandroidid (náº¿u cÃ³)
    result_aid = ld_command("modifyandroidid", "--index", index, "--androidid", android_id)
    if result_aid.returncode != 0:
        log.warning(f"  [WARN] modifyandroidid khÃ´ng kháº£ dá»¥ng cho {name}")

    log.info(f"  [DONE] Cáº¥u hÃ¬nh xong {name}")


def configure_all_instances() -> None:
    """
    Cáº¥u hÃ¬nh tuáº§n tá»± táº¥t cáº£ 10 instance má»¥c tiÃªu.
    DÃ¹ng danh sÃ¡ch instance hiá»‡n cÃ³ Ä‘á»ƒ láº¥y index chÃ­nh xÃ¡c.
    """
    existing = list_instances()
    for i in range(1, INSTANCE_COUNT + 1):
        name = f"{INSTANCE_PREFIX}{i:02d}"
        if name not in existing:
            log.warning(f"[SKIP CONFIG] {name} khÃ´ng tá»“n táº¡i, bá»- qua cáº¥u hÃ¬nh")
            continue
        configure_instance(existing[name], name)
        time.sleep(0.5)


# ===========================================================================
# STATUS MONITORING
# ===========================================================================

def get_instance_status(index: int) -> str:
    """
    Kiá»ƒm tra tráº¡ng thÃ¡i má»™t instance.
    ldconsole isrunning --index <n>
    Output: "running" náº¿u Ä‘ang cháº¡y, ngÆ°á»£c láº¡i "stopped"
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
        log.error(f"Lá»--i khi kiá»ƒm tra status index={index}: {e}")
        return "error"


def get_all_statuses() -> dict:
    """
    Tráº£ vá»- dict tráº¡ng thÃ¡i táº¥t cáº£ instance má»¥c tiÃªu:
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
    log.info(f"Tráº¡ng thÃ¡i táº¥t cáº£ instance: {statuses}")
    return statuses


# ===========================================================================
# STATE PERSISTENCE
# ===========================================================================

def save_state(statuses: Optional[dict] = None) -> None:
    """
    LÆ°u tráº¡ng thÃ¡i cÃ¡c instance vÃ o data/instances_state.json.
    Náº¿u khÃ´ng truyá»-n statuses thÃ¬ tá»± query.
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
    log.info(f"[STATE] Ä-Ã£ lÆ°u tráº¡ng thÃ¡i vÃ o {STATE_FILE}")


def load_state() -> dict:
    """Ä-á»-c tráº¡ng thÃ¡i tá»« file JSON. Tráº£ vá»- {} náº¿u chÆ°a cÃ³."""
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
# CONVENIENCE ENTRY POINTS
# ===========================================================================

def full_setup() -> None:
    """
    Quy trÃ¬nh Ä‘áº§y Ä‘á»§:
    1. Táº¡o 10 instance
    2. Cáº¥u hÃ¬nh CPU/RAM + Device Spoofing
    3. Query tráº¡ng thÃ¡i
    4. LÆ°u state JSON
    """
    log.info("=" * 60)
    log.info("Báº®T Ä-áº¦U: Quy trÃ¬nh khá»Ÿi táº¡o TikTok Affiliate Farm")
    log.info("=" * 60)

    log.info("--- BÆ¯á»šC 1: Táº¡o Instance ---")
    create_instances()

    log.info("--- BÆ¯á»šC 2: Cáº¥u HÃ¬nh & Device Spoofing ---")
    configure_all_instances()

    log.info("--- BÆ¯á»šC 3: Query & LÆ°u Tráº¡ng ThÃ¡i ---")
    save_state()

    log.info("=" * 60)
    log.info("HOÃ€N Táº¤T: Xem tráº¡ng thÃ¡i táº¡i data/instances_state.json")
    log.info("=" * 60)


def status_report() -> None:
    """Chá»‰ query vÃ  hiá»ƒn thá»‹ tráº¡ng thÃ¡i, lÆ°u file JSON."""
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
        print(f"Lá»‡nh khÃ´ng há»£p lá»‡: {cmd}")
        sys.exit(1)
