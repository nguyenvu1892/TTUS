"""
tiktok_farmer.py — Task 3: Giả lập hành vi lướt TikTok FYP trên 10 VM song song.

Luồng:
    1. load_config()           — Đọc config.json (kế thừa từ ld_manager.py)
    2. load_proxies()          — Đọc data/proxies_list.txt (kế thừa từ proxy_manager.py)
    3. _open_tiktok(i)         — Mở TikTok bằng ADB am start
    4. run_session(i, proxy)   — Phiên lướt 15-20 phút cho 1 VM
       ├── _humanized_swipe()  — Vuốt với tọa độ/tốc độ ngẫu nhiên hoá
       ├── _humanized_watch()  — Xem video 7–60 giây (phân phối thực tế)
       └── _maybe_like()       — Like ngẫu nhiên (10–15%)
    5. _kill_tiktok(i)         — Kill TikTok sau phiên
    6. farm_all()              — ThreadPoolExecutor 10 VM song song

CLI:
    python tiktok_farmer.py start       # Chạy farm 10 VM song song
    python tiktok_farmer.py session <n> # Chạy 1 phiên thử cho VM index n
"""

import json
import logging
import math
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Logging — dùng chung file với ld_manager.py & proxy_manager.py
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
CONFIG_FILE = "config.json"
PROXIES_FILE = os.path.join("data", "proxies_list.txt")


# ---------------------------------------------------------------------------
# Config & Proxy Loading (kế thừa pattern từ proxy_manager.py)
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not os.path.isfile(CONFIG_FILE):
        logger.error(f"Không tìm thấy file cấu hình: {CONFIG_FILE}")
        sys.exit(1)

    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)

    required = ["LDPLAYER_PATH", "INSTANCE_COUNT", "TIKTOK_PACKAGE",
                "SCREEN_WIDTH", "SCREEN_HEIGHT",
                "SESSION_MIN_SEC", "SESSION_MAX_SEC"]
    for key in required:
        if key not in cfg:
            logger.error(f"config.json thiếu key: '{key}'")
            sys.exit(1)

    console_path = os.path.join(cfg["LDPLAYER_PATH"], "ldconsole.exe")
    if not os.path.isfile(console_path):
        logger.error(f"Không tìm thấy ldconsole.exe tại: {console_path}")
        sys.exit(1)

    cfg["_LD_CONSOLE"] = console_path
    return cfg


def load_proxies(path: str = PROXIES_FILE) -> list:
    if not os.path.isfile(path):
        logger.error(f"Không tìm thấy file proxy: {path}")
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
                logger.warning(f"Dòng {lineno}: port không hợp lệ, bỏ qua.")
    return proxies


# ---------------------------------------------------------------------------
# LDConsole ADB wrapper
# ---------------------------------------------------------------------------

