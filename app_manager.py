# -*- coding: utf-8 -*-
"""
app_manager.py -- Kiem tra va cai dat APK len nhieu LDPlayer VM song song.

Tinh nang:
  - Lay danh sach VM dang chay tu ldconsole runninglist
  - Kiem tra APK da duoc cai chua bang: adb shell pm list packages
  - Cai dat APK bang: adb install -r <apk_path>
  - Song song hoa bang ThreadPoolExecutor de tiet kiem thoi gian

CLI:
  python app_manager.py check   <package_name>
  python app_manager.py install <apk_path> <package_name>
  python app_manager.py auto    <apk_path> <package_name>   # check -> install neu can
"""

import subprocess
import sys
import os
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# Bootstrap colorama
# ============================================================
try:
    import colorama
    from colorama import Fore, Style
    colorama.init(autoreset=True)
    _C = True
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "colorama"], check=False)
    import colorama
    from colorama import Fore, Style
    colorama.init(autoreset=True)
    _C = True


# ============================================================
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/app_manager.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("APP_MGR")

def _ok(s):   return Fore.GREEN  + str(s) + Style.RESET_ALL
def _err(s):  return Fore.RED    + str(s) + Style.RESET_ALL
def _warn(s): return Fore.YELLOW + str(s) + Style.RESET_ALL
def _info(s): return Fore.CYAN   + str(s) + Style.RESET_ALL


# ============================================================
# Config
# ============================================================
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
ADB_BASE_PORT = 5555

def load_config() -> dict:
    if not os.path.isfile(CONFIG_FILE):
        logger.error(_err(f"Khong tim thay {CONFIG_FILE}"))
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    ld_path = cfg.get("LDPLAYER_PATH", "C:\\LDPlayer\\LDPlayer9")
    ld_console = os.path.join(ld_path, "ldconsole.exe")
    adb_exe    = os.path.join(ld_path, "adb.exe")

    if not os.path.isfile(ld_console):
        logger.error(_err(f"Khong tim thay ldconsole.exe: {ld_console}"))
        sys.exit(1)
    if not os.path.isfile(adb_exe):
        logger.error(_err(f"Khong tim thay adb.exe: {adb_exe}"))
        sys.exit(1)

    cfg["_LD_CONSOLE"] = ld_console
    cfg["_ADB_EXE"]    = adb_exe
    return cfg


# ============================================================
# ADB Helpers
# ============================================================

def _adb_port(ldplayer_index: int) -> int:
    """Port = 5555 + (ldplayer_index * 2)"""
    return ADB_BASE_PORT + (ldplayer_index * 2)


