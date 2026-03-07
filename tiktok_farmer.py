# -*- coding: utf-8 -*-
"""
tiktok_farmer.py -- Task 3: Gias lap hanh vi luot TikTok FYP tren nhieu VM song song.

Luong 3 buoc (da nang cap):
    1. auto_wake_all()       -- Bat VM chua chay, cho boot xong (getprop sys.boot_completed)
    2. preflight_check_all() -- Check IP My / bat SocksDroid neu IP sai (song song + Lock)
    3. farm_all()            -- Chay run_session() song song CHI cho VM passed pre-flight

CLI:
    python tiktok_farmer.py start       # Chay farm 10 VM song song
    python tiktok_farmer.py session <n> # Chay 1 phien thu cho VM index n (1-based)
    python tiktok_farmer.py wake        # Chi chay buoc 1: Auto-Wake tat ca VM
    python tiktok_farmer.py preflight   # Chi chay buoc 2: Pre-flight check all VMs
"""

import json
import logging
import math
import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE = os.path.join("data", "ld_manager.log")
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [FARMER] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONFIG_FILE  = "config.json"
PROXIES_FILE = os.path.join("data", "proxies_list.txt")
ADB_BASE_PORT = 5555

# Endpoint luay chon khi check IP (tranh rate-limit khi nhieu VM check cung luc)
IP_CHECK_ENDPOINTS = [
    "https://api.myip.com",
    "https://ipinfo.io/json",
    "https://ip-api.com/json",
]
BOOT_TIMEOUT_SEC  = 90    # Thoi gian cho Android boot toi da
BOOT_POLL_SEC     = 3     # Tan suat check getprop sys.boot_completed
PREFLIGHT_WORKERS = 5     # So thread song song cho IP check


# ---------------------------------------------------------------------------
# Config & Proxy Loading
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not os.path.isfile(CONFIG_FILE):
        logger.error(f"Khong tim thay file cau hinh: {CONFIG_FILE}")
        sys.exit(1)

    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)

    required = ["LDPLAYER_PATH", "INSTANCE_COUNT", "TIKTOK_PACKAGE",
                "SCREEN_WIDTH", "SCREEN_HEIGHT",
                "SESSION_MIN_SEC", "SESSION_MAX_SEC"]
    for key in required:
        if key not in cfg:
            logger.error(f"config.json thieu key: '{key}'")
            sys.exit(1)

    ld_path    = cfg["LDPLAYER_PATH"]
    ld_console = os.path.join(ld_path, "ldconsole.exe")
    adb_exe    = os.path.join(ld_path, "adb.exe")

    if not os.path.isfile(ld_console):
        logger.error(f"Khong tim thay ldconsole.exe: {ld_console}")
        sys.exit(1)
    if not os.path.isfile(adb_exe):
        logger.error(f"Khong tim thay adb.exe: {adb_exe}")
        sys.exit(1)

    cfg["_LD_CONSOLE"] = ld_console
    cfg["_ADB_EXE"]    = adb_exe
    return cfg


def load_proxies(path: str = PROXIES_FILE) -> list:
    if not os.path.isfile(path):
        logger.error(f"Khong tim thay file proxy: {path}")
        sys.exit(1)

    proxies = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) != 4:
                continue
            ip, port_str, user, pwd = parts
            try:
                proxies.append({"ip": ip.strip(), "port": int(port_str),
                                 "user": user.strip(), "pass": pwd.strip()})
            except ValueError:
                logger.warning(f"Dong {lineno}: port khong hop le, bo qua.")
    return proxies


# ---------------------------------------------------------------------------
# LDConsole helpers
# ---------------------------------------------------------------------------

