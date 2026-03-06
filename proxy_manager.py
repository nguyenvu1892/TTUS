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
        "requests":     "requests>=2.28.0",
        "colorama":     "colorama>=0.4.6",
        "uiautomator2": "uiautomator2>=3.0.0",
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
import base64
import json
import logging
import os
import time
import xml.etree.ElementTree as ET

import colorama
import requests
import uiautomator2 as u2
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


def _adb_port(ldplayer_index: int) -> int:
    """
    Tinh port ADB tu LDPlayer index CHINH XAC (0-based) lay tu ldconsole list2.
    Port = 5555 + (ldplayer_index * 2)

    Vi du:
      index=1 -> 5557   (TikTok_US_01 neu VM goc la index 0 va bi tat)
      index=9 -> 5573
     index=10 -> 5575
    """
    return ADB_BASE_PORT + (ldplayer_index * 2)


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
    Chay lenh shell voi quyen root (su) tren may ao qua ADB truc tiep.

    Cach truyen dung:
      adb -s 127.0.0.1:<port> shell "su -c '<shell_cmd>'"

    Luu y: Phai gop "su -c 'cmd'" thanh MOT string duy nhat cho adb shell,
    khong duoc tach thanh nhieu args rieng le (su, -c, cmd).

    Returns: (success: bool, output: str)
    """
    serial   = f"127.0.0.1:{port}"
    # Boc shell_cmd vao dau phay don, gop su + -c + cmd thanh 1 string
    su_cmd   = f"su -c '{shell_cmd}'"
    full_cmd = [adb_exe, "-s", serial, "shell", su_cmd]
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
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


def get_running_instances(ld_console: str) -> list:
    """
    Lay danh sach CHINH XAC cac VM dang chay bang ldconsole runninglist.
    Tra ve list cac dict {"index": int, "name": str} sap xep tang dan theo index.

    Format output cua ldconsole runninglist:
      index,name,top_window_handle,bind_window_handle,pid,...

    Neu runninglist khong kha dung, fallback ve list2 va loc nhung VM dang running.
    """
    def _parse_list(output: str) -> list:
        instances = []
        for line in output.strip().splitlines():
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    idx  = int(parts[0].strip())
                    name = parts[1].strip()
                    if name:  # bo qua dong header hoac rong
                        instances.append({"index": idx, "name": name})
                except ValueError:
                    continue
        return sorted(instances, key=lambda x: x["index"])

    # Thu runninglist truoc (chi tra ve VM dang chay)
    ok, output = _ld_command(ld_console, "runninglist")
    if ok and output.strip():
        instances = _parse_list(output)
        if instances:
            logger.info(_info(f"Runninglist: {len(instances)} VM dang chay: "
                              + ", ".join(f"{v['name']}(idx={v['index']})" for v in instances)))
            return instances

    # Fallback: list2 + isrunning check
    logger.warning(_warn("runninglist khong kha dung, dung list2 + isrunning check."))
    ok2, output2 = _ld_command(ld_console, "list2")
    if not ok2 or not output2.strip():
        logger.error(_err("Khong the lay danh sach VM tu ldconsole."))
        return []

    all_instances = _parse_list(output2)
    running = [v for v in all_instances if _is_running(ld_console, v["index"])]
    logger.info(_info(f"list2 + filter: {len(running)} VM dang chay."))
    return running


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


# ============================================================
# UI Automator Helpers (SocksDroid dung PreferenceActivity)
# ============================================================

def _u2_connect(serial: str, label: str):
    """
    Ket noi uiautomator2.
    Tu dong cai atx-agent vao device qua ADB (khong can internet tren VM).
    """
    try:
        device = u2.connect(serial)
        device.info   # ping kiem tra ket noi
        return device
    except Exception as exc:
        raise RuntimeError(f"{label} u2.connect({serial}) that bai: {exc}")


def _u2_find_switch(device):
    """Tim VPN toggle Switch / ToggleButton chinh cua SocksDroid."""
    for cls in ("android.widget.Switch", "android.widget.ToggleButton"):
        el = device(className=cls)
        if el.exists:
            return el
    return None


def _u2_fill_pref_dialog(device, row_text: str, value: str, label: str) -> bool:
    """
    Quy trinh dien mot truong Preference trong SocksDroid:
      1. Tim hang Preference trong danh sach (vi du: 'Server IP')
      2. Click de mo Popup Dialog
      3. Tim duy nhat 1 EditText trong Dialog -> clear -> type -> click OK

    SocksDroid dung PreferenceActivity / PreferenceFragment:
    - Man hinh chinh la ListView chua cac hang Preference
    - Khi click mot hang, Android hien PopupDialog voi EditText de nhap
    - KHONG co EditText truc tiep tren man hinh chinh

    Returns:
        True neu dien thanh cong.
        False neu bat ky buoc nao that bai (pref row khong tim thay, dialog khong
        xuat hien, khong click duoc OK).
    """
    DIALOG_TIMEOUT = 4   # giay cho dialog xuat hien

    # -- Buoc 1: Tim hang Preference theo text label --
    pref_row = device(text=row_text)
    if not pref_row.exists:
        logger.error(_err(f"{label} FAIL: Khong tim thay hang Preference '{row_text}'. "
                          "Kiem tra lai ten hien thi tren giao dien SocksDroid."))
        return False

    pref_row.click()
    time.sleep(0.6)

    # -- Buoc 2: Cho Popup Dialog xuat hien, phai co EditText ben trong --
    dialog_edit = device(className="android.widget.EditText", focused=True)
    if not dialog_edit.wait(timeout=DIALOG_TIMEOUT):
        # Thu fallback: EditText bat ky (co the khong focused ngay)
        dialog_edit = device(className="android.widget.EditText")
        if not dialog_edit.exists:
            logger.error(_err(f"{label} FAIL: Dialog EditText khong xuat hien sau khi click '{row_text}'. "
                              "App co the to ra loi hoac giao dien da thay doi."))
            return False

    # -- Buoc 3: Xoa va go gia tri moi --
    dialog_edit.clear_text()
    time.sleep(0.2)
    dialog_edit.set_text(str(value))
    time.sleep(0.3)

    # -- Buoc 4: Click nut OK trong dialog --
    ok_btn = device(text="OK")
    if not ok_btn.exists:
        # Mot so phien ban SocksDroid dung "Set" hoac "Apply"
        for btn_text in ("Set", "Apply", "Luu", "Done"):
            ok_btn = device(text=btn_text)
            if ok_btn.exists:
                break

    if not ok_btn.exists:
        logger.error(_err(f"{label} FAIL: Khong tim thay nut OK/Set trong dialog '{row_text}'."))
        device.press("back")   # Dong dialog tranh ket man hinh
        return False

    ok_btn.click()
    time.sleep(0.4)

    display_val = "****" if "pass" in row_text.lower() else value
    logger.info(_info(f"{label} [{row_text}] = {display_val}"))
    return True


# ============================================================
# Configure via UI Automator  (SocksDroid PreferenceActivity)
# ============================================================

def configure_proxy(adb_exe: str, ldplayer_index: int, vm_name: str, proxy: dict) -> bool:
    """
    Cau hinh SOCKS5 proxy bang UI Automator -- 100% UI interaction.

    SocksDroid dung PreferenceActivity:
      - Man hinh chinh la danh sach cac hang Preference (ListView)
      - Moi hang co text label: 'Server IP', 'Server Port', 'Username', 'Password'
      - Click mot hang -> mo Dialog voi 1 EditText -> nhap -> OK

    Quy trinh:
      1. Stop SocksDroid (giat sach RAM cache)
      2. Start SocksDroid tuoi
      3. Tat VPN switch neu dang ON
      4. Dien 4 truong theo trinh tu: Server IP, Server Port, Username, Password
         LUI: neu bat ky truong nao fail -> ERROR do + return False NGAY LAP TUC
              TUYET DOI khong gat VPN neu config chua hoan tat.
      5. Kiem tra ket qua: neu ca 4 truong OK -> bat VPN switch
    """
    port   = _adb_port(ldplayer_index)
    serial = f"127.0.0.1:{port}"
    label  = f"[{vm_name}] [idx={ldplayer_index}] [{serial}]"

    logger.info(f"{label} Cau hinh proxy qua UI Automator: {proxy['ip']}:{proxy['port']}...")

    if not _adb_connect(adb_exe, port):
        logger.error(_err(f"{label} Khong the ket noi ADB."))
        return False

    try:
        device = _u2_connect(serial, label)
    except RuntimeError as exc:
        logger.error(_err(str(exc)))
        return False

    try:
        # 1. Stop SocksDroid de giat sach RAM
        logger.info(f"{label} [1/5] Stop SocksDroid...")
        device.app_stop(SOCKSDROID_PACKAGE)
        time.sleep(1.0)

        # 2. Mo SocksDroid tuoi
        logger.info(f"{label} [2/5] Mo SocksDroid...")
        device.app_start(SOCKSDROID_PACKAGE, ".MainActivity", wait=True)
        time.sleep(2.5)

        # 3. Tat VPN switch neu dang ON
        logger.info(f"{label} [3/5] Kiem tra VPN switch...")
        vpn_switch = _u2_find_switch(device)
        if vpn_switch and vpn_switch.info.get("checked", False):
            logger.info(f"{label} VPN dang ON - tat truoc khi doi IP...")
            vpn_switch.click()
            time.sleep(1.5)
            # Xu ly dialog "Disconnect VPN?" neu co
            for confirm_text in ("OK", "Yes", "Disconnect"):
                confirm = device(text=confirm_text)
                if confirm.exists:
                    confirm.click()
                    time.sleep(0.5)
                    break

        # 4a. PHASE 1: Dien Server IP va Server Port (luon hien thi, khong can scroll)
        logger.info(f"{label} [4/5] Phase 1 - Dien Server IP, Server Port...")
        for row_text, value in [("Server IP", proxy["ip"]), ("Server Port", str(proxy["port"]))]:
            if not _u2_fill_pref_dialog(device, row_text, value, label):
                logger.error(_err(
                    f"{label} === FAIL tai '{row_text}'. VM se KHONG bat VPN. ==="
                ))
                return False

        # 4b. SCROLL XUONG de tim phan Authentication (Username/Password nam nua duoi)
        logger.info(f"{label} [4/5] Scroll xuong phan Username & Password Authentication...")
        AUTH_SECTION = "Username & Password Authentication"
        scrollable = device(scrollable=True)
        if scrollable.exists:
            # Thu scroll den thi phan Authentication truoc
            try:
                scrollable.scroll.to(text=AUTH_SECTION)
                time.sleep(0.5)
            except Exception:
                # Fallback: swipe len (scroll xuong man hinh)
                device.swipe(360, 900, 360, 300, duration=0.4)
                time.sleep(0.5)
        else:
            # Khong co scrollable view -> swipe thu cong
            device.swipe(360, 900, 360, 300, duration=0.4)
            time.sleep(0.5)

        # 4c. Kich hoat "Username & Password Authentication" neu chua tick
        #    CheckBoxPreference: ban than hang preference la checkable element.
        #    Kiem tra .info['checked'] tren chinh hang do, KHONG tim checkbox con bat ky.
        auth_row = device(text=AUTH_SECTION)
        if auth_row.exists:
            auth_info = auth_row.info
            is_checked = auth_info.get("checked", None)

            if is_checked is False:
                # Chua duoc tick -> click DE BAT
                logger.info(f"{label} [AUTH] 'Username & Password Authentication' dang OFF. Bat len...")
                auth_row.click()
                time.sleep(0.6)   # Cho UI mo khoa 2 o Username/Password ben duoi
                # Xac nhan lai da bat chua
                new_checked = device(text=AUTH_SECTION).info.get("checked", None)
                if new_checked is False:
                    logger.error(_err(f"{label} [AUTH] Van khong the bat Authentication. "
                                      "VM se KHONG duoc bat VPN."))
                    return False
                logger.info(_ok(f"{label} [AUTH] Da bat thanh cong -- Username/Password fields da mo."))

            elif is_checked is True:
                logger.info(f"{label} [AUTH] 'Username & Password Authentication' da ON, bo qua.")

            else:
                # 'checked' key khong co trong info (co the la Switch hoac view khac)
                # Fallback: tim con truc tiep la CheckBox trong cung container
                auth_cb = auth_row.child(className="android.widget.CheckBox")
                if auth_cb.exists:
                    if not auth_cb.info.get("checked", True):
                        logger.info(f"{label} [AUTH] Tim thay CheckBox con dang OFF. Bat len...")
                        auth_cb.click()
                        time.sleep(0.6)
                else:
                    # Khong xac dinh duoc trang thai -- click row de dam bao bat
                    logger.warning(_warn(f"{label} [AUTH] Khong xac dinh duoc 'checked' state. "
                                         "Click de dam bao bat..."))
                    auth_row.click()
                    time.sleep(0.6)
        else:
            logger.warning(_warn(f"{label} Khong tim thay muc '{AUTH_SECTION}' tren man hinh. "
                                  "Thu dien Username/Password truc tiep..."))


        # 4d. PHASE 2: Dien Username va Password (gio da scroll xuong va auth da bat)
        logger.info(f"{label} [4/5] Phase 2 - Dien Username, Password...")
        for row_text, value in [("Username", proxy["user"]), ("Password", proxy["pass"])]:
            if not _u2_fill_pref_dialog(device, row_text, value, label):
                logger.error(_err(
                    f"{label} === FAIL tai '{row_text}'. VM se KHONG bat VPN. ==="
                ))
                return False


        # 5. Ca 4 truong OK -> bat VPN switch an toan
        logger.info(f"{label} [5/5] Bat VPN switch voi proxy moi...")
        vpn_switch = _u2_find_switch(device)
        if not vpn_switch:
            logger.error(_err(f"{label} Khong tim thay VPN switch sau khi dien xong. "
                              "Khong the bat VPN."))
            return False

        vpn_switch.click()
        time.sleep(1.5)

        # Xu ly dialog VPN permission (lan dau bam OK)
        for confirm_text in ("OK", "Connect", "Yes"):
            confirm = device(text=confirm_text)
            if confirm.exists:
                logger.info(f"{label} Xu ly dialog VPN permission -- click '{confirm_text}'...")
                confirm.click()
                time.sleep(2.0)
                break

        logger.info(_ok(f"{label} === HOAN THANH: VPN da bat voi {proxy['ip']}:{proxy['port']} ==="))
        return True

    except Exception as exc:
        logger.error(_err(f"{label} Loi ngoai du kien trong UI Automator: {exc}"))
        return False



def verify_proxy(adb_exe: str, ldplayer_index: int, vm_name: str, proxy: dict) -> bool:
    """
    Xac minh proxy da duoc nap vao SocksDroid bang cach doc lai SharedPrefs.

    Args:
        ldplayer_index: Index CHINH XAC tu ldconsole (de tinh port).
        vm_name:        Ten may ao (de log).
    Returns True neu IP + Port co mat trong file.
    """
    port   = _adb_port(ldplayer_index)
    serial = f"127.0.0.1:{port}"
    label  = f"[{vm_name}] [idx={ldplayer_index}] [{serial}]"

    logger.info(f"{label} Dang xac minh proxy ({proxy['ip']}:{proxy['port']}) ...")

    if not _adb_connect(adb_exe, port):
        logger.error(_err(f"{label} VERIFY FAIL: Khong the ket noi ADB."))
        return False

    ok, output = _adb_shell_su(adb_exe, port,
                                f"cat {SOCKSDROID_PREFS_FILE} 2>/dev/null")
    if not ok or not output.strip():
        logger.error(_err(f"{label} VERIFY FAIL: Khong doc duoc SharedPrefs."))
        return False

    ip_ok   = proxy["ip"]   in output
    port_ok = str(proxy["port"]) in output

    if ip_ok and port_ok:
        logger.info(_ok(f"{label} VERIFY OK: {proxy['ip']}:{proxy['port']} da co trong SharedPrefs."))
        return True
    else:
        logger.error(_err(
            f"{label} VERIFY FAIL: IP_found={ip_ok}, Port_found={port_ok}.\n"
            f"SharedPrefs: {output[:200]}"
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
    """
    Cau hinh SOCKS5 proxy cho tat ca VM DANG CHAY bang Direct ADB.
    Lay danh sach VM thuc te tu ldconsole runninglist (khong hardcode index).
    """
    ld_console = cfg["_LD_CONSOLE"]
    adb_exe    = cfg["_ADB_EXE"]

    # Lay danh sach VM dang chay voi index CHINH XAC
    running = get_running_instances(ld_console)
    if not running:
        logger.error(_err("Khong co VM nao dang chay. Hay khoi dong 10 VM truoc."))
        sys.exit(1)

    if len(proxies) < len(running):
        logger.error(_err(f"Can {len(running)} proxy, hien co {len(proxies)}. Kiem tra {PROXIES_FILE}."))
        sys.exit(1)

    print(_info("=" * 64))
    print(_info(f"  BAT DAU: Cau hinh proxy cho {len(running)} VM dang chay ..."))
    for vm in running:
        port = _adb_port(vm["index"])
        print(_info(f"    {vm['name']:<20} idx={vm['index']} port={port}"))
    print(_info("=" * 64))

    success, failed = 0, 0
    for seq, vm in enumerate(running):
        proxy = proxies[seq]  # ghep tuan tu: VM thu seq -> proxy thu seq
        ok    = configure_proxy(adb_exe, vm["index"], vm["name"], proxy)
        if ok:
            success += 1
        else:
            failed += 1
        if seq < len(running) - 1:
            time.sleep(CONFIGURE_DELAY_SEC)

    summary = f"CONFIGURE XONG: {success} thanh cong / {failed} that bai / {len(running)} tong"
    logger.info(_ok(summary) if failed == 0 else _warn(summary))


def verify_all(cfg: dict, proxies: list) -> dict:
    """
    Xac minh proxy tren tat ca VM dang chay. Tra ve dict {vm_name: OK/FAIL}.
    Lay danh sach VM thuc te tu ldconsole runninglist.
    """
    ld_console = cfg["_LD_CONSOLE"]
    adb_exe    = cfg["_ADB_EXE"]

    running = get_running_instances(ld_console)
    if not running:
        logger.error(_err("Khong co VM nao dang chay."))
        return {}

    if len(proxies) < len(running):
        logger.error(_err(f"Can {len(running)} proxy de verify."))
        sys.exit(1)

    print(_info("=" * 64))
    print(_info(f"  VERIFY: Kiem tra SharedPrefs tren {len(running)} VM ..."))
    print(_info("=" * 64))

    results = {}
    for seq, vm in enumerate(running):
        ok = verify_proxy(adb_exe, vm["index"], vm["name"], proxies[seq])
        results[vm["name"]] = "OK" if ok else "FAIL"

    ok_count   = sum(1 for v in results.values() if v == "OK")
    fail_count = len(running) - ok_count
    logger.info(_ok(f"VERIFY XONG: {ok_count} OK / {fail_count} FAIL / {len(running)} tong")
                if fail_count == 0 else
                _warn(f"VERIFY XONG: {ok_count} OK / {fail_count} FAIL / {len(running)} tong"))

    # In bang ket qua
    print()
    print(f"  {'VM':<20} {'Idx':<5} {'Port':<7} {'Proxy':<35} {'Status'}")
    print("  " + "-" * 78)
    for seq, vm in enumerate(running):
        port   = _adb_port(vm["index"])
        proxy  = proxies[seq]
        status = results.get(vm["name"], "?")
        color  = _ok(status) if status == "OK" else _err(status)
        print(f"  {vm['name']:<20} {vm['index']:<5} {port:<7} "
              f"{proxy['ip']}:{proxy['port']:<26} {color}")
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
