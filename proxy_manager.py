"""
proxy_manager.py — Task 2: Tự động cài đặt và cấu hình SocksDroid trên 10 VM LDPlayer.

Luồng:
    0. _bootstrap_deps()    — Tự kiểm tra và pip install thư viện cần thiết
    1. load_config()        — Đọc config.json, validate LDPLAYER_PATH + SOCKSDROID_APK_PATH
    2. load_proxies()       — Parse data/proxies_list.txt → list[dict]
    3. download_apk()       — Tự tải SocksDroid APK từ GitHub nếu chưa có
    4. install_app(i)       — ldconsole installapp → cài APK vào VM index i
    5. configure_proxy(i)   — ldconsole adb → am start Intent → cấu hình + bật proxy
    6. setup_all()          — Full pipeline cho 10 VM

CLI:
    python proxy_manager.py setup       # Tải APK (nếu cần) + Cài + Cấu hình
    python proxy_manager.py download    # Chỉ tải APK
    python proxy_manager.py install     # Chỉ cài APK (yêu cầu APK đã có)
    python proxy_manager.py configure   # Chỉ cấu hình proxy (APK đã được cài)
"""

# ============================================================
# STEP 0 — AUTO-BOOTSTRAP DEPENDENCIES
# Phải chạy trước MỌI import của thư viện bên ngoài.
# ============================================================
import subprocess
import sys


def _bootstrap_deps():
    """
    Kiểm tra và tự động cài đặt các thư viện cần thiết qua pip.
    Chỉ cài nếu chưa có — không ghi đè version đã tồn tại.
    """
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
        print(f"[BOOTSTRAP] Đang cài đặt thư viện thiếu: {', '.join(missing)}")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
            check=False,
        )
        if result.returncode != 0:
            print("[BOOTSTRAP] ❌ Cài đặt thư viện thất bại. Hãy chạy thủ công:")
            print(f"  pip install {' '.join(missing)}")
            sys.exit(1)
        print("[BOOTSTRAP] ✅ Cài đặt thư viện thành công.\n")


_bootstrap_deps()

# ============================================================
# IMPORTS (sau khi bootstrap đảm bảo thư viện đã sẵn sàng)
# ============================================================
import json
import logging
import os
import time

import colorama
import requests
from colorama import Fore, Style

colorama.init(autoreset=True)  # Kích hoạt màu ANSI trên Windows Console

# ============================================================
# Cấu hình Logging — dùng chung handler file với ld_manager.py
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
CONFIG_FILE = "config.json"
PROXIES_FILE = os.path.join("data", "proxies_list.txt")

# SocksDroid — fork bndeff/socksdroid (GPL-3.0)
SOCKSDROID_PACKAGE = "net.typeblog.socks"
SOCKSDROID_ACTIVITY = "net.typeblog.socks/.MainActivity"
SOCKSDROID_GITHUB_API = "https://api.github.com/repos/bndeff/socksdroid/releases/latest"
SOCKSDROID_FALLBACK_URL = (
    "https://github.com/bndeff/socksdroid/releases/download/1.0.4/socksdroid-1.0.4.apk"
)

# Delay giữa các lần gọi ldconsole
INSTALL_DELAY_SEC = 2
CONFIGURE_DELAY_SEC = 1

# ============================================================
# ANSI Color Helpers
# ============================================================
def _warn(msg: str) -> str:
    return f"{Fore.YELLOW}{Style.BRIGHT}{msg}{Style.RESET_ALL}"


def _error(msg: str) -> str:
    return f"{Fore.RED}{Style.BRIGHT}{msg}{Style.RESET_ALL}"


def _ok(msg: str) -> str:
    return f"{Fore.GREEN}{Style.BRIGHT}{msg}{Style.RESET_ALL}"


def _info(msg: str) -> str:
    return f"{Fore.CYAN}{msg}{Style.RESET_ALL}"


def _print_vpn_warning(index: int = 0):
    """In cảnh báo VPN permission nổi bật bằng màu vàng/đỏ."""
    border = "=" * 64
    print()
    print(_warn(border))
    print(_warn("  ⚠️  VPN PERMISSION — THAO TÁC THỦ CÔNG BẮT BUỘC ⚠️"))
    print(_warn(border))
    if index:
        print(_warn(f"  VM Index: {index:02d}"))
    print(_warn("  Thực hiện các bước sau trong MỖI VM đã cài SocksDroid:"))
    print(_warn(""))
    print(f"  {Fore.YELLOW}1.{Style.RESET_ALL} Mở SocksDroid trong VM")
    print(f"  {Fore.YELLOW}2.{Style.RESET_ALL} Nhấn nút {Fore.RED}{Style.BRIGHT}'Connect'{Style.RESET_ALL}")
    print(f"  {Fore.YELLOW}3.{Style.RESET_ALL} Chấp nhận hộp thoại {Fore.RED}{Style.BRIGHT}'Connection Request'{Style.RESET_ALL}")
    print(f"  {Fore.YELLOW}4.{Style.RESET_ALL} Nhấn Disconnect (script sẽ configure lại qua ADB)")
    print()
    print(f"  {Fore.RED}{Style.BRIGHT}Sau khi xác nhận tất cả VM, chạy:{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}  python proxy_manager.py configure{Style.RESET_ALL}")
    print(_warn(border))
    print()


