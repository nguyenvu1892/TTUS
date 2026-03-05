# -*- coding: utf-8 -*-
"""
proxy_manager.py -- Task 2: Tu dong cai dat va cau hinh SocksDroid tren 10 VM LDPlayer.

Luong:
    0. _bootstrap_deps()     -- Tu kiem tra va pip install thu vien can thiet
    1. load_config()         -- Doc config.json
    2. load_proxies()        -- Parse data/proxies_list.txt -> list[dict]
    3. download_apk()        -- Tu tai SocksDroid APK tu GitHub neu chua co
    4. install_app(i)        -- ldconsole installapp -> cai APK vao VM index i
    5. configure_proxy(i)    -- Root ADB: ghi SharedPrefs truc tiep vao /data/data/...
    6. verify_proxy(i)       -- Root ADB: doc lai SharedPrefs, xac nhan proxy da nap
    7. setup_all()           -- Full pipeline cho 10 VM

CLI:
    python proxy_manager.py setup       # Tai APK (neu can) + Cai + Cau hinh + Verify
    python proxy_manager.py download    # Chi tai APK
    python proxy_manager.py install     # Chi cai APK (yeu cau APK da co)
    python proxy_manager.py configure   # Chi cau hinh proxy (APK da duoc cai)
    python proxy_manager.py verify      # Chi kiem tra trang thai proxy tren 10 VM

Luu y Root ADB:
    - May ao can duoc cau hinh voi --root 1 --adb 1 (ld_manager.py da xu ly)
    - Lenh ADB duoc chay qua: ldconsole adb --index i --command "su -c '...'"
    - SharedPrefs duoc ghi truc tiep vao /data/data/net.typeblog.socks/shared_prefs/
      tranh hoan toan viec hien thi VPN permission dialog
"""

# ============================================================
# STEP 0 -- AUTO-BOOTSTRAP DEPENDENCIES
# Phai chay truoc MOI import cua thu vien ben ngoai.
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

# SocksDroid -- fork bndeff/socksdroid (GPL-3.0)
# Package name cua ban fork duoc su dung:
SOCKSDROID_PACKAGE    = "net.typeblog.socks"
SOCKSDROID_ACTIVITY   = "net.typeblog.socks/.MainActivity"

# SharedPreferences path tren may ao (yeu cau root):
#   /data/data/<package>/shared_prefs/<prefs_file>
# Ten file SharedPrefs duoc lay tu source code SocksDroid (net.typeblog.socks_preferences.xml)
SOCKSDROID_PREFS_DIR  = f"/data/data/{SOCKSDROID_PACKAGE}/shared_prefs"
SOCKSDROID_PREFS_FILE = f"{SOCKSDROID_PREFS_DIR}/{SOCKSDROID_PACKAGE}_preferences.xml"

SOCKSDROID_GITHUB_API   = "https://api.github.com/repos/bndeff/socksdroid/releases/latest"
SOCKSDROID_FALLBACK_URL = (
    "https://github.com/bndeff/socksdroid/releases/download/1.0.4/socksdroid-1.0.4.apk"
)

INSTALL_DELAY_SEC   = 2
CONFIGURE_DELAY_SEC = 1


# ============================================================
# ANSI Color Helpers
# ============================================================
def _warn(msg): return f"{Fore.YELLOW}{Style.BRIGHT}{msg}{Style.RESET_ALL}"
def _err(msg):  return f"{Fore.RED}{Style.BRIGHT}{msg}{Style.RESET_ALL}"
def _ok(msg):   return f"{Fore.GREEN}{Style.BRIGHT}{msg}{Style.RESET_ALL}"
def _info(msg): return f"{Fore.CYAN}{msg}{Style.RESET_ALL}"


def _print_root_check_warning(index: int):
    """In canh bao neu root chua duoc bat trong may ao."""
    print()
    print(_warn("=" * 64))
    print(_warn(f"  [VM {index:02d}] ROOT CHUA DUOC BAT!"))
    print(_warn("  Chay 'python ld_manager.py configure' truoc de bat:"))
    print(_warn("    --root 1  (bat quyen root)"))
    print(_warn("    --adb 1   (bat ADB mang noi bo)"))
    print(_warn("=" * 64))
    print()


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
    return cfg


