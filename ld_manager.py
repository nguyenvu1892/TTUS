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

def configure_base_instance() -> None:
    """
    Cau hinh may ao GOC (index 0 / LDPlayer) lam TEMPLATE truoc khi copy.

    Chien luoc Copy-from-Base:
      - "ldconsole modify" ap dung len may ao copy KHONG persist (bug LDPlayer).
      - Giai phap: cau hinh may goc index=0 mot lan duy nhat voi Root+ADB+Res+CPU+RAM.
      - Sau do moi "copy --from 0" de tao 10 may con -> 100% ke thua cau hinh.
      - configure_instance() chi can chay Device Spoofing (IMEI, model...) tren may con.
    """
    log.info("[BASE] Dang cau hinh may ao GOC (index=0) lam Template ...")
    result = ld_command(
        "modify",
        "--index",      "0",
        "--cpu",        TARGET_CPU_CORES,
        "--memory",     TARGET_RAM_MB,
        "--resolution", "720,1280,320",  # Mobile 9:16 portrait, 320dpi
        "--root",       "1",             # BAT ROOT
        "--adb",        "1",             # BAT ADB mang noi bo
    )
    if result.returncode == 0:
        log.info(
            f"[BASE] Template OK: CPU={TARGET_CPU_CORES}, RAM={TARGET_RAM_MB}MB, "
            "Resolution=720x1280, Root=ON, ADB=ON."
        )
        log.info("[BASE] Cac may ao duoc copy --from 0 se ke thua toan bo cau hinh nay.")
    else:
        log.warning(f"[BASE] modify may goc loi: {result.stderr.strip()}")
        log.warning("[BASE] Hay cau hinh may goc (index=0) thu cong trong LDPlayer truoc khi chay setup.")


def configure_instance(index: int, name: str) -> None:
    """
    Device Spoofing cho mot instance (IMEI, Android ID, Manufacturer, Model).
    Root/ADB/Resolution da duoc ke thua tu may goc index=0 qua copy.
    Khong can chay modify lai de tranh bug persistence cua ldconsole.
    """
    log.info(f"[CONFIG] Dang cau hinh {name} (index={index})...")

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
    """Device Spoofing tuan tu cho tat ca 10 instance muc tieu."""
    existing = list_instances()
    for i in range(1, INSTANCE_COUNT + 1):
        name = f"{INSTANCE_PREFIX}{i:02d}"
        if name not in existing:
            log.warning(f"[SKIP CONFIG] {name} khong ton tai, bo qua cau hinh")
            continue
        configure_instance(existing[name], name)
        time.sleep(0.5)


# ===========================================================================
# OPTIMIZE VMS -- Ep cau hinh toi thieu de tiet kiem tai nguyen
# ===========================================================================

# Cac tham so toi uu cho moi truong Farm (TikTok + SocksDroid)
_OPTIMIZE_SETTINGS = {
    "advancedSettings.cpuCount"     : 2,       # 2 core du chay SocksDroid+TikTok
    "advancedSettings.memorySize"   : 1536,    # 1536MB: toi thieu tranh OOM kill
    "advancedSettings.resolution"   : {"width": 720, "height": 1280},  # Giu nguyen toa do ADB
    "advancedSettings.resolutionDpi": 240,     # DPI nhe hon (320 -> 240)
    "basicSettings.fps"             : 20,      # 20fps: du mua man hinh, tiet kiem CPU
    "advancedSettings.micphoneName" : "",      # Tat mic (chuoi rong = disable)
    "advancedSettings.speakerName"  : "",      # Tat speaker
}