# ============================================================
# Config & Data Loading
# ============================================================

def load_config() -> dict:
    """Đọc config.json, validate các key bắt buộc và trả về dict cấu hình."""
    if not os.path.isfile(CONFIG_FILE):
        logger.error(_error(f"Không tìm thấy file cấu hình: {CONFIG_FILE}"))
        sys.exit(1)

    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)

    required_keys = ["LDPLAYER_PATH", "INSTANCE_COUNT", "INSTANCE_PREFIX", "SOCKSDROID_APK_PATH"]
    for key in required_keys:
        if key not in cfg:
            logger.error(_error(f"config.json thiếu key bắt buộc: '{key}'"))
            sys.exit(1)

    ld_path = cfg["LDPLAYER_PATH"]
    console_path = os.path.join(ld_path, "ldconsole.exe")
    if not os.path.isfile(console_path):
        logger.error(_error(f"Không tìm thấy ldconsole.exe tại: {console_path}"))
        sys.exit(1)

    cfg["_LD_CONSOLE"] = console_path
    return cfg


def load_proxies(path: str = PROXIES_FILE) -> list:
    """
    Parse file proxy list, bỏ qua dòng trống và comment (#).
    Format mỗi dòng: IP:Port:Username:Password

    Returns:
        list of dict: [{"ip": ..., "port": ..., "user": ..., "pass": ...}, ...]
    """
    if not os.path.isfile(path):
        logger.error(_error(f"Không tìm thấy file proxy: {path}"))
        sys.exit(1)

    proxies = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) != 4:
                logger.warning(
                    _warn(f"Dòng {lineno} không đúng format (IP:Port:User:Pass), bỏ qua: {line}")
                )
                continue
            ip, port_str, user, pwd = parts
            try:
                port = int(port_str)
            except ValueError:
                logger.warning(
                    _warn(f"Dòng {lineno}: Port không phải số nguyên ('{port_str}'), bỏ qua.")
                )
                continue
            proxies.append({"ip": ip.strip(), "port": port, "user": user.strip(), "pass": pwd.strip()})

    logger.info(_info(f"Đã load {len(proxies)} proxy từ {path}"))
    return proxies


# ============================================================
# APK Auto-Download
# ============================================================

