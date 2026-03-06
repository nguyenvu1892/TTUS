# -*- coding: utf-8 -*-
"""
proxy_manager.py -- Task 2: Tu dong cai dat va cau hinh SocksDroid tren 10 VM LDPlayer.

ADB Connection Mode:
    Tat ca lenh ADB dung truc tiep adb.exe qua localhost port.
    LDPlayer port formula: VM index i (0-based) -> port = 5555 + (i * 2)
    Vi du: TikTok_US_01 (index=0) -> 127.0.0.1:5555
            TikTok_US_02 (index=1) -> 127.0.0.1:5557 ...
    Ly do: ldconsole adb bridge crash voi exit code 0xC0000409 (ACCESS_VIOLATION).

CLI:
    python proxy_manager.py setup       # Tai APK + Cai + Cau hinh + Verify
    python proxy_manager.py download    # Chi tai APK
    python proxy_manager.py install     # Chi cai APK (yeu cau APK da co)
    python proxy_manager.py configure   # Chi cau hinh proxy (Root ADB -> SharedPrefs)
    python proxy_manager.py verify      # Chi kiem tra trang thai proxy tren 10 VM
"""

# ============================================================
# STEP 0 -- AUTO-BOOTSTRAP DEPENDENCIES
# ============================================================
import subprocess
import sys


def _bootstrap_deps():
    """Kiem tra va tu dong cai dat cac thu vien can thiet qua pip."""
    required = {
        "requests": "requests>=2.28.0",
        "colorama": "colorama>=0.4.6",
    }
    missing = []
    for module_name, pip_spec in required.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(pip_spec)

    if missing:
        print(f"[BOOTSTRAP] Dang cai dat thu vien thieu: {', '.join(missing)}")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
            check=False,
        )
        if result.returncode != 0:
            print("[BOOTSTRAP] FAIL. Chay thu cong: pip install " + " ".join(missing))
            sys.exit(1)
        print("[BOOTSTRAP] OK - Thu vien da san sang.\n")


_bootstrap_deps()

# ============================================================
# IMPORTS
# ============================================================
import json
import logging
import os
import time

import colorama
import requests
from colorama import Fore, Style

colorama.init(autoreset=True)

# ============================================================
# Logging
# ============================================================
LOG_FILE = os.path.join("data", "ld_manager.log")
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ============================================================
# Constants
# ============================================================
CONFIG_FILE  = "config.json"
PROXIES_FILE = os.path.join("data", "proxies_list.txt")

SOCKSDROID_PACKAGE    = "net.typeblog.socks"
SOCKSDROID_ACTIVITY   = "net.typeblog.socks/.MainActivity"
SOCKSDROID_PREFS_DIR  = f"/data/data/{SOCKSDROID_PACKAGE}/shared_prefs"
SOCKSDROID_PREFS_FILE = f"{SOCKSDROID_PREFS_DIR}/{SOCKSDROID_PACKAGE}_preferences.xml"

SOCKSDROID_GITHUB_API   = "https://api.github.com/repos/bndeff/socksdroid/releases/latest"
SOCKSDROID_FALLBACK_URL = (
    "https://github.com/bndeff/socksdroid/releases/download/1.0.4/socksdroid-1.0.4.apk"
)

# LDPlayer ADB port formula: port = 5555 + (ldplayer_index * 2)
# TikTok_US_01 -> index=0 -> port 5555
# TikTok_US_02 -> index=1 -> port 5557 ... TikTok_US_10 -> index=9 -> port 5573
ADB_BASE_PORT = 5555

INSTALL_DELAY_SEC   = 2
CONFIGURE_DELAY_SEC = 1


# ============================================================
# ANSI Color Helpers
# ============================================================
def _warn(msg): return f"{Fore.YELLOW}{Style.BRIGHT}{msg}{Style.RESET_ALL}"
def _err(msg):  return f"{Fore.RED}{Style.BRIGHT}{msg}{Style.RESET_ALL}"
def _ok(msg):   return f"{Fore.GREEN}{Style.BRIGHT}{msg}{Style.RESET_ALL}"
def _info(msg): return f"{Fore.CYAN}{msg}{Style.RESET_ALL}"