def load_proxies(path: str = PROXIES_FILE) -> list:
    """
    Parse file proxy list, bo qua dong trong va comment (#).
    Format moi dong: IP:Port:Username:Password
    Returns: list of dict {"ip", "port", "user", "pass"}
    """
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
                logger.warning(_warn(f"Dong {lineno} khong dung format (IP:Port:User:Pass), bo qua: {line}"))
                continue
            ip, port_str, user, pwd = parts
            try:
                port = int(port_str)
            except ValueError:
                logger.warning(_warn(f"Dong {lineno}: Port khong phai so nguyen ('{port_str}'), bo qua."))
                continue
            proxies.append({"ip": ip.strip(), "port": port, "user": user.strip(), "pass": pwd.strip()})

    logger.info(_info(f"Da load {len(proxies)} proxy tu {path}"))
    return proxies


# ============================================================
# APK Auto-Download
# ============================================================

def download_apk(apk_path: str) -> bool:
    """
    Tu dong tai SocksDroid APK tu GitHub neu file chua ton tai.
    1. Goi GitHub Releases API de lay URL APK ban moi nhat.
    2. Fallback ve URL hardcoded (v1.0.4) neu API loi.
    3. Streaming download voi progress bar.
    """
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
                                print(f"\r  [{bar}] {pct:.0f}%  ({downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB)",
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
# Core LDConsole Wrapper
# ============================================================

def _ld_command(ld_console: str, *args) -> tuple:
    """Goi ldconsole.exe. Returns (success: bool, output: str)."""
    cmd = [ld_console] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            logger.warning(_warn(f"ldconsole exit {result.returncode}: {output[:120]}"))
            return False, output
        return True, output
    except subprocess.TimeoutExpired:
        logger.error(_err(f"Timeout ldconsole: {args}"))
        return False, "TIMEOUT"
    except Exception as exc:
        logger.error(_err(f"Loi ldconsole: {exc}"))
        return False, str(exc)


def _adb_su(ld_console: str, index: int, shell_cmd: str) -> tuple:
    """
    Chay lenh ADB voi quyen root (su -c) qua ldconsole bridge.

    Ky thuat: ldconsole adb --index i --command "su -c '<shell_cmd>'"
    Yeu cau:  may ao da duoc cau hinh --root 1 --adb 1 (ld_manager.py).

    Returns: (success: bool, output: str)
    """
    # Dung dau ngoac kep ben ngoai, dau phay don ben trong su -c
    adb_cmd = f"su -c '{shell_cmd}'"
    return _ld_command(ld_console, "adb", "--index", str(index), "--command", adb_cmd)


def _is_running(ld_console: str, index: int) -> bool:
    """Kiem tra VM index co dang chay khong."""
    ok, output = _ld_command(ld_console, "isrunning", "--index", str(index))
    return ok and "running" in output.lower()


# ============================================================
# SharedPrefs XML builder
# ============================================================

def _build_prefs_xml(proxy: dict) -> str:
    """
    Tao noi dung file SharedPreferences XML cho SocksDroid.

    Key names duoc lay tu source code cua bndeff/socksdroid:
      proxy_server   -> IP/hostname cua SOCKS5 server
      proxy_port     -> Port (luu la string trong SharedPrefs)
      proxy_username -> Username xac thuc
      proxy_password -> Password xac thuc
    """
    xml = (
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
    return xml


# ============================================================
# Install, Configure, Verify
# ============================================================

def install_app(ld_console: str, index: int, apk_path: str) -> bool:
    """Cai APK SocksDroid vao VM index qua ldconsole installapp."""
    abs_apk = os.path.abspath(apk_path)
    if not os.path.isfile(abs_apk):
        logger.error(_err(f"[VM {index:02d}] APK khong ton tai: {abs_apk}"))
        return False

    if not _is_running(ld_console, index):
        logger.warning(_warn(f"[VM {index:02d}] VM chua chay -- bo qua install."))
        return False

    logger.info(f"[VM {index:02d}] Dang cai SocksDroid APK ...")
    ok, output = _ld_command(ld_console, "installapp", "--index", str(index), "--filename", abs_apk)
    if ok:
        logger.info(_ok(f"[VM {index:02d}] Cai APK thanh cong."))
    else:
        logger.error(_err(f"[VM {index:02d}] Cai APK that bai: {output}"))
    return ok


def configure_proxy(ld_console: str, index: int, proxy: dict) -> bool:
    """
    Ghi cau hinh proxy SOCKS5 truc tiep vao SharedPreferences cua SocksDroid
    bang quyen root (su -c), sau do khoi dong app.

    Quy trinh Root ADB:
      1. Force-stop SocksDroid (dam bao app khong lock file)
      2. Tao thu muc SharedPrefs neu chua co
      3. Ghi XML SharedPrefs bang: su -c "echo '<xml>' > /data/data/.../prefs.xml"
      4. Set quyen file (660) va owner dung voi package
      5. Khoi dong SocksDroid: su -c "am start -n ..."

    KHONG co VPN permission dialog vi app duoc khoi dong bang root.
    """
    if not _is_running(ld_console, index):
        logger.warning(_warn(f"[VM {index:02d}] VM chua chay -- bo qua configure."))
        return False

    logger.info(f"[VM {index:02d}] Dang cau hinh proxy -> {proxy['ip']}:{proxy['port']} (user: {proxy['user']}) ...")

    # 1. Force-stop SocksDroid truoc khi ghi file
    _adb_su(ld_console, index, f"am force-stop {SOCKSDROID_PACKAGE}")
    time.sleep(0.5)

    # 2. Tao thu muc SharedPrefs
    _adb_su(ld_console, index, f"mkdir -p {SOCKSDROID_PREFS_DIR}")

    # 3. Build va ghi XML SharedPrefs
    xml_content = _build_prefs_xml(proxy)
    # Dung printf de tranh van de escape voi cac ky tu dac biet trong password
    # Thay the kep don trong proxy fields bang \' de tranh break su -c shell
    ip   = proxy["ip"].replace("'", "\\'")
    port = str(proxy["port"])
    user = proxy["user"].replace("'", "\\'")
    pwd  = proxy["pass"].replace("'", "\\'")

    write_cmd = (
        f"printf '%s' "
        f"'<?xml version=\\'1.0\\' encoding=\\'utf-8\\' standalone=\\'yes\\' ?>"
        f"<map>"
        f"<string name=\\'proxy_server\\'>{ip}</string>"
        f"<string name=\\'proxy_port\\'>{port}</string>"
        f"<string name=\\'proxy_username\\'>{user}</string>"
        f"<string name=\\'proxy_password\\'>{pwd}</string>"
        f"<boolean name=\\'ipv6\\' value=\\'false\\' />"
        f"<boolean name=\\'udp_forward\\' value=\\'false\\' />"
        f"<boolean name=\\'per_app\\' value=\\'false\\' />"
        f"</map>' > {SOCKSDROID_PREFS_FILE}"
    )
    ok_write, out_write = _adb_su(ld_console, index, write_cmd)
    if not ok_write:
        logger.error(_err(f"[VM {index:02d}] Ghi SharedPrefs that bai: {out_write}"))
        _print_root_check_warning(index)
        return False

    # 4. Set quyen file phu hop voi package (660) va owner = package uid
    _adb_su(ld_console, index, f"chmod 660 {SOCKSDROID_PREFS_FILE}")
    # Lay uid cua package va doi owner
    _adb_su(ld_console, index,
            f"chown $(stat -c '%U:%G' {SOCKSDROID_PREFS_DIR}) {SOCKSDROID_PREFS_FILE}")

    # 5. Khoi dong SocksDroid voi root (khong co VPN dialog)
    start_cmd = f"am start -n {SOCKSDROID_ACTIVITY} --ez intent_start true"
    ok_start, out_start = _adb_su(ld_console, index, start_cmd)
    if not ok_start:
        logger.warning(_warn(f"[VM {index:02d}] Am start that bai: {out_start}"))
    else:
        logger.info(_ok(f"[VM {index:02d}] Cau hinh proxy va khoi dong SocksDroid thanh cong."))

    return ok_write  # Thanh cong neu it nhat ghi duoc SharedPrefs


def verify_proxy(ld_console: str, index: int, proxy: dict) -> bool:
    """
    Kiem tra xem proxy da thuc su duoc nap vao SocksDroid chua.

    Phuong phap: Doc lai file SharedPreferences bang root va kiem tra
    xem IP va Port trong file co khop voi proxy du kien khong.

    Returns:
        True  -- SharedPrefs ton tai va chua dung IP:Port mong muon
        False -- File khong ton tai, rong, hoac chua sai gia tri
    """
    logger.info(f"[VM {index:02d}] Dang xac minh proxy da nap ({proxy['ip']}:{proxy['port']}) ...")

    ok, output = _adb_su(ld_console, index, f"cat {SOCKSDROID_PREFS_FILE} 2>/dev/null")

    if not ok or not output.strip():
        logger.error(_err(
            f"[VM {index:02d}] VERIFY FAIL: Khong doc duoc SharedPrefs. "
            f"Root chua hoat dong hoac file chua duoc ghi."
        ))
        return False

    # Kiem tra IP va Port trong noi dung XML
    ip_ok   = proxy["ip"]   in output
    port_ok = str(proxy["port"]) in output

    if ip_ok and port_ok:
        logger.info(_ok(f"[VM {index:02d}] VERIFY OK: {proxy['ip']}:{proxy['port']} da co mat trong SharedPrefs."))
        return True
    else:
        logger.error(_err(
            f"[VM {index:02d}] VERIFY FAIL: IP_found={ip_ok}, Port_found={port_ok}. "
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
        ok = install_app(ld_console, i, apk_path)
        if ok:
            success += 1
        else:
            failed += 1
        if i < count:
            time.sleep(INSTALL_DELAY_SEC)

    logger.info(f"INSTALL XONG: {success} thanh cong / {failed} that bai / {count} tong")


def configure_all(cfg: dict, proxies: list) -> None:
    """Cau hinh proxy cho tat ca VM, ghep proxy[i-1] voi VM index i."""
    ld_console = cfg["_LD_CONSOLE"]
    count      = cfg["INSTANCE_COUNT"]

    if len(proxies) < count:
        logger.error(_err(f"Can {count} proxy, hien co {len(proxies)}. Kiem tra {PROXIES_FILE}."))
        sys.exit(1)

    print(_info("=" * 64))
    print(_info(f"  BAT DAU: Cau hinh proxy (Root ADB -> SharedPrefs) cho {count} VM ..."))
    print(_info("=" * 64))

    success, failed = 0, 0
    for i in range(1, count + 1):
        proxy = proxies[i - 1]
        ok    = configure_proxy(ld_console, i, proxy)
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
    ld_console = cfg["_LD_CONSOLE"]
    count      = cfg["INSTANCE_COUNT"]

    if len(proxies) < count:
        logger.error(_err(f"Can {count} proxy de verify."))
        sys.exit(1)

    print(_info("=" * 64))
    print(_info(f"  VERIFY: Kiem tra SharedPrefs tren {count} VM ..."))
    print(_info("=" * 64))

    results = {}
    for i in range(1, count + 1):
        ok = verify_proxy(ld_console, i, proxies[i - 1])
        results[f"TikTok_US_{i:02d}"] = "OK" if ok else "FAIL"

    ok_count   = sum(1 for v in results.values() if v == "OK")
    fail_count = count - ok_count
    summary    = f"VERIFY XONG: {ok_count} OK / {fail_count} FAIL / {count} tong"
    logger.info(_ok(summary) if fail_count == 0 else _warn(summary))

    # In bang ket qua
    print()
    print(f"  {'VM':<20} {'Proxy':<30} {'Status'}")
    print("  " + "-" * 60)
    for i in range(1, count + 1):
        name   = f"TikTok_US_{i:02d}"
        proxy  = proxies[i - 1]
        status = results[name]
        color  = _ok(status) if status == "OK" else _err(status)
        print(f"  {name:<20} {proxy['ip']}:{proxy['port']:<21} {color}")
    print()
    return results


def setup_all(cfg: dict, proxies: list) -> None:
    """Full pipeline: Tai APK (neu can) -> Cai -> Cau hinh -> Verify cho 10 VM."""
    # Buoc 0: Dam bao APK da san sang
    apk_path = cfg["SOCKSDROID_APK_PATH"]
    if not download_apk(apk_path):
        logger.error(_err("Khong the tai APK. Kiem tra ket noi mang hoac tai thu cong."))
        sys.exit(1)

    install_all(cfg, proxies)
    configure_all(cfg, proxies)

    # Cho SocksDroid khoi dong hoan toan truoc khi verify
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
        print(f"  {Fore.CYAN}setup{Style.RESET_ALL}      -- Tai APK + Cai + Cau hinh (Root ADB) + Verify")
        print(f"  {Fore.CYAN}download{Style.RESET_ALL}   -- Chi tai APK SocksDroid tu GitHub")
        print(f"  {Fore.CYAN}install{Style.RESET_ALL}    -- Chi cai APK vao tat ca VM dang chay")
        print(f"  {Fore.CYAN}configure{Style.RESET_ALL}  -- Chi ghi SharedPrefs proxy qua Root ADB")
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