def _ld_cmd(ld_console: str, *args, timeout: int = 60) -> tuple:
    """Goi ldconsole. Returns (ok: bool, output: str)."""
    try:
        result = subprocess.run(
            [ld_console] + list(args),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except Exception as exc:
        return False, str(exc)


def _ld_get_all_vms(ld_console: str) -> list:
    """Lay danh sach TAT CA VM tu ldconsole list2. Returns [{index, name}]."""
    ok, out = _ld_cmd(ld_console, "list2")
    if not ok or not out.strip():
        return []
    vms = []
    for line in out.strip().splitlines():
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                vms.append({"index": int(parts[0].strip()), "name": parts[1].strip()})
            except ValueError:
                pass
    return sorted(vms, key=lambda v: v["index"])


def _ld_is_running(ld_console: str, index: int) -> bool:
    ok, out = _ld_cmd(ld_console, "isrunning", "--index", str(index))
    return ok and "running" in out.lower()


def _ld_get_running(ld_console: str) -> list:
    """Lay danh sach VM dang chay. Returns [{index, name}]."""
    ok, out = _ld_cmd(ld_console, "runninglist")
    if ok and out.strip():
        vms = []
        for line in out.strip().splitlines():
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    vms.append({"index": int(parts[0].strip()), "name": parts[1].strip()})
                except ValueError:
                    pass
        if vms:
            return sorted(vms, key=lambda v: v["index"])
    # Fallback: list2 + isrunning
    all_vms = _ld_get_all_vms(ld_console)
    return [vm for vm in all_vms if _ld_is_running(ld_console, vm["index"])]


# ---------------------------------------------------------------------------
# Direct ADB helpers (khong qua ldconsole bridge)
# ---------------------------------------------------------------------------

def _adb_port(ldplayer_index: int) -> int:
    return ADB_BASE_PORT + (ldplayer_index * 2)


def _adb_connect(adb_exe: str, port: int) -> bool:
    try:
        result = subprocess.run(
            [adb_exe, "connect", f"127.0.0.1:{port}"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        out = (result.stdout + result.stderr).strip()
        return "connected" in out.lower() or "already" in out.lower()
    except Exception:
        return False


def _adb_shell(adb_exe: str, port: int, cmd: str, timeout: int = 30) -> tuple:
    """Gui lenh shell. Returns (ok, output)."""
    serial = f"127.0.0.1:{port}"
    try:
        result = subprocess.run(
            [adb_exe, "-s", serial, "shell", cmd],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Humanized Behavior Primitives (dung ldconsole ADB bridge cho input)
# ---------------------------------------------------------------------------

def _adb(ld_console: str, index: int, command: str) -> tuple:
    """Gui lenh ADB input vao VM (dung ldconsole bridge cho input swipe/tap)."""
    try:
        result = subprocess.run(
            [ld_console, "adb", "--index", str(index), "--command", command],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as exc:
        return False, str(exc)


def _humanized_swipe(ld_console: str, index: int, cfg: dict) -> bool:
    W  = cfg["SCREEN_WIDTH"]
    H  = cfg["SCREEN_HEIGHT"]
    cx = W // 2

    x_start = int(random.gauss(cx, W * 0.07))
    x_start = max(int(W * 0.18), min(int(W * 0.82), x_start))
    x_end   = x_start + int(random.gauss(0, 18))
    x_end   = max(80, min(W - 80, x_end))

    y_start     = int(random.uniform(H * 0.65, H * 0.78))
    swipe_ratio = random.uniform(0.48, 0.72)
    y_end       = int(y_start - H * swipe_ratio)
    y_end       = max(int(H * 0.09), y_end)

    raw_dur  = random.lognormvariate(math.log(350), 0.4)
    duration = int(max(180, min(800, raw_dur)))

    cmd = f"input swipe {x_start} {y_start} {x_end} {y_end} {duration}"
    ok, _ = _adb(ld_console, index, cmd)
    time.sleep(random.uniform(0.08, 0.35))
    return ok


def _humanized_watch(index: int) -> float:
    if random.random() < 0.08:
        watch_sec = random.uniform(45, 60)
        logger.info(f"[VM {index:02d}] Video hay! Xem {watch_sec:.1f}s")
    else:
        watch_sec = random.uniform(7, 35)
    time.sleep(watch_sec)
    return watch_sec


def _maybe_like(ld_console: str, index: int, cfg: dict) -> bool:
    like_threshold = random.uniform(0.10, 0.15)
    if random.random() > like_threshold:
        return False

    W = cfg["SCREEN_WIDTH"]
    H = cfg["SCREEN_HEIGHT"]

    tap_x = W // 2 + int(random.gauss(0, 22))
    tap_y = int(H * 0.50) + int(random.gauss(0, 30))
    tap_x = max(100, min(W - 100, tap_x))
    tap_y = max(int(H * 0.30), min(int(H * 0.70), tap_y))

    _adb(ld_console, index, f"input tap {tap_x} {tap_y}")
    time.sleep(random.uniform(0.08, 0.14))
    tap_x2 = tap_x + int(random.gauss(0, 4))
    tap_y2 = tap_y + int(random.gauss(0, 4))
    _adb(ld_console, index, f"input tap {tap_x2} {tap_y2}")

    logger.info(f"[VM {index:02d}] Like! ({tap_x},{tap_y})")
    time.sleep(random.uniform(0.4, 1.2))
    return True


# ---------------------------------------------------------------------------
# TikTok Onboarding Handler
# ---------------------------------------------------------------------------

ONBOARDING_ELEMENT_TIMEOUT = 3   # Giay cho moi element (ngan de tiet kiem thoi gian)

# Cac text button can tu dong click khi Onboarding
_ONBOARDING_CLICK_TEXTS = [
    "Agree and continue",       # Terms of Service (EN)
    "Dong y va tiep tuc",       # Terms of Service (VI fallback)
    "Skip",                     # Bo qua man hinh so thich
    "Allow",                    # Cap quyen notification
    "Continue",                 # Man hinh gioi thieu
    "OK",                       # Dialog thong bao
    "Not now",                  # Popup upsell
]


def handle_onboarding(adb_exe: str, port: int, index: int) -> None:
    """
    Xu ly nhanh cac man hinh Onboarding / Tutorial cua TikTok.

    Chien thuat Short-circuit:
      - Moi element chi doi toi da ONBOARDING_ELEMENT_TIMEOUT giay
      - Neu khong tim thay -> skip (khong raise Exception)
      - VM da qua Onboarding: toan bo ham mat < 4s
      - VM moi lan dau: mat 8-15s tuy so man hinh xuat hien

    Ket thuc: Thuc hien 1 Swipe Up de xoa bo Tutorial Overlay neu con ton tai.
    """
    import importlib
    try:
        u2 = importlib.import_module("uiautomator2")
    except ImportError:
        logger.warning(f"[VM {index:02d}] uiautomator2 chua duoc cai -- bo qua Onboarding handler.")
        return

    serial = f"127.0.0.1:{port}"
    try:
        d = u2.connect(serial)
        d.implicitly_wait(ONBOARDING_ELEMENT_TIMEOUT)
    except Exception as exc:
        logger.warning(f"[VM {index:02d}] Khong ket noi uiautomator2 (port={port}): {exc}")
        return

    logger.info(f"[VM {index:02d}] Bat dau xu ly Onboarding (timeout={ONBOARDING_ELEMENT_TIMEOUT}s/element)...")

    # Mapping: keyword -> cac chuoi co the xuat hien tren nut (textContains hoac descriptionContains)
    _ONBOARDING_KEYWORDS = [
        "Agree",        # "Agree and continue" (EN) -- su dung textContains
        "Dong y",       # "Dong y va tiep tuc" (VI fallback)
        "Skip",         # Bo qua so thich
        "Allow",        # Cap quyen notification
        "Continue",     # Man hinh gioi thieu
        "Not now",      # Popup upsell
        "OK",           # Dialog thong bao
    ]

    # -- Click cac nut Onboarding bang Robust Dual-probe Selector --
    for keyword in _ONBOARDING_KEYWORDS:
        found = False
        try:
            # Probe 1: textContains (UiSelector tieu chuan)
            el = d(textContains=keyword)
            if el.exists(timeout=ONBOARDING_ELEMENT_TIMEOUT):
                logger.info(f"[VM {index:02d}] [ONBOARDING] Tim thay (textContains='{keyword}') -- Dang click...")
                el.click()
                time.sleep(0.8)
                found = True
        except Exception as exc:
            logger.info(f"[VM {index:02d}] [ONBOARDING] textContains='{keyword}' loi: {exc}")

        if not found:
            try:
                # Probe 2: descriptionContains (accessibility label)
                el2 = d(descriptionContains=keyword)
                if el2.exists(timeout=1):
                    logger.info(f"[VM {index:02d}] [ONBOARDING] Tim thay (descContains='{keyword}') -- Dang click...")
                    el2.click()
                    time.sleep(0.8)
                    found = True
            except Exception:
                pass

        if not found:
            # Probe 3 (chi cho 'Agree'): Coordinate fallback -- click vi tri co dinh
            # Vi tri nut 'Agree and continue' luon khoanh giua, phan duoi man hinh
            if "agree" in keyword.lower():
                try:
                    logger.info(
                        f"[VM {index:02d}] [ONBOARDING] Fallback coord click "
                        f"(0.5, 0.75) cho '{keyword}'..."
                    )
                    d.click(0.5, 0.75)   # toa do ty le dua tren kich thuoc man hinh
                    time.sleep(2.0)      # doi de xem popup co bien mat khong
                except Exception as coord_exc:
                    logger.info(f"[VM {index:02d}] [ONBOARDING] Coord click loi: {coord_exc}")
            else:
                logger.info(f"[VM {index:02d}] [ONBOARDING] Khong thay '{keyword}' -- bo qua.")

    # -- Swipe Up cuoi cung: xoa bo Tutorial Overlay neu co --
    try:
        W = 720
        H = 1280
        # Swipe tu 75% man hinh len 30% (giong nguoi that vuot xem video)
        d.swipe(W // 2, int(H * 0.75), W // 2, int(H * 0.30), duration=0.5)
        logger.info(f"[VM {index:02d}] Onboarding: Swipe Up de xoa tutorial overlay.")
        time.sleep(0.5)
    except Exception:
        pass

    logger.info(f"[VM {index:02d}] Onboarding handler hoan tat.")


# ---------------------------------------------------------------------------
# TikTok App Control
# ---------------------------------------------------------------------------

TIKTOK_FOREGROUND_TIMEOUT = 15   # Giay cho TikTok len foreground
TIKTOK_FOREGROUND_POLL   = 2    # Tan suat check (giay)


def _open_tiktok(ld_console: str, index: int, package: str,
                 adb_exe: str, port: int) -> bool:
    """
    Mo TikTok va XAC NHAN no thuc su len foreground truoc khi farm.

    Quy trinh:
      1. Gui lenh am start
      2. Vong lap cho toi da TIKTOK_FOREGROUND_TIMEOUT giay:
           - Bắn: dumpsys window windows | grep mCurrentFocus
           - Neu thay package name trong output -> THANH CONG
      3. Neu het timeout -> ERROR do, return False -> run_session skip VM
    """
    label = f"[VM {index:02d}] [{package}]"
    logger.info(f"{label} Dang mo TikTok...")

    # Dung monkey de tim va mo LAUNCHER Activity (khong can biet ten Activity class)
    # Stdout cua monkey bi suppress hoan toan (capture_output=True ben trong _adb_shell)
    monkey_cmd = (
        f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
    )
    ok, out = _adb_shell(adb_exe, port, monkey_cmd, timeout=15)
    if not ok:
        logger.error(f"{label} Lenh monkey that bai (rc!=0): {out[:120]}")
        return False

    # Vong lap xac nhan foreground ("Mat Than")
    deadline = time.time() + TIKTOK_FOREGROUND_TIMEOUT
    attempt  = 0
    while time.time() < deadline:
        attempt += 1
        time.sleep(TIKTOK_FOREGROUND_POLL)

        ok, out = _adb_shell(
            adb_exe, port,
            "dumpsys window windows | grep mCurrentFocus",
            timeout=8,
        )
        if ok and package in out:
            logger.info(
                f"{label} XAC NHAN: TikTok dang o Foreground "
                f"(attempt {attempt}, t={int(time.time()-deadline+TIKTOK_FOREGROUND_TIMEOUT)}s)."
            )
            # Xu ly Onboarding truoc khi bat dau farm
            handle_onboarding(adb_exe, port, index)
            return True

        logger.info(f"{label} Chua thay foreground (attempt {attempt})... [{out[:80]}]")

    # Het timeout
    logger.error(
        f"{label} [ERROR] Khong the mo TikTok sau {TIKTOK_FOREGROUND_TIMEOUT}s. "
        "Bo qua VM nay -- tuyet doi khong chay Swipe/Like."
    )
    return False


def _kill_tiktok(ld_console: str, index: int, package: str) -> bool:
    ok, _ = _adb(ld_console, index, f"am force-stop {package}")
    return ok


# ---------------------------------------------------------------------------
# BUOC 1: Auto-Wake -- Bat VM va cho boot xong
# ---------------------------------------------------------------------------

def _launch_vm(ld_console: str, vm: dict) -> bool:
    """Bat 1 VM bang ldconsole launch. Returns True neu lenh thanh cong."""
    ok, out = _ld_cmd(ld_console, "launch", "--index", str(vm["index"]), timeout=30)
    if ok:
        logger.info(f"[{vm['name']}] Da gui lenh launch.")
    else:
        logger.error(f"[{vm['name']}] launch that bai: {out[:80]}")
    return ok


def _wait_boot(adb_exe: str, vm: dict, timeout: int = BOOT_TIMEOUT_SEC) -> bool:
    """
    Vong lap cho Android boot xong.
    Dieu kien san sang: adb shell getprop sys.boot_completed == "1"
    """
    port  = _adb_port(vm["index"])
    name  = vm["name"]
    label = f"[{name}] [port={port}]"

    deadline = time.time() + timeout
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        _adb_connect(adb_exe, port)
        ok, out = _adb_shell(adb_exe, port, "getprop sys.boot_completed", timeout=8)
        if ok and out.strip() == "1":
            logger.info(f"{label} Boot xong (attempt {attempts}).")
            return True
        time.sleep(BOOT_POLL_SEC)

    logger.error(f"{label} TIMEOUT: Android chua boot xong sau {timeout}s. Bo qua VM nay.")
    return False



BATCH_SIZE    = 3    # So VM khoi dong moi cuot (Chunk)
BATCH_GAP_SEC = 5    # Nghi giua cac Batch (giay)


def auto_wake_all(cfg: dict, start_index: int = 1, end_index: int = 10) -> list:
    """
    Buoc 1: Bat cac VM trong pham vi [start_index, end_index] theo phuong phap
    'cuon chieu' (Chunking) de tranh Boot Storm (qua tai Disk/CPU).

    Thuat toan:
      1. Chia danh sach VM thanh cac Batch kich thuoc BATCH_SIZE
      2. Moi Batch:
         a. Launch tung VM (delay 2s/VM trong cung Batch)
         b. DOI toan bo Batch boot xong (song song, timeout BOOT_TIMEOUT_SEC)
         c. Chi khi Batch hien tai FULLY BOOT -> nghi BATCH_GAP_SEC -> Batch tiep
      3. VM da chay san -> them ngay vao ready_vms, khong launch lai
      4. VM boot that bai -> log ERROR, bo qua (tuyet doi khong dua vao Farm)

    Bao ve:
      - Index 0 (may goc) bi loai tru tuyet doi
      - Chi bat VM co index nam trong [start_index, end_index]

    Returns:
      list VM [{index, name}] da boot xong thanh cong, sap xep theo index.
    """
    ld_console = cfg["_LD_CONSOLE"]
    adb_exe    = cfg["_ADB_EXE"]

    all_vms = _ld_get_all_vms(ld_console)
    if not all_vms:
        logger.error("Khong tim thay VM nao trong LDPlayer (kiem tra ldconsole list2).")
        return []

    # -- FILTER: Loai may goc (index=0) va chi lay VM trong khoang target --
    target_vms = [
        vm for vm in all_vms
        if vm["index"] != 0 and start_index <= vm["index"] <= end_index
    ]

    if not target_vms:
        logger.error(
            f"Khong co VM nao trong khoang index [{start_index}, {end_index}]. "
            "Kiem tra lai tham so hoac danh sach VM."
        )
        return []

    # Chia thanh batch
    batches = [target_vms[i:i + BATCH_SIZE] for i in range(0, len(target_vms), BATCH_SIZE)]
    total_batches = len(batches)

    logger.info("=" * 64)
    logger.info(
        f"  BUOC 1: AUTO-WAKE -- {len(target_vms)} VM | "
        f"BATCH_SIZE={BATCH_SIZE} | {total_batches} cuot | "
        f"index {start_index}~{end_index}"
    )
    logger.info("=" * 64)

    ready_vms = []

    for batch_no, batch in enumerate(batches, start=1):
        logger.info(
            f"  [BATCH {batch_no}/{total_batches}] "
            f"Bat dau: {[v['name'] for v in batch]}"
        )

        # -- a. Launch cac VM chua chay trong Batch nay --
        to_boot = []
        for vm in batch:
            if _ld_is_running(ld_console, vm["index"]):
                logger.info(f"  [{vm['name']}] Da chay san sang -- khong can launch.")
                to_boot.append(vm)   # Van can cho boot confirm
            else:
                logger.info(f"  [{vm['name']}] Dang launch...")
                if _launch_vm(ld_console, vm):
                    to_boot.append(vm)
                else:
                    logger.error(f"  [{vm['name']}] Launch that bai -- bo qua VM nay.")
                time.sleep(2)   # Nghi 2s giua cac launch trong cung Batch

        if not to_boot:
            logger.warning(f"  [BATCH {batch_no}] Khong launch duoc VM nao, chuyen Batch tiep theo.")
            continue

        # -- b. DOI TOAN BO BATCH boot xong (song song) --
        logger.info(
            f"  [BATCH {batch_no}] Cho {len(to_boot)} VM boot xong "
            f"(timeout {BOOT_TIMEOUT_SEC}s/VM)..."
        )
        batch_ready = []
        with ThreadPoolExecutor(max_workers=len(to_boot)) as pool:
            futures = {pool.submit(_wait_boot, adb_exe, vm): vm for vm in to_boot}
            for future in as_completed(futures):
                vm = futures[future]
                try:
                    if future.result():
                        batch_ready.append(vm)
                    else:
                        logger.error(f"  [{vm['name']}] Boot TIMEOUT -- loai khoi Farm.")
                except Exception as exc:
                    logger.error(f"  [{vm['name']}] Exception khi cho boot: {exc}")

        logger.info(
            f"  [BATCH {batch_no}] XONG: {len(batch_ready)}/{len(to_boot)} VM ready."
        )
        ready_vms.extend(batch_ready)

        # -- c. Nghi giua cac Batch (tru Batch cuoi cung) --
        if batch_no < total_batches:
            logger.info(
                f"  [BATCH {batch_no}] Nghi {BATCH_GAP_SEC}s truoc khi launch Batch tiep theo..."
            )
            time.sleep(BATCH_GAP_SEC)

    ready_vms.sort(key=lambda v: v["index"])
    logger.info(f"AUTO-WAKE XONG: {len(ready_vms)}/{len(target_vms)} VM san sang.")
    return ready_vms



# ---------------------------------------------------------------------------
# BUOC 2: Pre-flight Check -- Kiem tra IP, bat proxy neu can
# ---------------------------------------------------------------------------

# Lock dung cho buoc bat SocksDroid (UI Automator khong duoc chay dong thoi)
_PROXY_CONFIGURE_LOCK = threading.Lock()


def _check_ip_on_vm(adb_exe: str, vm: dict) -> str:
    """
    Check IP hien tai tren VM bang curl/wget (fallback).
    Returns IP string hoac "" neu loi.
    """
    port  = _adb_port(vm["index"])
    label = f"[{vm['name']}]"

    # Chon endpoint ngau nhien de tranh rate-limit
    endpoint = random.choice(IP_CHECK_ENDPOINTS)

    # Thu curl truoc
    ok, out = _adb_shell(adb_exe, port,
                          f"curl -s --max-time 10 {endpoint}", timeout=15)
    if not ok or not out.strip():
        # Fallback: wget
        logger.info(f"{label} curl that bai, thu wget...")
        ok, out = _adb_shell(adb_exe, port,
                              f"wget -q -O - {endpoint}", timeout=15)

    if not ok or not out.strip():
        logger.warning(f"{label} Khong lay duoc IP (ca curl va wget deu that bai).")
        return ""

    return out.strip()


def _is_us_ip(response_str: str) -> bool:
    """
    Kiem tra xem response co chua indicator IP My khong.
    Ho tro output tu: api.myip.com, ipinfo.io, ip-api.com
    """
    low = response_str.lower()
    return (
        '"united states"' in low
        or '"us"' in low
        or '"country":"us"' in low
        or '"country": "us"' in low
        or 'united states' in low
    )


def _worker_preflight(adb_exe: str, vm: dict, proxy: dict, cfg: dict) -> dict:
    """
    Worker thread: kiem tra IP cho 1 VM va bat proxy neu can.
    Returns {vm, proxy, ip_ok: bool, action: str}
    """
    port  = _adb_port(vm["index"])
    label = f"[{vm['name']}] [idx={vm['index']}] [port={port}]"
    result = {"vm": vm, "proxy": proxy, "ip_ok": False, "action": "unknown"}

    if not _adb_connect(adb_exe, port):
        logger.error(f"{label} Khong the ket noi ADB.")
        result["action"] = "adb_fail"
        return result

    # Lan 1: Kiem tra IP
    response = _check_ip_on_vm(adb_exe, vm)
    if _is_us_ip(response):
        logger.info(f"{label} IP My hop le. Mang an toan.")
        result["ip_ok"] = True
        result["action"] = "ok"
        return result

    # IP khong hop le -> bat SocksDroid (de tranh crash UI Automator, dung Lock)
    logger.warning(f"{label} IP khong phai My ({response[:60]}) -- Dang bat proxy...")

    with _PROXY_CONFIGURE_LOCK:
        logger.info(f"{label} [LOCK] Bat SocksDroid voi proxy {proxy['ip']}:{proxy['port']}...")
        try:
            # Lazy import proxy_manager (tranh side-effect khi import o cap module)
            import importlib
            pm = importlib.import_module("proxy_manager")
            ok_cfg = pm.configure_proxy(adb_exe, vm["index"], vm["name"], proxy)
        except Exception as exc:
            logger.error(f"{label} Loi khi goi proxy_manager.configure_proxy: {exc}")
            ok_cfg = False

    if not ok_cfg:
        logger.error(f"{label} Bat SocksDroid that bai -- Bo qua VM nay.")
        result["action"] = "proxy_fail"
        return result

    # Doi SocksDroid ket noi
    time.sleep(3.0)

    # Lan 2: Re-verify IP
    response2 = _check_ip_on_vm(adb_exe, vm)
    if _is_us_ip(response2):
        logger.info(f"{label} Re-verify OK -- IP da doi thanh My.")
        result["ip_ok"] = True
        result["action"] = "proxy_fixed"
    else:
        logger.error(
            f"{label} Re-verify FAIL -- IP van khong phai My ({response2[:60]}). "
            "VM nay se KHONG duoc mo TikTok."
        )
        result["action"] = "ip_fail"

    return result


def preflight_check_all(cfg: dict, running_vms: list, proxies: list) -> list:
    """
    Buoc 2: Kiem tra IP song song tren tat ca VM.

    - IP check: song song (ThreadPoolExecutor, PREFLIGHT_WORKERS threads)
    - SocksDroid configure: tuyen tu (bao ve boi _PROXY_CONFIGURE_LOCK)
    - VM pass preflight -> duoc dua vao danh sach farm
    - VM fail -> bao loi do, loai khoi danh sach

    Returns:
        list tuple (vm, proxy) cua cac VM passed pre-flight.
    """
    adb_exe = cfg["_ADB_EXE"]

    logger.info("=" * 64)
    logger.info(f"  BUOC 2: PRE-FLIGHT CHECK -- {len(running_vms)} VM")
    logger.info(f"  Endpoint: {IP_CHECK_ENDPOINTS}")
    logger.info("=" * 64)

    # Ghep VM voi proxy tuand tu
    if len(proxies) < len(running_vms):
        logger.error(f"Khong du proxy ({len(proxies)}) cho {len(running_vms)} VM.")
        sys.exit(1)

    vm_proxy_pairs = [(vm, proxies[seq]) for seq, vm in enumerate(running_vms)]

    passed = []
    with ThreadPoolExecutor(max_workers=PREFLIGHT_WORKERS) as pool:
        futures = {
            pool.submit(_worker_preflight, adb_exe, vm, proxy, cfg): (vm, proxy)
            for vm, proxy in vm_proxy_pairs
        }
        for future in as_completed(futures):
            vm, proxy = futures[future]
            try:
                r = future.result()
                if r["ip_ok"]:
                    passed.append((vm, proxy))
                else:
                    logger.error(
                        f"[{vm['name']}] PRE-FLIGHT FAIL (action={r['action']}). "
                        "Loai khoi danh sach farm session."
                    )
            except Exception as exc:
                logger.error(f"[{vm['name']}] Exception trong pre-flight: {exc}")

    passed.sort(key=lambda x: x[0]["index"])
    ok_count   = len(passed)
    fail_count = len(running_vms) - ok_count
    logger.info(f"PRE-FLIGHT XONG: {ok_count} pass / {fail_count} fail / {len(running_vms)} tong")
    return passed


# ---------------------------------------------------------------------------
# BUOC 3: Session Engine -- 1 phien cho 1 VM
# ---------------------------------------------------------------------------

def run_session(vm: dict, proxy: dict, cfg: dict) -> dict:
    """
    Chay 1 phien luot TikTok hoan chinh cho VM.

    Args:
        vm:    {index, name} - thong tin VM thuc te tu ldconsole
        proxy: {ip, port, user, pass}
        cfg:   config dict
    """
    ld_console  = cfg["_LD_CONSOLE"]
    adb_exe     = cfg["_ADB_EXE"]
    package     = cfg["TIKTOK_PACKAGE"]
    index       = vm["index"]
    port        = _adb_port(index)
    session_sec = random.uniform(cfg["SESSION_MIN_SEC"], cfg["SESSION_MAX_SEC"])

    logger.info(
        f"[{vm['name']}] Bat dau phien "
        f"({session_sec/60:.1f} phut | proxy: {proxy['ip']}:{proxy['port']})"
    )

    result = {"vm": vm["name"], "index": index, "videos_watched": 0, "likes": 0,
              "session_sec": round(session_sec), "status": "ok"}

    # Mo TikTok + xac nhan foreground truoc khi bat dau farm
    if not _open_tiktok(ld_console, index, package, adb_exe, port):
        result["status"] = "error_open"
        return result

    session_start   = time.monotonic()
    last_like_video = -99

    try:
        while (time.monotonic() - session_start) < session_sec:
            _humanized_watch(index)
            result["videos_watched"] += 1

            current_video = result["videos_watched"]
            if (current_video - last_like_video) >= 5:
                liked = _maybe_like(ld_console, index, cfg)
                if liked:
                    result["likes"] += 1
                    last_like_video = current_video

            if (time.monotonic() - session_start) >= session_sec:
                break
            _humanized_swipe(ld_console, index, cfg)

    except Exception as exc:
        logger.error(f"[{vm['name']}] Loi trong phien: {exc}")
        result["status"] = "error_runtime"

    _kill_tiktok(ld_console, index, package)
    elapsed   = time.monotonic() - session_start
    like_rate = result["likes"] / max(1, result["videos_watched"]) * 100

    logger.info(
        f"[{vm['name']}] Ket thuc phien | "
        f"{result['videos_watched']} video | "
        f"{result['likes']} likes ({like_rate:.1f}%) | "
        f"{elapsed/60:.1f} phut | status: {result['status']}"
    )
    return result


# Toa do New York City cho GPS injection
_US_GPS_LLI = "-73.935242,40.730610"   # format: LNG,LAT (theo ldconsole)


def _inject_gps_all(cfg: dict, ready_vms: list) -> None:
    """
    Ban toa do GPS New York cho tung VM vua boot.
    Dung ldconsole action --name setlocate -- bat buoc TikTok lay vi tri My.
    """
    ld_console = cfg["_LD_CONSOLE"]
    logger.info(f"[GPS] Ep GPS New York City cho {len(ready_vms)} VM...")
    for vm in ready_vms:
        idx = vm["index"]
        try:
            r = subprocess.run(
                [ld_console, "action", "--name", "setlocate",
                 "--LLI", _US_GPS_LLI, "--index", str(idx)],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10,
            )
            if r.returncode == 0:
                logger.info(f"[GPS] [{vm['name']}] New York OK ({_US_GPS_LLI})")
            else:
                logger.warning(
                    f"[GPS] [{vm['name']}] setlocate warn (rc={r.returncode}): "
                    f"{(r.stdout+r.stderr).strip()[:80]}"
                )
        except Exception as exc:
            logger.warning(f"[GPS] [{vm['name']}] Exception: {exc}")


# ---------------------------------------------------------------------------
# Farm Orchestrator -- 3 buoc
# ---------------------------------------------------------------------------

def farm_all(cfg: dict, proxies: list,
             start_index: int = 1, end_index: int = 10) -> None:
    """
    Dieu phoi toan bo farm (3 buoc).

    Args:
        start_index: Index VM bat dau (inclusive, mac dinh 1).
        end_index:   Index VM ket thuc (inclusive, mac dinh 10).
                     Chi tinh index >= 1; index 0 bi loai tuyet doi.
    """
    farm_start = time.monotonic()

    # BUOC 1: Auto-Wake (chi VM trong khoang va khong phai index 0)
    ready_vms = auto_wake_all(cfg, start_index=start_index, end_index=end_index)
    if not ready_vms:
        logger.error("Khong co VM nao san sang sau Auto-Wake. Dung lai.")
        return

    # BUOC 1.5: Ep GPS New York City cho tung VM vua boot (ldconsole action setlocate)
    # Lam ngay sau wake, truoc khi mo TikTok, de TikTok thay vi tri My khi khoi dong
    _inject_gps_all(cfg, ready_vms)

    # BUOC 2: Pre-flight (proxy duoc slice theo so VM thuc te, khong hard-code 10)
    #   Map tuan tu: ready_vms[0] <-> proxies[0], ready_vms[1] <-> proxies[1], ...
    if len(proxies) < len(ready_vms):
        logger.error(
            f"Khong du proxy ({len(proxies)}) cho {len(ready_vms)} VM san sang. "
            "Them proxy vao data/proxies_list.txt."
        )
        return

    passed = preflight_check_all(cfg, ready_vms, proxies)
    if not passed:
        logger.error("Khong co VM nao pass Pre-flight. Tuyet doi khong mo TikTok.")
        return

    # BUOC 3: Farm session song song
    count = len(passed)
    logger.info("=" * 64)
    logger.info(f"  BUOC 3: FARM BAT DAU -- {count} VM (ThreadPoolExecutor)")
    logger.info("=" * 64)

    results = []
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = {
            pool.submit(run_session, vm, proxy, cfg): (vm, proxy)
            for vm, proxy in passed
        }
        for future in as_completed(futures):
            vm, proxy = futures[future]
            try:
                res = future.result()
                results.append(res)
            except Exception as exc:
                logger.error(f"[{vm['name']}] Future exception: {exc}")
                results.append({"vm": vm["name"], "status": "future_error"})

    # Tong ket
    elapsed      = time.monotonic() - farm_start
    total_videos = sum(r.get("videos_watched", 0) for r in results)
    total_likes  = sum(r.get("likes", 0) for r in results)
    ok_count     = sum(1 for r in results if r.get("status") == "ok")

    logger.info("=" * 64)
    logger.info(f"  FARM HOAN TAT trong {elapsed/60:.1f} phut")
    logger.info(f"  VM thanh cong: {ok_count}/{count}")
    logger.info(f"  Tong video da xem: {total_videos}")
    logger.info(f"  Tong luot like:    {total_likes} "
                f"({total_likes/max(1,total_videos)*100:.1f}%)")
    logger.info("=" * 64)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    valid = ("start", "session", "wake", "preflight")
    if len(sys.argv) < 2 or sys.argv[1] not in valid:
        print(f"\nCach dung: python {os.path.basename(__file__)} <command> [from N] [to M]\n")
        print("  start [from N] [to M]  -- Full pipeline (Wake->Preflight->Farm) [mac dinh: VM 1-5]")
        print("    VD: start            -> chay VM index 1 den 5 (TEST MODE mac dinh)")
        print("    VD: start from 1 to 10 -> chay toan bo 10 VM (Production mode)")
        print("  session <n>            -- Chay 1 phien thu cho VM index n")
        print("  wake [from N] [to M]   -- Chi chay Buoc 1: Auto-Wake")
        print("  preflight              -- Chi chay Buoc 2: Pre-flight check\n")
        sys.exit(1)

    command = sys.argv[1]
    cfg     = load_config()
    proxies = load_proxies()

    # TEST MODE: Mac dinh chi chay 5 VM dau (VM 1-5).
    # De chay toan bo 10 VM, goi: python tiktok_farmer.py start from 1 to 10
    def _parse_range(argv, default_start=1, default_end=5):
        """Trich xuat 'from N to M' tu argv. Vi du: ['start', 'from', '1', 'to', '5']"""
        s, e = default_start, default_end
        try:
            if "from" in argv:
                s = int(argv[argv.index("from") + 1])
            if "to" in argv:
                e = int(argv[argv.index("to") + 1])
        except (ValueError, IndexError):
            pass
        if s < 1:
            s = 1  # Khoa cung: tuyet doi khong cho index 0
        return s, e

    if command == "start":
        s, e = _parse_range(sys.argv)
        logger.info(f"RANGE: farm VM index {s} -> {e}")
        farm_all(cfg, proxies, start_index=s, end_index=e)

    elif command == "wake":
        s, e = _parse_range(sys.argv)
        logger.info(f"RANGE: wake VM index {s} -> {e}")
        ready = auto_wake_all(cfg, start_index=s, end_index=e)
        logger.info(f"Wake xong: {len(ready)} VM san sang.")

    elif command == "preflight":
        running = _ld_get_running(cfg["_LD_CONSOLE"])
        if not running:
            logger.error("Khong co VM nao dang chay. Chay 'wake' truoc.")
            sys.exit(1)
        passed = preflight_check_all(cfg, running, proxies)
        logger.info(f"Pre-flight xong: {len(passed)} VM pass.")

    elif command == "session":
        if len(sys.argv) < 3:
            print("Thieu index VM. Vi du: python tiktok_farmer.py session 1")
            sys.exit(1)
        try:
            idx = int(sys.argv[2])
        except ValueError:
            print("Index phai la so nguyen.")
            sys.exit(1)

        count = cfg["INSTANCE_COUNT"]
        if not (1 <= idx <= count):
            print(f"Index phai nam trong [1, {count}].")
            sys.exit(1)
        if len(proxies) < idx:
            print(f"Khong co proxy cho VM {idx}.")
            sys.exit(1)

        # Tim VM theo index trong danh sach dang chay
        running = _ld_get_running(cfg["_LD_CONSOLE"])
        vm_match = next((v for v in running if v["index"] == idx), None)
        if not vm_match:
            # Neu khong tim thay trong running, tao provisional vm dict
            vm_match = {"index": idx, "name": f"TikTok_US_{idx:02d}"}
            logger.warning(f"VM index {idx} khong co trong danh sach dang chay. Thu chay thu.")

        result = run_session(vm_match, proxies[idx - 1], cfg)
        logger.info(f"Session result: {result}")


if __name__ == "__main__":
    main()