def download_apk(apk_path: str) -> bool:
    """
    Tự động tải SocksDroid APK từ GitHub nếu file chưa tồn tại.

    Chiến lược:
        1. Gọi GitHub Releases API để lấy URL APK của bản mới nhất.
        2. Nếu API bị block/timeout → fallback về URL hardcoded (v1.0.4).
        3. Streaming download với progress bar thủ công để tránh treo.

    Args:
        apk_path: Đường dẫn lưu APK (từ config.json["SOCKSDROID_APK_PATH"])

    Returns:
        True nếu APK đã có hoặc vừa tải thành công, False nếu thất bại.
    """
    abs_path = os.path.abspath(apk_path)

    # Nếu APK đã tồn tại, không tải lại
    if os.path.isfile(abs_path):
        size_mb = os.path.getsize(abs_path) / (1024 * 1024)
        logger.info(_ok(f"APK đã tồn tại ({size_mb:.1f} MB): {abs_path}"))
        return True

    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)

    # Bước 1: Lấy URL từ GitHub API
    download_url = _resolve_apk_url()

    # Bước 2: Streaming download
    logger.info(_info(f"Đang tải SocksDroid APK từ:\n  {download_url}"))
    print(_info("Đang tải APK... (có thể mất vài giây)"))

    try:
        with requests.get(download_url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 8192

            with open(abs_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded / total * 100
                            # In progress đơn giản mỗi 10%
                            if int(pct) % 10 == 0 and pct > 0:
                                bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
                                print(
                                    f"\r  [{bar}] {pct:.0f}%  ({downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB)",
                                    end="",
                                    flush=True,
                                )
            print()  # Xuống dòng sau progress bar

        size_mb = os.path.getsize(abs_path) / (1024 * 1024)
        logger.info(_ok(f"✅ Tải APK thành công ({size_mb:.1f} MB) → {abs_path}"))
        return True

    except requests.RequestException as exc:
        logger.error(_error(f"❌ Lỗi khi tải APK: {exc}"))
        # Xóa file dở dang nếu có
        if os.path.isfile(abs_path):
            os.remove(abs_path)
        return False


def _resolve_apk_url() -> str:
    """
    Dùng GitHub API để tìm URL APK của bản release mới nhất.
    Fallback về URL hardcoded nếu API không trả về kết quả hợp lệ.
    """
    try:
        resp = requests.get(SOCKSDROID_GITHUB_API, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        assets = data.get("assets", [])
        for asset in assets:
            name = asset.get("name", "")
            if name.endswith(".apk"):
                url = asset.get("browser_download_url", "")
                if url:
                    logger.info(_info(f"GitHub API → phiên bản mới nhất: {data.get('tag_name', '?')} ({name})"))
                    return url
    except Exception as exc:
        logger.warning(_warn(f"GitHub API không khả dụng ({exc}). Dùng URL fallback."))

    logger.info(_info(f"Fallback URL: {SOCKSDROID_FALLBACK_URL}"))
    return SOCKSDROID_FALLBACK_URL


# ============================================================
# Core LDConsole Wrapper
# ============================================================

def _ld_command(ld_console: str, *args) -> tuple:
    """
    Gọi ldconsole.exe với các tham số đã cho.

    Returns:
        (success: bool, output: str)
    """
    cmd = [ld_console] + list(args)
    logger.debug("Chạy lệnh: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            logger.warning(_warn(f"ldconsole exit code {result.returncode}: {output}"))
            return False, output
        return True, output
    except subprocess.TimeoutExpired:
        logger.error(_error(f"Timeout khi gọi ldconsole: {args}"))
        return False, "TIMEOUT"
    except Exception as exc:
        logger.error(_error(f"Lỗi không xác định khi gọi ldconsole: {exc}"))
        return False, str(exc)


def _is_running(ld_console: str, index: int) -> bool:
    """Kiểm tra VM index có đang chạy không."""
    ok, output = _ld_command(ld_console, "isrunning", "--index", str(index))
    return ok and "running" in output.lower()


# ============================================================
# Install & Configure
# ============================================================

def install_app(ld_console: str, index: int, apk_path: str) -> bool:
    """
    Cài APK SocksDroid vào VM có index `index` qua ldconsole installapp.

    Args:
        ld_console: Đường dẫn đến ldconsole.exe
        index:      Index của VM (1-based)
        apk_path:   Đường dẫn đến socksdroid.apk

    Returns:
        True nếu thành công, False nếu thất bại.
    """
    abs_apk = os.path.abspath(apk_path)
    if not os.path.isfile(abs_apk):
        logger.error(_error(f"[VM {index:02d}] APK không tồn tại: {abs_apk}"))
        return False

    if not _is_running(ld_console, index):
        logger.warning(
            _warn(f"[VM {index:02d}] VM chưa chạy — bỏ qua install. Khởi động VM rồi chạy lại.")
        )
        return False

    logger.info(f"[VM {index:02d}] Đang cài SocksDroid APK ...")
    ok, output = _ld_command(ld_console, "installapp", "--index", str(index), "--filename", abs_apk)
    if ok:
        logger.info(_ok(f"[VM {index:02d}] ✅ Cài APK thành công."))
    else:
        logger.error(_error(f"[VM {index:02d}] ❌ Cài APK thất bại: {output}"))
    return ok


def configure_proxy(ld_console: str, index: int, proxy: dict) -> bool:
    """
    Bắn Intent ADB vào SocksDroid để cấu hình SOCKS5 proxy và kích hoạt kết nối.

    Intent extras:
        --es intent_ip    <ip>      SOCKS5 server address
        --ei intent_port  <port>    SOCKS5 port (phải là integer, dùng --ei)
        --es intent_user  <user>    Username
        --es intent_pass  <pass>    Password
        --ez intent_start true      Kích hoạt kết nối VPN ngay lập tức

    Args:
        ld_console: Đường dẫn đến ldconsole.exe
        index:      Index của VM (1-based)
        proxy:      dict {"ip", "port", "user", "pass"}

    Returns:
        True nếu thành công, False nếu thất bại.
    """
    if not _is_running(ld_console, index):
        logger.warning(
            _warn(f"[VM {index:02d}] VM chưa chạy — bỏ qua configure. Khởi động VM rồi chạy lại.")
        )
        return False

    adb_intent = (
        f"am start -n {SOCKSDROID_ACTIVITY} "
        f"--es intent_ip {proxy['ip']} "
        f"--ei intent_port {proxy['port']} "
        f"--es intent_user {proxy['user']} "
        f"--es intent_pass {proxy['pass']} "
        f"--ez intent_start true"
    )

    logger.info(
        f"[VM {index:02d}] Cấu hình proxy → {proxy['ip']}:{proxy['port']} (user: {proxy['user']}) ..."
    )
    ok, output = _ld_command(ld_console, "adb", "--index", str(index), "--command", adb_intent)
    if ok:
        logger.info(_ok(f"[VM {index:02d}] ✅ Cấu hình proxy thành công."))
    else:
        logger.error(_error(f"[VM {index:02d}] ❌ Cấu hình proxy thất bại: {output}"))
    return ok


# ============================================================
# High-level Pipeline
# ============================================================

def install_all(cfg: dict, proxies: list) -> None:
    """Cài APK SocksDroid vào tất cả VM đang chạy."""
    ld_console = cfg["_LD_CONSOLE"]
    apk_path = cfg["SOCKSDROID_APK_PATH"]
    count = cfg["INSTANCE_COUNT"]

    print(_info("=" * 64))
    print(_info(f"  BẮT ĐẦU: Cài APK SocksDroid vào {count} VM ..."))
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

    logger.info(f"INSTALL XONG: {success} thành công / {failed} thất bại / {count} tổng")

    # In cảnh báo VPN permission nổi bật sau khi cài xong
    if success > 0:
        _print_vpn_warning()


def configure_all(cfg: dict, proxies: list) -> None:
    """Cấu hình proxy cho tất cả VM, ghép proxy[i-1] với VM index i."""
    ld_console = cfg["_LD_CONSOLE"]
    count = cfg["INSTANCE_COUNT"]

    if len(proxies) < count:
        logger.error(
            _error(
                f"Số lượng proxy ({len(proxies)}) ít hơn số VM ({count}). "
                f"Hãy thêm đủ {count} dòng vào {PROXIES_FILE}."
            )
        )
        sys.exit(1)

    print(_info("=" * 64))
    print(_info(f"  BẮT ĐẦU: Cấu hình proxy cho {count} VM ..."))
    print(_info("=" * 64))

    success, failed = 0, 0
    for i in range(1, count + 1):
        proxy = proxies[i - 1]
        ok = configure_proxy(ld_console, i, proxy)
        if ok:
            success += 1
        else:
            failed += 1
        if i < count:
            time.sleep(CONFIGURE_DELAY_SEC)

    summary = f"CONFIGURE XONG: {success} thành công / {failed} thất bại / {count} tổng"
    if failed == 0:
        logger.info(_ok(summary))
    else:
        logger.warning(_warn(summary))


def setup_all(cfg: dict, proxies: list) -> None:
    """Full pipeline: Tải APK (nếu cần) → Cài → Cấu hình proxy cho tất cả VM."""
    # Bước 0: Đảm bảo APK đã sẵn sàng
    apk_path = cfg["SOCKSDROID_APK_PATH"]
    if not download_apk(apk_path):
        logger.error(_error("Không thể tải APK. Hãy kiểm tra kết nối mạng hoặc tải thủ công."))
        sys.exit(1)

    install_all(cfg, proxies)
    # (Người dùng cần xác nhận VPN permission thủ công sau install_all)
    # configure_all được tách ra để run sau khi xác nhận VPN
    logger.info(_warn("Sau khi xác nhận VPN permission trên tất cả VM, chạy:"))
    logger.info(_warn("  python proxy_manager.py configure"))


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    valid_commands = ("setup", "download", "install", "configure")
    if len(sys.argv) < 2 or sys.argv[1] not in valid_commands:
        print(f"\n{_info('Cách dùng:')} python {os.path.basename(__file__)} <command>\n")
        print(f"  {Fore.CYAN}setup{Style.RESET_ALL}      — Tải APK + Cài + Hướng dẫn configure (full pipeline)")
        print(f"  {Fore.CYAN}download{Style.RESET_ALL}   — Chỉ tải APK SocksDroid từ GitHub")
        print(f"  {Fore.CYAN}install{Style.RESET_ALL}    — Chỉ cài APK vào tất cả VM đang chạy")
        print(f"  {Fore.CYAN}configure{Style.RESET_ALL}  — Chỉ bắn Intent ADB cấu hình proxy\n")
        sys.exit(1)

    command = sys.argv[1]
    cfg = load_config()
    proxies = load_proxies()

    if command == "download":
        ok = download_apk(cfg["SOCKSDROID_APK_PATH"])
        sys.exit(0 if ok else 1)
    elif command == "install":
        if not os.path.isfile(cfg["SOCKSDROID_APK_PATH"]):
            logger.error(_error("APK chưa tồn tại. Chạy trước: python proxy_manager.py download"))
            sys.exit(1)
        install_all(cfg, proxies)
    elif command == "configure":
        configure_all(cfg, proxies)
    elif command == "setup":
        setup_all(cfg, proxies)


if __name__ == "__main__":
    main()