def _adb_connect(adb_exe: str, port: int) -> bool:
    """Ket noi ADB. Returns True neu thanh cong."""
    try:
        result = subprocess.run(
            [adb_exe, "connect", f"127.0.0.1:{port}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        out = (result.stdout + result.stderr).strip()
        return "connected" in out.lower() or "already" in out.lower()
    except Exception:
        return False


def _adb_shell(adb_exe: str, port: int, cmd: str, timeout: int = 60) -> tuple:
    """Chay lenh shell. Returns (success: bool, output: str)."""
    serial = f"127.0.0.1:{port}"
    try:
        result = subprocess.run(
            [adb_exe, "-s", serial, "shell", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


def _adb_install(adb_exe: str, port: int, apk_path: str, timeout: int = 300) -> tuple:
    """
    Cai APK qua direct ADB: adb -s 127.0.0.1:<port> install -r <apk>.
    Returns (success: bool, output: str).
    """
    serial  = f"127.0.0.1:{port}"
    abs_apk = os.path.abspath(apk_path)
    try:
        result = subprocess.run(
            [adb_exe, "-s", serial, "install", "-r", abs_apk],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        success = "success" in output.lower()
        return success, output
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT (>{timeout}s)"
    except Exception as e:
        return False, str(e)


# ============================================================
# LDConsole: get running instances
# ============================================================

def _ld_cmd(ld_console: str, *args) -> tuple:
    try:
        result = subprocess.run(
            [ld_console] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except Exception as e:
        return False, str(e)


def get_running_instances(ld_console: str) -> list:
    """
    Lay danh sach VM dang chay tu ldconsole runninglist.
    Tra ve list [{index: int, name: str}] sap xep theo index.
    """
    def _parse(output: str) -> list:
        instances = []
        for line in output.strip().splitlines():
            parts = line.split(",")
            if len(parts) >= 2:
                try:
                    idx  = int(parts[0].strip())
                    name = parts[1].strip()
                    if name:
                        instances.append({"index": idx, "name": name})
                except ValueError:
                    continue
        return sorted(instances, key=lambda x: x["index"])

    ok, out = _ld_cmd(ld_console, "runninglist")
    if ok and out.strip():
        instances = _parse(out)
        if instances:
            return instances

    # Fallback: list2 + isrunning
    ok2, out2 = _ld_cmd(ld_console, "list2")
    if not ok2 or not out2.strip():
        return []
    all_vms = _parse(out2)
    running = []
    for vm in all_vms:
        ok_r, r_out = _ld_cmd(ld_console, "isrunning", "--index", str(vm["index"]))
        if ok_r and "running" in r_out.lower():
            running.append(vm)
    return running


# ============================================================
# Core Functions
# ============================================================

def check_package(adb_exe: str, port: int, vm_name: str, package_name: str) -> bool:
    """
    Kiem tra xem package da duoc cai tren VM chua.
    Dung: pm list packages | grep <package_name>
    Returns True neu tim thay package.
    """
    _, output = _adb_shell(adb_exe, port, f"pm list packages {package_name}")
    found = package_name in output
    if found:
        logger.info(_ok(f"[{vm_name}] Da cai: {package_name}"))
    else:
        logger.info(_info(f"[{vm_name}] Chua cai: {package_name}"))
    return found


def install_apk(adb_exe: str, port: int, vm_name: str, apk_path: str, package_name: str) -> bool:
    """
    Cai APK len VM qua direct ADB.
    Returns True neu cai thanh cong.
    """
    if not os.path.isfile(apk_path):
        logger.error(_err(f"[{vm_name}] APK khong ton tai: {apk_path}"))
        return False

    if not _adb_connect(adb_exe, port):
        logger.error(_err(f"[{vm_name}] Khong the ket noi ADB port {port}"))
        return False

    logger.info(f"[{vm_name}] Dang cai {os.path.basename(apk_path)} ...")
    ok, output = _adb_install(adb_exe, port, apk_path)

    if ok:
        logger.info(_ok(f"[{vm_name}] Cai APK thanh cong: {package_name}"))
    else:
        logger.error(_err(f"[{vm_name}] Cai APK THAT BAI: {output[:200]}"))
    return ok


def _worker_check_install(adb_exe: str, vm: dict, apk_path: str,
                           package_name: str, force_install: bool) -> dict:
    """
    Worker chay trong thread: check -> install neu can.
    Returns dict ket qua: {name, index, port, was_installed, success}.
    """
    port    = _adb_port(vm["index"])
    name    = vm["name"]
    result  = {"name": name, "index": vm["index"], "port": port,
               "was_installed": False, "success": True}

    if not _adb_connect(adb_exe, port):
        logger.error(_err(f"[{name}] Khong the ket noi ADB port {port}"))
        result["success"] = False
        return result

    already = check_package(adb_exe, port, name, package_name)

    if already and not force_install:
        result["was_installed"] = True
        return result   # Skip, da co app

    ok = install_apk(adb_exe, port, name, apk_path, package_name)
    result["success"] = ok
    return result


def check_and_install_app(
    apk_path: str,
    package_name: str,
    cfg: dict,
    force_install: bool = False,
    max_workers: int = 4,
) -> dict:
    """
    Pipeline chinh: Quet VM -> Check -> Install neu chua co.
    Song song hoa bang ThreadPoolExecutor.

    Args:
        apk_path:      Duong dan den file APK.
        package_name:  Package name Android (vi du: com.zhiliaoapp.musically).
        cfg:           Dict config da load tu config.json.
        force_install: Neu True, cai de len ca khi da co.
        max_workers:   So luong thread song song.

    Returns dict {vm_name: "OK" | "SKIP" | "FAIL"}.
    """
    ld_console = cfg["_LD_CONSOLE"]
    adb_exe    = cfg["_ADB_EXE"]

    # Lay danh sach VM dang chay
    running = get_running_instances(ld_console)
    if not running:
        logger.error(_err("Khong co VM nao dang chay. Khoi dong VM truoc."))
        return {}

    print(_info("=" * 64))
    print(_info(f"  CHECK & INSTALL: {package_name}"))
    print(_info(f"  APK  : {os.path.abspath(apk_path)}"))
    print(_info(f"  VMs  : {len(running)} dang chay | Workers: {max_workers}"))
    print(_info("=" * 64))

    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _worker_check_install, adb_exe, vm, apk_path, package_name, force_install
            ): vm
            for vm in running
        }

        for future in as_completed(futures):
            vm = futures[future]
            try:
                r = future.result()
                if r["was_installed"] and not force_install:
                    results[r["name"]] = "SKIP"
                elif r["success"]:
                    results[r["name"]] = "OK"
                else:
                    results[r["name"]] = "FAIL"
            except Exception as exc:
                logger.error(_err(f"[{vm['name']}] Exception trong thread: {exc}"))
                results[vm["name"]] = "FAIL"

    # In bang tong ket
    ok_count   = sum(1 for v in results.values() if v == "OK")
    skip_count = sum(1 for v in results.values() if v == "SKIP")
    fail_count = sum(1 for v in results.values() if v == "FAIL")

    print()
    print(f"  {'VM':<20} {'Idx':<5} {'Port':<7} {'Status'}")
    print("  " + "-" * 50)
    for vm in sorted(running, key=lambda x: x["index"]):
        port   = _adb_port(vm["index"])
        status = results.get(vm["name"], "?")
        color  = _ok(status) if status in ("OK", "SKIP") else _err(status)
        print(f"  {vm['name']:<20} {vm['index']:<5} {port:<7} {color}")
    print()

    summary = f"TONG KET: {ok_count} cai moi / {skip_count} bo qua / {fail_count} loi / {len(running)} tong"
    logger.info(_ok(summary) if fail_count == 0 else _warn(summary))
    return results


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    usage = (
        f"\n{_info('Cach dung:')}\n"
        f"  python {os.path.basename(__file__)} check   <package_name>\n"
        f"  python {os.path.basename(__file__)} install <apk_path> <package_name>\n"
        f"  python {os.path.basename(__file__)} auto    <apk_path> <package_name>\n"
        f"  python {os.path.basename(__file__)} force   <apk_path> <package_name>\n\n"
        f"  Vi du (TikTok):\n"
        f"    python {os.path.basename(__file__)} auto tiktok.apk com.zhiliaoapp.musically\n"
    )

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    cfg = load_config()

    if cmd == "check":
        if len(sys.argv) < 3:
            print(usage)
            sys.exit(1)
        package_name = sys.argv[2]
        running      = get_running_instances(cfg["_LD_CONSOLE"])
        for vm in running:
            port = _adb_port(vm["index"])
            _adb_connect(cfg["_ADB_EXE"], port)
            check_package(cfg["_ADB_EXE"], port, vm["name"], package_name)

    elif cmd in ("install", "auto", "force"):
        if len(sys.argv) < 4:
            print(usage)
            sys.exit(1)
        apk_path     = sys.argv[2]
        package_name = sys.argv[3]
        force        = (cmd == "force")

        if not os.path.isfile(apk_path):
            logger.error(_err(f"Khong tim thay file APK: {apk_path}"))
            sys.exit(1)

        check_and_install_app(apk_path, package_name, cfg, force_install=force)

    else:
        print(usage)
        sys.exit(1)


if __name__ == "__main__":
    main()