# ============================================================
# Config & Data Loading
# ============================================================

def load_config() -> dict:
    """Doc config.json, validate cac key bat buoc va tra ve dict cau hinh."""
    if not os.path.isfile(CONFIG_FILE):
        logger.error(_err(f"Khong tim thay file cau hinh: {CONFIG_FILE}"))
        sys.exit(1)

    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)

    required_keys = ["LDPLAYER_PATH", "INSTANCE_COUNT", "INSTANCE_PREFIX", "SOCKSDROID_APK_PATH"]
    for key in required_keys:
        if key not in cfg:
            logger.error(_err(f"config.json thieu key bat buoc: '{key}'"))
            sys.exit(1)

    console_path = os.path.join(cfg["LDPLAYER_PATH"], "ldconsole.exe")
    if not os.path.isfile(console_path):
        logger.error(_err(f"Khong tim thay ldconsole.exe tai: {console_path}"))
        sys.exit(1)

    cfg["_LD_CONSOLE"] = console_path
    cfg["_ADB_EXE"]    = _find_adb(cfg)
    return cfg


def load_proxies(path: str = PROXIES_FILE) -> list:
    """Parse file proxy list (IP:Port:User:Pass). Bo qua dong trong va comment."""
    if not os.path.isfile(path):
        logger.error(_err(f"Khong tim thay file proxy: {path}"))
        sys.exit(1)

    proxies = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) != 4:
                logger.warning(_warn(f"Dong {lineno} khong dung format, bo qua: {line}"))
                continue
            ip, port_str, user, pwd = parts
            try:
                port = int(port_str)
            except ValueError:
                logger.warning(_warn(f"Dong {lineno}: Port khong hop le, bo qua."))
                continue
            proxies.append({"ip": ip.strip(), "port": port,
                            "user": user.strip(), "pass": pwd.strip()})

    logger.info(_info(f"Da load {len(proxies)} proxy tu {path}"))
    return proxies


# ============================================================
# APK Auto-Download
# ============================================================