def _adb(ld_console: str, index: int, command: str) -> tuple:
    """Gửi lệnh ADB vào VM index qua ldconsole bridge."""
    try:
        result = subprocess.run(
            [ld_console, "adb", "--index", str(index), "--command", command],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        logger.warning(f"[VM {index:02d}] ADB timeout: {command[:60]}")
        return False, "TIMEOUT"
    except Exception as exc:
        logger.error(f"[VM {index:02d}] ADB exception: {exc}")
        return False, str(exc)


# ---------------------------------------------------------------------------
# Humanized Behavior Primitives
# ---------------------------------------------------------------------------

def _humanized_swipe(ld_console: str, index: int, cfg: dict) -> bool:
    """
    Vuốt lên để chuyển video với tọa độ và tốc độ được ngẫu nhiên hoá.

    Mô hình 3 chiều:
      X: Gaussian(μ=center, σ=7% chiều rộng) — ngón tay không bao giờ chính tâm
      Y: Uniform [65–78%] bắt đầu, vuốt [48–72%] chiều cao
      Duration: Log-Normal(μ=350ms, σ=0.4) — đuôi dài về phía chậm như người thật
    """
    W = cfg["SCREEN_WIDTH"]   # 1080
    H = cfg["SCREEN_HEIGHT"]  # 1920
    cx = W // 2               # 540

    # --- X axis ---
    x_start = int(random.gauss(cx, W * 0.07))
    x_start = max(int(W * 0.18), min(int(W * 0.82), x_start))   # clamp [195, 885]
    x_end   = x_start + int(random.gauss(0, 18))                  # drift ngang nhẹ
    x_end   = max(80, min(W - 80, x_end))

    # --- Y axis ---
    y_start     = int(random.uniform(H * 0.65, H * 0.78))          # [1248, 1498]
    swipe_ratio = random.uniform(0.48, 0.72)
    y_end       = int(y_start - H * swipe_ratio)
    y_end       = max(int(H * 0.09), y_end)                        # không sát đỉnh

    # --- Duration: Log-Normal → range ~180–800ms ---
    raw_dur = random.lognormvariate(math.log(350), 0.4)
    duration = int(max(180, min(800, raw_dur)))

    cmd = f"input swipe {x_start} {y_start} {x_end} {y_end} {duration}"
    ok, _ = _adb(ld_console, index, cmd)

    # Micro-pause sau swipe (mắt nhìn vào màn hình trước khi đọc content)
    time.sleep(random.uniform(0.08, 0.35))
    return ok


def _humanized_watch(index: int) -> float:
    """
    Giả lập thời gian xem video.

    Phân phối:
        8%  → video "hay": 45–60 giây (người bị cuốn)
        92% → thường:      7–35 giây (lướt bình thường)

    Returns:
        Số giây đã ngủ (để log).
    """
    if random.random() < 0.08:
        # Video hay — dừng lại lâu hơn
        watch_sec = random.uniform(45, 60)
        logger.info(f"[VM {index:02d}] 🎬 Video hay! Xem {watch_sec:.1f}s")
    else:
        watch_sec = random.uniform(7, 35)

    time.sleep(watch_sec)
    return watch_sec


def _maybe_like(ld_console: str, index: int, cfg: dict) -> bool:
    """
    Thả tim với xác suất 10–15% bằng double tap.
    Tọa độ double tap được jitter ngẫu nhiên quanh trung tâm màn hình.
    Tuyệt đối không like liên tục nhiều video liên tiếp.

    Returns:
        True nếu đã like, False nếu không.
    """
    # Xác suất like theo phiên: 10–15% (lấy ngưỡng random mỗi lần để không đều)
    like_threshold = random.uniform(0.10, 0.15)
    if random.random() > like_threshold:
        return False

    W = cfg["SCREEN_WIDTH"]
    H = cfg["SCREEN_HEIGHT"]

    # Double tap: tap 1
    tap_x = W // 2 + int(random.gauss(0, 22))
    tap_y = int(H * 0.50) + int(random.gauss(0, 30))
    tap_x = max(100, min(W - 100, tap_x))
    tap_y = max(int(H * 0.30), min(int(H * 0.70), tap_y))

    _adb(ld_console, index, f"input tap {tap_x} {tap_y}")
    # Khoảng cách giữa 2 tap của double tap: 80–140ms (ngưỡng Android)
    time.sleep(random.uniform(0.08, 0.14))
    # Tap 2 — lệch nhẹ vài pixel so với tap 1 (ngón tay không đứng yên tuyệt đối)
    tap_x2 = tap_x + int(random.gauss(0, 4))
    tap_y2 = tap_y + int(random.gauss(0, 4))
    _adb(ld_console, index, f"input tap {tap_x2} {tap_y2}")

    logger.info(f"[VM {index:02d}] ❤️  Like! ({tap_x},{tap_y})")

    # Pause nhỏ sau like để giả lập phản ứng nhìn thấy animation
    time.sleep(random.uniform(0.4, 1.2))
    return True


# ---------------------------------------------------------------------------
# TikTok App Control
# ---------------------------------------------------------------------------

def _open_tiktok(ld_console: str, index: int, package: str) -> bool:
    """Mở TikTok bằng am start và chờ app load."""
    logger.info(f"[VM {index:02d}] Đang mở TikTok...")
    cmd = f"am start -n {package}/.main.MainActivity"
    ok, output = _adb(ld_console, index, cmd)
    if ok:
        # Chờ TikTok load FYP: 4–7 giây
        load_wait = random.uniform(4.0, 7.0)
        logger.info(f"[VM {index:02d}] TikTok đã mở. Chờ FYP load {load_wait:.1f}s ...")
        time.sleep(load_wait)
    else:
        logger.error(f"[VM {index:02d}] Mở TikTok thất bại: {output}")
    return ok


def _kill_tiktok(ld_console: str, index: int, package: str) -> bool:
    """Force-stop TikTok để kết thúc phiên sạch sẽ."""
    logger.info(f"[VM {index:02d}] Đang kill TikTok...")
    ok, _ = _adb(ld_console, index, f"am force-stop {package}")
    return ok


# ---------------------------------------------------------------------------
# Session Engine — 1 phiên cho 1 VM
# ---------------------------------------------------------------------------

def run_session(index: int, proxy: dict, cfg: dict) -> dict:
    """
    Chạy 1 phiên lướt TikTok hoàn chỉnh cho VM `index`.

    Phiên kéo dài SESSION_MIN_SEC–SESSION_MAX_SEC giây (15–20 phút).
    Trong mỗi vòng lặp:
        1. Xem video (_humanized_watch)
        2. Vuốt qua video tiếp theo (_humanized_swipe)
        3. Thỉnh thoảng like (_maybe_like)

    Args:
        index: VM index (1-based)
        proxy: dict {"ip", "port", "user", "pass"} — dùng để log
        cfg:   config dict

    Returns:
        dict kết quả phiên: {index, videos_watched, likes, session_sec, status}
    """
    ld_console  = cfg["_LD_CONSOLE"]
    package     = cfg["TIKTOK_PACKAGE"]
    session_sec = random.uniform(cfg["SESSION_MIN_SEC"], cfg["SESSION_MAX_SEC"])

    logger.info(
        f"[VM {index:02d}] ▶ Bắt đầu phiên "
        f"({session_sec/60:.1f} phút | proxy: {proxy['ip']}:{proxy['port']})"
    )

    result = {"index": index, "videos_watched": 0, "likes": 0,
              "session_sec": round(session_sec), "status": "ok"}

    # Mở TikTok
    if not _open_tiktok(ld_console, index, package):
        result["status"] = "error_open"
        return result

    session_start = time.monotonic()
    last_like_video = -99  # Chống like liên tiếp: ít nhất 5 video giữa 2 lần like

    try:
        while (time.monotonic() - session_start) < session_sec:
            # 1. Xem video hiện tại
            _humanized_watch(index)
            result["videos_watched"] += 1

            # 2. Thỉnh thoảng like — không like video liền kề
            current_video = result["videos_watched"]
            if (current_video - last_like_video) >= 5:
                liked = _maybe_like(ld_console, index, cfg)
                if liked:
                    result["likes"] += 1
                    last_like_video = current_video

            # 3. Kiểm tra còn thời gian không trước khi swipe
            if (time.monotonic() - session_start) >= session_sec:
                break

            # 4. Vuốt sang video tiếp
            _humanized_swipe(ld_console, index, cfg)

    except Exception as exc:
        logger.error(f"[VM {index:02d}] Lỗi trong phiên: {exc}")
        result["status"] = "error_runtime"

    # Kết thúc phiên
    _kill_tiktok(ld_console, index, package)
    elapsed = time.monotonic() - session_start
    like_rate = result["likes"] / max(1, result["videos_watched"]) * 100

    logger.info(
        f"[VM {index:02d}] ■ Kết thúc phiên | "
        f"{result['videos_watched']} video | "
        f"{result['likes']} likes ({like_rate:.1f}%) | "
        f"{elapsed/60:.1f} phút | status: {result['status']}"
    )
    return result


# ---------------------------------------------------------------------------
# Farm Orchestrator — ThreadPoolExecutor 10 VM song song
# ---------------------------------------------------------------------------

def farm_all(cfg: dict, proxies: list) -> None:
    """
    Điều phối 10 VM lướt TikTok CÙNG MỘT LÚC bằng ThreadPoolExecutor.

    Lý do chọn Thread thay vì Process:
        - Workload I/O-bound (90% là time.sleep + ADB subprocess)
        - GIL được release trong sleep() và subprocess.run()
        - Không cần serialize/deserialize data giữa process
        - Ít overhead hơn trên hệ thống 56-thread hiện tại
    """
    count = cfg["INSTANCE_COUNT"]
    if len(proxies) < count:
        logger.error(f"Cần {count} proxy, hiện có {len(proxies)}. Kiểm tra {PROXIES_FILE}.")
        sys.exit(1)

    logger.info("=" * 64)
    logger.info(f"  FARM BẮT ĐẦU: {count} VM | ThreadPoolExecutor(max_workers={count})")
    logger.info("=" * 64)

    farm_start = time.monotonic()
    results = []

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = {
            pool.submit(run_session, i, proxies[i - 1], cfg): i
            for i in range(1, count + 1)
        }
        for future in as_completed(futures):
            vm_idx = futures[future]
            try:
                res = future.result()
                results.append(res)
            except Exception as exc:
                logger.error(f"[VM {vm_idx:02d}] Future exception: {exc}")
                results.append({"index": vm_idx, "status": "future_error"})

    # Tổng kết farm
    elapsed = time.monotonic() - farm_start
    total_videos = sum(r.get("videos_watched", 0) for r in results)
    total_likes  = sum(r.get("likes", 0) for r in results)
    ok_count     = sum(1 for r in results if r.get("status") == "ok")

    logger.info("=" * 64)
    logger.info(f"  FARM HOÀN TẤT trong {elapsed/60:.1f} phút")
    logger.info(f"  VM thành công: {ok_count}/{count}")
    logger.info(f"  Tổng video đã xem: {total_videos}")
    logger.info(f"  Tổng lượt like:    {total_likes} "
                f"({total_likes/max(1,total_videos)*100:.1f}%)")
    logger.info("=" * 64)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    valid = ("start", "session")
    if len(sys.argv) < 2 or sys.argv[1] not in valid:
        print(f"\nCách dùng: python {os.path.basename(__file__)} <command>\n")
        print("  start        — Chạy farm 10 VM song song (ThreadPoolExecutor)")
        print("  session <n>  — Chạy 1 phiên thử cho VM index n\n")
        sys.exit(1)

    command = sys.argv[1]
    cfg     = load_config()
    proxies = load_proxies()

    if command == "start":
        farm_all(cfg, proxies)

    elif command == "session":
        if len(sys.argv) < 3:
            print("Thiếu index VM. Ví dụ: python tiktok_farmer.py session 1")
            sys.exit(1)
        try:
            idx = int(sys.argv[2])
        except ValueError:
            print("Index phải là số nguyên.")
            sys.exit(1)

        count = cfg["INSTANCE_COUNT"]
        if not (1 <= idx <= count):
            print(f"Index phải nằm trong [1, {count}].")
            sys.exit(1)

        if len(proxies) < idx:
            print(f"Không có proxy cho VM {idx}.")
            sys.exit(1)

        result = run_session(idx, proxies[idx - 1], cfg)
        logger.info(f"Session result: {result}")


if __name__ == "__main__":
    main()