def optimize_all_vms() -> dict:
    """
    Chinh sua truc tiep file JSON config cua tung VM de ep ve cau hinh toi thieu.

    Phuong phap:
      - Doc file leidianX.config (JSON)
      - Merge _OPTIMIZE_SETTINGS vao config hien tai
      - Ghi lai file

    Bao ve an toan (QUAN TRONG):
      - Kiem tra isrunning truoc khi ghi. Neu VM dang chay -> log WARNING, bo qua.
      - Neu khong tim thay file config -> log WARNING, bo qua.
      - Ghi tam sang .tmp truoc, rename sau de tranh corrupt khi bi gian doan.

    Returns:
      dict {vm_name: 'ok' | 'skipped_running' | 'skipped_not_found' | 'error'}
    """
    ld_path    = Path(CFG["LDPLAYER_PATH"])
    config_dir = ld_path / "vms" / "config"
    existing   = list_instances()   # {name: index}

    if not config_dir.exists():
        log.error(f"[OPTIMIZE] Khong tim thay thu muc config: {config_dir}")
        return {}

    log.info("=" * 60)
    log.info(f"[OPTIMIZE] Bat dau toi uu cau hinh {len(existing)} VM...")
    log.info(f"[OPTIMIZE] Thu muc config: {config_dir}")
    log.info("=" * 60)

    results = {}
    for name, idx in sorted(existing.items(), key=lambda x: x[1]):
        config_file = config_dir / f"leidian{idx}.config"
        label       = f"[OPTIMIZE] [{name}] [idx={idx}]"

        # -- Bao ve an toan: kiem tra VM dang chay khong --
        try:
            status = get_instance_status(idx)
        except Exception as exc:
            log.warning(f"{label} Khong kiem tra duoc trang thai: {exc}. Bo qua.")
            results[name] = "error"
            continue

        if status == "running":
            log.warning(
                f"{label} VM DANG CHAY -- Bo qua de tranh corrupt file config. "
                "Tat VM roi chay lai lenh 'optimize'."
            )
            results[name] = "skipped_running"
            continue

        # -- Kiem tra file config ton tai --
        if not config_file.exists():
            log.warning(f"{label} Khong tim thay file config: {config_file}")
            results[name] = "skipped_not_found"
            continue

        # -- Doc config hien tai --
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as exc:
            log.error(f"{label} Loi khi doc file config: {exc}")
            results[name] = "error"
            continue

        # -- Merge _OPTIMIZE_SETTINGS vao config --
        original_cpu = config_data.get("advancedSettings.cpuCount", "?")
        original_ram = config_data.get("advancedSettings.memorySize", "?")
        original_fps = config_data.get("basicSettings.fps", "?")
        config_data.update(_OPTIMIZE_SETTINGS)

        # -- Ghi lai file (qua file tam de tranh corrupt) --
        tmp_file = config_file.with_suffix(".config.tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
            tmp_file.replace(config_file)   # atomic rename tren cung partition
            log.info(
                f"{label} OK | "
                f"CPU: {original_cpu} -> {_OPTIMIZE_SETTINGS['advancedSettings.cpuCount']} | "
                f"RAM: {original_ram}MB -> {_OPTIMIZE_SETTINGS['advancedSettings.memorySize']}MB | "
                f"FPS: {original_fps} -> {_OPTIMIZE_SETTINGS['basicSettings.fps']}"
            )
            results[name] = "ok"
        except Exception as exc:
            log.error(f"{label} Loi khi ghi file config: {exc}")
            if tmp_file.exists():
                tmp_file.unlink()
            results[name] = "error"

    # -- Tong ket --
    ok_count   = sum(1 for v in results.values() if v == "ok")
    skip_count = sum(1 for v in results.values() if v.startswith("skipped"))
    err_count  = sum(1 for v in results.values() if v == "error")
    log.info(
        f"[OPTIMIZE] HOAN TAT: {ok_count} OK / {skip_count} bo qua / "
        f"{err_count} loi / {len(results)} tong"
    )
    return results


# ===========================================================================
# US ENVIRONMENT SPOOFING -- Gia mao Ngon ngu / Mui gio / GPS sang My
# ===========================================================================

_ADB_BOOT_TIMEOUT  = 90   # Cho Android boot (giay)
_ADB_BOOT_POLL     = 3    # Tan suat check (giay)

# Toa do New York City (co the thay bang bat ky thanh pho My nao)
_US_GPS_LAT = "40.730610"
_US_GPS_LNG = "-73.935242"


def _adb_exe_path() -> str:
    """Tra ve duong dan adb.exe dua vao LDPLAYER_PATH trong config."""
    return str(Path(CFG["LDPLAYER_PATH"]) / "adb.exe")


def _vm_adb_port(ldplayer_index: int) -> int:
    return 5555 + (ldplayer_index * 2)


def _run_adb(adb_exe: str, port: int, *cmd_args, timeout: int = 30) -> tuple:
    """Goi adb -s 127.0.0.1:<port> <cmd_args>. Returns (ok, output)."""
    serial = f"127.0.0.1:{port}"
    try:
        r = subprocess.run(
            [adb_exe, "-s", serial] + list(cmd_args),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as exc:
        return False, str(exc)


def _wait_boot_ld(adb_exe: str, index: int, name: str) -> bool:
    port    = _vm_adb_port(index)
    deadline = time.time() + _ADB_BOOT_TIMEOUT
    while time.time() < deadline:
        _run_adb(adb_exe, port, "connect", f"127.0.0.1:{port}", timeout=8)
        ok, out = _run_adb(adb_exe, port, "shell", "getprop sys.boot_completed", timeout=8)
        if ok and out.strip() == "1":
            log.info(f"[ENV] [{name}] Android da san sang.")
            return True
        time.sleep(_ADB_BOOT_POLL)
    log.error(f"[ENV] [{name}] TIMEOUT: Android chua boot sau {_ADB_BOOT_TIMEOUT}s.")
    return False


def set_us_environment(vm_indices: list = None) -> dict:
    """
    Gia mao moi truong he thong sang chuan My (1 lan duy tri vinh vien).

    Luong:
      1. Launch VM chua chay
      2. Cho Android boot xong (getprop sys.boot_completed == '1')
      3. Ap dung: Ngon ngu (en-US), Mui gio (America/New_York)
      4. Ap dung: GPS (New York City via ldconsole location)
      5. adb reboot -- khoi dong lai de persist.sys.* ngam vinh vien

    Args:
      vm_indices: List index can xu ly. Mac dinh: lay tu list2 (bo index 0).

    Returns:
      dict {vm_name: 'ok' | 'boot_fail' | 'error'}
    """
    adb_exe    = _adb_exe_path()
    existing   = list_instances()   # {name: index}
    results    = {}

    # Chon VM target
    if vm_indices is None:
        targets = [(name, idx) for name, idx in existing.items() if idx != 0]
    else:
        targets = [(name, idx) for name, idx in existing.items()
                   if idx in vm_indices and idx != 0]

    log.info("=" * 60)
    log.info(f"[ENV] BAT DAU: Spoof US environment cho {len(targets)} VM")
    log.info(f"[ENV] GPS: ({_US_GPS_LAT}, {_US_GPS_LNG}) -- New York City")
    log.info("=" * 60)

    for name, idx in sorted(targets, key=lambda x: x[1]):
        port  = _vm_adb_port(idx)
        label = f"[ENV] [{name}] [idx={idx}] [port={port}]"

        # -- Buoc 1: Launch VM neu chua chay --
        status = get_instance_status(idx)
        if status != "running":
            log.info(f"{label} Dang launch VM...")
            r = ld_command("launch", "--index", str(idx), timeout=30)
            if r.returncode != 0:
                log.error(f"{label} Launch that bai. Bo qua.")
                results[name] = "error"
                continue
            time.sleep(2)

        # -- Buoc 2: Cho Android boot --
        if not _wait_boot_ld(adb_exe, idx, name):
            results[name] = "boot_fail"
            continue

        # -- Buoc 3: Ap dung Language + Timezone qua su -c setprop --
        env_cmd = (
            "su -c \""
            "setprop persist.sys.locale en-US && "
            "setprop persist.sys.timezone America/New_York && "
            "service call alarm 3 s16 America/New_York && "
            "am broadcast -a android.intent.action.LOCALE_CHANGED"
            "\""
        )
        ok, out = _run_adb(adb_exe, port, "shell", env_cmd, timeout=20)
        if ok:
            log.info(f"{label} Language=en-US, Timezone=America/New_York: OK")
        else:
            log.warning(f"{label} setprop warning (co the khong co root): {out[:80]}")

        # -- Buoc 4: GPS Spoofing qua ldconsole location --
        gps_ok = ld_command(
            "location", "--index", str(idx),
            "--LNG", _US_GPS_LNG, "--LAT", _US_GPS_LAT,
            timeout=15,
        )
        if gps_ok.returncode == 0:
            log.info(f"{label} GPS New York City ({_US_GPS_LAT}, {_US_GPS_LNG}): OK")
        else:
            log.warning(f"{label} GPS via ldconsole that bai -- thu setprop...")
            # Fallback: setprop GPS (chi co tac dung tren mot so ROM)
            _run_adb(adb_exe, port, "shell",
                     f"su -c \"setprop persist.gps.latitude {_US_GPS_LAT} "
                     f"&& setprop persist.gps.longitude {_US_GPS_LNG}\"",
                     timeout=10)

        # -- Buoc 5: adb reboot de persist settings vinh vien --
        log.info(f"{label} Dang reboot VM de ngam cau hinh moi vinh vien...")
        _run_adb(adb_exe, port, "reboot", timeout=15)
        log.info(f"{label} Da gui lenh reboot. VM se khoi dong lai.")
        results[name] = "ok"
        time.sleep(1.0)   # Tranh gua qua nhieu lenh cung luc

    ok_count  = sum(1 for v in results.values() if v == "ok")
    fail_count = len(results) - ok_count
    log.info(f"[ENV] HOAN TAT: {ok_count} OK / {fail_count} loi / {len(results)} tong")
    log.info("[ENV] Luu y: Sau khi VM reboot xong, ung dung moi se thay ngon ngu/mui gio My.")
    return results


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
    """Quy trinh day du: Base Config + Tao Instance + Toi Uu + Device Spoof + Save State."""
    log.info("=" * 60)
    log.info("BAT DAU: Quy trinh khoi tao TikTok Affiliate Farm")
    log.info("=" * 60)

    log.info("--- BUOC 0: Cau Hinh May Goc (Template index=0) ---")
    configure_base_instance()

    log.info("--- BUOC 1: Tao Instance (copy --from 0) ---")
    create_instances()

    log.info("--- BUOC 2: Toi Uu Cau Hinh (CPU/RAM/FPS/Audio - sua JSON config truc tiep) ---")
    optimize_all_vms()

    log.info("--- BUOC 3: Device Spoofing (IMEI/Model ke thua Root+ADB tu may goc) ---")
    configure_all_instances()

    log.info("--- BUOC 4: Query & Luu Trang Thai ---")
    save_state()

    log.info("=" * 60)
    log.info("HOAN TAT: Xem trang thai tai data/instances_state.json")
    log.info("="  * 60)


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
  python ld_manager.py setup           # Full setup (base config + create + optimize + spoof + status)
  python ld_manager.py configure-base  # Chi cau hinh may goc index=0 lam template
  python ld_manager.py create          # Chi tao 10 instance (copy --from 0)
  python ld_manager.py optimize        # Ep cau hinh toi thieu (CPU/RAM/FPS/Audio) -- VM phai tat
  python ld_manager.py spoof_env       # Gia mao Language/Timezone/GPS sang My (1 lan), reboot VM
  python ld_manager.py configure       # Chi chay device spoofing tren 10 may
  python ld_manager.py status          # Query & save status report
  python ld_manager.py list            # List all existing instances
""")
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == "setup":
        full_setup()
    elif cmd == "configure-base":
        configure_base_instance()
    elif cmd == "create":
        create_instances()
    elif cmd == "optimize":
        optimize_all_vms()
    elif cmd == "spoof_env":
        results = set_us_environment()
        for name, status in results.items():
            print(f"  {name}: {status}")
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