def download_apk(apk_path: str) -> bool:
    """Tu dong tai SocksDroid APK tu GitHub neu file chua ton tai."""
    abs_path = os.path.abspath(apk_path)
    if os.path.isfile(abs_path):
        size_mb = os.path.getsize(abs_path) / (1024 * 1024)
        logger.info(_ok(f"APK da ton tai ({size_mb:.1f} MB): {abs_path}"))
        return True

    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    download_url = _resolve_apk_url()
    logger.info(_info(f"Dang tai SocksDroid APK tu:\n  {download_url}"))
    print(_info("Dang tai APK... (co the mat vai giay)"))

    try:
        with requests.get(download_url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total      = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(abs_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded / total * 100
                            if int(pct) % 10 == 0 and pct > 0:
                                bar = "#" * int(pct // 5) + "-" * (20 - int(pct // 5))
                                print(f"\r  [{bar}] {pct:.0f}%  "
                                      f"({downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB)",
                                      end="", flush=True)
            print()
        size_mb = os.path.getsize(abs_path) / (1024 * 1024)
        logger.info(_ok(f"Tai APK thanh cong ({size_mb:.1f} MB) -> {abs_path}"))
        return True
    except requests.RequestException as exc:
        logger.error(_err(f"Loi khi tai APK: {exc}"))
        if os.path.isfile(abs_path):
            os.remove(abs_path)
        return False


def _resolve_apk_url() -> str:
    """Dung GitHub API de tim URL APK ban moi nhat. Fallback ve URL hardcoded."""
    try:
        resp = requests.get(SOCKSDROID_GITHUB_API, timeout=10)
        resp.raise_for_status()
        data   = resp.json()
        assets = data.get("assets", [])
        for asset in assets:
            name = asset.get("name", "")
            if name.endswith(".apk"):
                url = asset.get("browser_download_url", "")
                if url:
                    logger.info(_info(f"GitHub API -> phien ban: {data.get('tag_name','?')} ({name})"))
                    return url
    except Exception as exc:
        logger.warning(_warn(f"GitHub API khong kha dung ({exc}). Dung URL fallback."))
    logger.info(_info(f"Fallback URL: {SOCKSDROID_FALLBACK_URL}"))
    return SOCKSDROID_FALLBACK_URL


# ============================================================
# Direct ADB Helpers (thay the ldconsole adb bridge)
# ============================================================

def _find_adb(cfg: dict) -> str:
    """
    Tim duong dan den adb.exe.
    Thu tu uu tien:
      1. {LDPLAYER_PATH}/adb.exe  -- adb.exe di kem LDPlayer
      2. "adb"                    -- adb da co trong system PATH
    """
    candidate = os.path.join(cfg["LDPLAYER_PATH"], "adb.exe")
    if os.path.isfile(candidate):
        logger.info(_info(f"Dung ADB tai: {candidate}"))
        return candidate

    # Thu system PATH
    try:
        result = subprocess.run(["adb", "version"], capture_output=True, timeout=5)
        if result.returncode == 0:
            logger.info(_info("Dung ADB tu system PATH"))
            return "adb"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    logger.error(_err(
        f"Khong tim thay adb.exe. Da thu: {candidate} va system PATH.\n"
        "Hay cai Android SDK Platform-Tools hoac dat LDPLAYER_PATH chinh xac trong config.json."
    ))
    sys.exit(1)


def _adb_port(vm_number: int) -> int:
    """
    Tinh port ADB cho VM thu vm_number (1-based).
    LDPlayer index = vm_number - 1 (0-based).
    Port = 5555 + (ldplayer_index * 2).

    TikTok_US_01 (index=0) -> 5555
    TikTok_US_02 (index=1) -> 5557
    ...
    TikTok_US_10 (index=9) -> 5573
    """
    return ADB_BASE_PORT + ((vm_number - 1) * 2)


def _adb_connect(adb_exe: str, port: int) -> bool:
    """
    Ket noi ADB den may ao qua localhost:<port>.
    Returns True neu ket noi thanh cong hoac da ket noi truoc do.
    """
    try:
        result = subprocess.run(
            [adb_exe, "connect", f"127.0.0.1:{port}"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        output = (result.stdout + result.stderr).strip()
        if "connected" in output.lower() or "already" in output.lower():
            return True
        logger.warning(_warn(f"ADB connect 127.0.0.1:{port}: {output}"))
        return False
    except subprocess.TimeoutExpired:
        logger.error(_err(f"ADB connect timeout: port {port}"))
        return False
    except FileNotFoundError:
        logger.error(_err(f"Khong tim thay adb.exe: {adb_exe}"))
        return False


def _adb_shell_su(adb_exe: str, port: int, shell_cmd: str, timeout: int = 30) -> tuple:
    """
    Chay lenh shell voi quyen root tren may ao qua ADB truc tiep.
    Lenh: adb -s 127.0.0.1:<port> shell su -c '<shell_cmd>'

    Returns: (success: bool, output: str)
    """
    serial = f"127.0.0.1:{port}"
    # Wrap shell_cmd trong dau ngoac kep de su -c xu ly dung
    full_cmd = [adb_exe, "-s", serial, "shell", "su", "-c", shell_cmd]
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        # su loi thu'ong tra ve exit code != 0 hoac co "Permission denied"
        if result.returncode != 0 or "Permission denied" in output:
            logger.warning(_warn(f"[{serial}] su -c that bai (rc={result.returncode}): {output[:120]}"))
            return False, output
        return True, output
    except subprocess.TimeoutExpired:
        logger.error(_err(f"[{serial}] ADB su timeout: {shell_cmd[:60]}"))
        return False, "TIMEOUT"
    except Exception as exc:
        logger.error(_err(f"[{serial}] ADB exception: {exc}"))
        return False, str(exc)


def _adb_shell(adb_exe: str, port: int, shell_cmd: str, timeout: int = 30) -> tuple:
    """Chay lenh shell khong can root (cho install, isrunning...). Returns (success, output)."""
    serial = f"127.0.0.1:{port}"
    full_cmd = [adb_exe, "-s", serial, "shell", shell_cmd]
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as exc:
        return False, str(exc)


# ============================================================
# LDConsole Wrapper (chi dung cho: install, isrunning, list)
# ============================================================

def _ld_command(ld_console: str, *args) -> tuple:
    """Goi ldconsole.exe cho cac tac vu KHONG phai ADB shell. Returns (success, output)."""
    cmd = [ld_console] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            logger.warning(_warn(f"ldconsole exit {result.returncode}: {output[:80]}"))
            return False, output
        return True, output
    except subprocess.TimeoutExpired:
        logger.error(_err(f"Timeout ldconsole: {args}"))
        return False, "TIMEOUT"
    except Exception as exc:
        logger.error(_err(f"Loi ldconsole: {exc}"))
        return False, str(exc)


def _is_running(ld_console: str, index: int) -> bool:
    """Kiem tra VM (0-based index) co dang chay khong."""
    ok, output = _ld_command(ld_console, "isrunning", "--index", str(index))
    return ok and "running" in output.lower()


# ============================================================
# SharedPrefs XML Builder
# ============================================================

def _build_prefs_xml(proxy: dict) -> str:
    """Tao noi dung SharedPreferences XML cho SocksDroid."""
    return (
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>"
        "<map>"
        f"<string name=\"proxy_server\">{proxy['ip']}</string>"
        f"<string name=\"proxy_port\">{proxy['port']}</string>"
        f"<string name=\"proxy_username\">{proxy['user']}</string>"
        f"<string name=\"proxy_password\">{proxy['pass']}</string>"
        "<boolean name=\"ipv6\" value=\"false\" />"
        "<boolean name=\"udp_forward\" value=\"false\" />"
        "<boolean name=\"per_app\" value=\"false\" />"
        "</map>"
    )


# ============================================================
# Install, Configure, Verify
# ============================================================

def install_app(ld_console: str, index_0: int, apk_path: str) -> bool:
    """Cai APK SocksDroid vao VM (0-based index) qua ldconsole installapp."""
    abs_apk = os.path.abspath(apk_path)
    if not os.path.isfile(abs_apk):
        logger.error(_err(f"[VM {index_0+1:02d}] APK khong ton tai: {abs_apk}"))
        return False

    if not _is_running(ld_console, index_0):
        logger.warning(_warn(f"[VM {index_0+1:02d}] VM chua chay -- bo qua install."))
        return False

    logger.info(f"[VM {index_0+1:02d}] Dang cai SocksDroid APK ...")
    ok, output = _ld_command(ld_console, "installapp",
                             "--index", str(index_0), "--filename", abs_apk)
    if ok:
        logger.info(_ok(f"[VM {index_0+1:02d}] Cai APK thanh cong."))
    else:
        logger.error(_err(f"[VM {index_0+1:02d}] Cai APK that bai: {output}"))
    return ok


def configure_proxy(adb_exe: str, vm_number: int, proxy: dict) -> bool:
    """
    Ghi cau hinh SOCKS5 proxy truc tiep vao SharedPreferences cua SocksDroid
    bang kiet noi ADB goc qua port localhost, chay lenh voi su -c (root).

    Port: 5555 + ((vm_number - 1) * 2)
    Quy trinh:
      1. adb connect 127.0.0.1:<port>
      2. su -c force-stop SocksDroid
      3. su -c mkdir SharedPrefs dir
      4. su -c ghi XML file bang printf
      5. su -c chmod 660 + chown
      6. su -c am start SocksDroid
    """
    port   = _adb_port(vm_number)
    serial = f"127.0.0.1:{port}"
    label  = f"[VM {vm_number:02d}] [{serial}]"

    logger.info(f"{label} Dang cau hinh proxy -> {proxy['ip']}:{proxy['port']} ...")

    # 1. Ket noi ADB
    if not _adb_connect(adb_exe, port):
        logger.error(_err(f"{label} Khong the ket noi ADB. VM co the chua chay hoac ADB chua bat."))
        return False

    # 2. Force-stop SocksDroid truoc khi ghi file
    _adb_shell_su(adb_exe, port, f"am force-stop {SOCKSDROID_PACKAGE}")
    time.sleep(0.4)

    # 3. Tao thu muc SharedPrefs
    _adb_shell_su(adb_exe, port, f"mkdir -p {SOCKSDROID_PREFS_DIR}")

    # 4. Ghi XML SharedPrefs bang printf (an toan voi cac ky tu dac biet trong password)
    ip   = proxy["ip"].replace("'", "\\'").replace('"', '\\"')
    port_str = str(proxy["port"])
    user = proxy["user"].replace("'", "\\'").replace('"', '\\"')
    pwd  = proxy["pass"].replace("'", "\\'").replace('"', '\\"')

    xml_content = (
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>"
        "<map>"
        f"<string name='proxy_server'>{ip}</string>"
        f"<string name='proxy_port'>{port_str}</string>"
        f"<string name='proxy_username'>{user}</string>"
        f"<string name='proxy_password'>{pwd}</string>"
        "<boolean name='ipv6' value='false' />"
        "<boolean name='udp_forward' value='false' />"
        "<boolean name='per_app' value='false' />"
        "</map>"
    )
    # Dung printf voi echo de tranh van de xuong dong
    write_cmd = f"echo {repr(xml_content)} > {SOCKSDROID_PREFS_FILE}"
    # Su dung cat heredoc thay vi echo de an toan hon
    write_cmd = (
        f"printf '%s' "
        f'"{xml_content.replace(chr(34), chr(92)+chr(34))}" '
        f"> {SOCKSDROID_PREFS_FILE}"
    )
    ok_write, out_write = _adb_shell_su(adb_exe, port, write_cmd)
    if not ok_write:
        # Thu cach viet don gian hon: dung cat voi stdin
        # Tao XML khong co dau ngoac kep ben trong de de escape
        simple_write = (
            f"echo \"<?xml version='1.0' encoding='utf-8' standalone='yes' ?>"
            f"<map>"
            f"<string name='proxy_server'>{ip}</string>"
            f"<string name='proxy_port'>{port_str}</string>"
            f"<string name='proxy_username'>{user}</string>"
            f"<string name='proxy_password'>{pwd}</string>"
            f"</map>\" > {SOCKSDROID_PREFS_FILE}"
        )
        ok_write, out_write = _adb_shell_su(adb_exe, port, simple_write)
        if not ok_write:
            logger.error(_err(f"{label} Ghi SharedPrefs that bai: {out_write[:120]}"))
            logger.error(_err(f"{label} Kiem tra: 1) Root da bat chua? 2) ADB daemon chay chua? 3) Dung port {port}?"))
            return False

    # 5. Set quyen file va owner
    _adb_shell_su(adb_exe, port, f"chmod 660 {SOCKSDROID_PREFS_FILE}")
    # Lay owner cua thu muc package va gan cho file prefs
    _adb_shell_su(adb_exe, port,
                  f"chown $(stat -c '%u:%g' {SOCKSDROID_PREFS_DIR}) {SOCKSDROID_PREFS_FILE}")

    # 6. Khoi dong SocksDroid
    ok_start, _ = _adb_shell_su(adb_exe, port,
                                 f"am start -n {SOCKSDROID_ACTIVITY} --ez intent_start true")
    if ok_start:
        logger.info(_ok(f"{label} Cau hinh proxy va khoi dong SocksDroid thanh cong."))
    else:
        logger.warning(_warn(f"{label} Ghi SharedPrefs OK nhung am start that bai. Kiem tra thu cong."))

    return True  # Thanh cong neu ghi duoc SharedPrefs


def verify_proxy(adb_exe: str, vm_number: int, proxy: dict) -> bool:
    """
    Xac minh proxy da duoc nap vao SocksDroid bang cach doc lai SharedPrefs.
    Returns True neu IP + Port co mat trong file.
    """
    port   = _adb_port(vm_number)
    serial = f"127.0.0.1:{port}"
    label  = f"[VM {vm_number:02d}] [{serial}]"

    logger.info(f"{label} Dang xac minh proxy ({proxy['ip']}:{proxy['port']}) ...")

    if not _adb_connect(adb_exe, port):
        logger.error(_err(f"{label} VERIFY FAIL: Khong the ket noi ADB."))
        return False

    ok, output = _adb_shell_su(adb_exe, port,
                                f"cat {SOCKSDROID_PREFS_FILE} 2>/dev/null")
    if not ok or not output.strip():
        logger.error(_err(f"{label} VERIFY FAIL: Khong doc duoc SharedPrefs. "
                          f"Root chua hoat dong hoac file chua duoc ghi."))
        return False

    ip_ok   = proxy["ip"]   in output
    port_ok = str(proxy["port"]) in output

    if ip_ok and port_ok:
        logger.info(_ok(f"{label} VERIFY OK: {proxy['ip']}:{proxy['port']} da co trong SharedPrefs."))
        return True
    else:
        logger.error(_err(
            f"{label} VERIFY FAIL: IP_found={ip_ok}, Port_found={port_ok}.\n"
            f"SharedPrefs hien tai: {output[:200]}"
        ))
        return False


# ============================================================
# High-level Pipeline
# ============================================================

def install_all(cfg: dict, proxies: list) -> None:
    """Cai APK SocksDroid vao tat ca VM dang chay."""
    ld_console = cfg["_LD_CONSOLE"]
    apk_path   = cfg["SOCKSDROID_APK_PATH"]
    count      = cfg["INSTANCE_COUNT"]

    print(_info("=" * 64))
    print(_info(f"  BAT DAU: Cai APK SocksDroid vao {count} VM ..."))
    print(_info("=" * 64))

    success, failed = 0, 0
    for i in range(1, count + 1):
        ok = install_app(ld_console, i - 1, apk_path)  # 0-based index
        if ok:
            success += 1
        else:
            failed += 1
        if i < count:
            time.sleep(INSTALL_DELAY_SEC)

    logger.info(f"INSTALL XONG: {success} thanh cong / {failed} that bai / {count} tong")


def configure_all(cfg: dict, proxies: list) -> None:
    """Cau hinh SOCKS5 proxy cho tat ca VM qua Direct ADB."""
    adb_exe = cfg["_ADB_EXE"]
    count   = cfg["INSTANCE_COUNT"]

    if len(proxies) < count:
        logger.error(_err(f"Can {count} proxy, hien co {len(proxies)}. Kiem tra {PROXIES_FILE}."))
        sys.exit(1)

    print(_info("=" * 64))
    print(_info(f"  BAT DAU: Cau hinh proxy (Direct ADB su -c) cho {count} VM ..."))
    print(_info(f"  Port range: {_adb_port(1)} -- {_adb_port(count)}"))
    print(_info("=" * 64))

    success, failed = 0, 0
    for i in range(1, count + 1):
        proxy = proxies[i - 1]
        ok    = configure_proxy(adb_exe, i, proxy)
        if ok:
            success += 1
        else:
            failed += 1
        if i < count:
            time.sleep(CONFIGURE_DELAY_SEC)

    summary = f"CONFIGURE XONG: {success} thanh cong / {failed} that bai / {count} tong"
    logger.info(_ok(summary) if failed == 0 else _warn(summary))


def verify_all(cfg: dict, proxies: list) -> dict:
    """Xac minh proxy da duoc nap tren tat ca VM. Tra ve dict ket qua."""
    adb_exe = cfg["_ADB_EXE"]
    count   = cfg["INSTANCE_COUNT"]

    if len(proxies) < count:
        logger.error(_err(f"Can {count} proxy de verify."))
        sys.exit(1)

    print(_info("=" * 64))
    print(_info(f"  VERIFY: Kiem tra SharedPrefs tren {count} VM ..."))
    print(_info("=" * 64))

    results = {}
    for i in range(1, count + 1):
        ok = verify_proxy(adb_exe, i, proxies[i - 1])
        results[f"TikTok_US_{i:02d}"] = "OK" if ok else "FAIL"

    ok_count   = sum(1 for v in results.values() if v == "OK")
    fail_count = count - ok_count
    logger.info(_ok(f"VERIFY XONG: {ok_count} OK / {fail_count} FAIL / {count} tong")
                if fail_count == 0 else
                _warn(f"VERIFY XONG: {ok_count} OK / {fail_count} FAIL / {count} tong"))

    print()
    print(f"  {'VM':<20} {'Port':<8} {'Proxy':<35} {'Status'}")
    print("  " + "-" * 72)
    for i in range(1, count + 1):
        name   = f"TikTok_US_{i:02d}"
        proxy  = proxies[i - 1]
        port   = _adb_port(i)
        status = results[name]
        color  = _ok(status) if status == "OK" else _err(status)
        print(f"  {name:<20} {port:<8} {proxy['ip']}:{proxy['port']:<26} {color}")
    print()
    return results


def setup_all(cfg: dict, proxies: list) -> None:
    """Full pipeline: Tai APK -> Cai -> Cau hinh -> Verify cho 10 VM."""
    apk_path = cfg["SOCKSDROID_APK_PATH"]
    if not download_apk(apk_path):
        logger.error(_err("Khong the tai APK."))
        sys.exit(1)

    install_all(cfg, proxies)
    configure_all(cfg, proxies)
    logger.info("Cho SocksDroid khoi dong (5s) ...")
    time.sleep(5)
    verify_all(cfg, proxies)


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    valid_commands = ("setup", "download", "install", "configure", "verify")
    if len(sys.argv) < 2 or sys.argv[1] not in valid_commands:
        print(f"\n{_info('Cach dung:')} python {os.path.basename(__file__)} <command>\n")
        print(f"  {Fore.CYAN}setup{Style.RESET_ALL}      -- Tai APK + Cai + Cau hinh (Direct ADB) + Verify")
        print(f"  {Fore.CYAN}download{Style.RESET_ALL}   -- Chi tai APK SocksDroid tu GitHub")
        print(f"  {Fore.CYAN}install{Style.RESET_ALL}    -- Chi cai APK vao tat ca VM dang chay")
        print(f"  {Fore.CYAN}configure{Style.RESET_ALL}  -- Chi ghi SharedPrefs proxy (Direct ADB su -c)")
        print(f"  {Fore.CYAN}verify{Style.RESET_ALL}     -- Kiem tra proxy da duoc nap thanh cong\n")
        sys.exit(1)

    command = sys.argv[1]
    cfg     = load_config()
    proxies = load_proxies()

    if command == "download":
        ok = download_apk(cfg["SOCKSDROID_APK_PATH"])
        sys.exit(0 if ok else 1)
    elif command == "install":
        if not os.path.isfile(cfg["SOCKSDROID_APK_PATH"]):
            logger.error(_err("APK chua ton tai. Chay truoc: python proxy_manager.py download"))
            sys.exit(1)
        install_all(cfg, proxies)
    elif command == "configure":
        configure_all(cfg, proxies)
    elif command == "verify":
        verify_all(cfg, proxies)
    elif command == "setup":
        setup_all(cfg, proxies)


if __name__ == "__main__":
    main()
