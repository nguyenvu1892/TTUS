# TASK REPORT -- Auto-Wake + Pre-flight Proxy Check cho tiktok_farmer.py

> **Trang thai:** CHO DUYET -- Khong sua code truoc khi nhan APPROVED
> **Thoi gian tao:** 2026-03-06 15:25

---

## 1. XAC NHAN HIEU VIEC

**Yeu cau them 3 buoc vao luong chinh cua `tiktok_farmer.py`:**

- **Buoc 1 -- Auto-Wake:** Tu dong bat VM bang `ldconsole launch`, cho Android boot xong (15-30s) roi moi tiep tuc.
- **Buoc 2 -- Pre-flight Check:** Kiem tra IP thuc tren tung VM qua `adb shell curl`. Neu IP VN/loi -> goi `proxy_manager` ep bat SocksDroid -> kiem tra lai. Neu van fail -> bo qua VM do, log ERROR do, tuyet doi khong mo TikTok.
- **Buoc 3 -- Launch TikTok:** Chi goi `launch_tiktok()` neu Buoc 2 xac nhan IP la My.

**Pham vi thay doi:**
- File chinh: `tiktok_farmer.py`
- Phu thuoc moi: `proxy_manager.py` (goi ham configure/verify)

---

## 2. PHAN TICH KY THUAT & DE XUAT TOI UU

### Buoc 1 -- Auto-Wake

| Giai phap | Danh gia |
|---|---|
| `ldconsole launch --index n` | Dung, nhung khong co return code "boot xong" |
| **Vong lap check boot:** `adb shell getprop sys.boot_completed` | **TOI UU** -- tra ve "1" khi Android san sang nhan lenh ADB |
| Sleep 30s co dinh | SAI -- lang phi thoi gian, co VM boot nhanh hon 15s |

**De xuat thuc thi:**
```python
def wait_for_boot(adb_exe, port, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, out = _adb_shell(adb_exe, port, "getprop sys.boot_completed")
        if ok and out.strip() == "1":
            return True
        time.sleep(3)
    return False   # timeout -> bao loi
```

### Buoc 2 -- Pre-flight IP Check

**Van de voi `curl`:**
- Android emulator khong dam bao co `curl` (AOSP chuan khong co).
- **De xuat uu tien:**
  1. `adb shell curl -s --max-time 10 https://api.myip.com` (thu truoc)
  2. Fallback: `adb shell wget -q -O - https://api.myip.com` (co tren nhieu phien ban Android)
  3. Parse JSON output: `{"ip": "x.x.x.x", "country": "United States"}`

**Lieu phap kiem tra "IP My":**
- Don gian nhat: kiem tra `"United States"` co trong output khong (khong can goi API thu 3).
- Neu response rong hoac timeout -> coi la loi, xu ly nhu "IP khong hop le".

**Goi `proxy_manager.configure_proxy()`:**
- `proxy_manager.py` hien dung `uiautomator2` -> khi import vao `tiktok_farmer.py` se kich hoat bootstrap (cai dat uiautomator2 neu chua co).
- **Rui ro:** `import proxy_manager` se chay ham `_bootstrap_deps()` ngay luc import -> co the lam cham khoi dong.
- **De xuat:** Import lazy (chi import khi can) hoac tach ham `configure_proxy` thanh module rieng khong co side-effect.

### Buoc 3 -- Launch TikTok

- Da co san trong `tiktok_farmer.py`: `_open_tiktok()` + `run_session()`.
- Chi can boc them dieu kien: `if proxy_ok: run_session()`.

---

## 3. DANH GIA RUI RO

| Rui ro | Muc do | Giai phap de xuat |
|---|---|---|
| `curl`/`wget` khong co tren VM | TRUNG BINH | Thu ca 2, fallback: skip check (log WARN) |
| Import `proxy_manager` goi bootstrap | THAP | Dung `importlib` lazy hoac tach helper |
| `ldconsole launch` mo foreground, con truot cac VM khac | THAP | Khong anh huong vì moi VM la process doc lap |
| Boot timeout (VM boot > 90s) | THAP | Timeout 90s, bao loi, bo qua VM do |
| API myip.com bi rate-limit (10 VM check dong thoi) | TRUNG BINH | Add sleep 1s giua moi VM khi check, hoac dung IP khac (ipinfo.io) |
| `configure_proxy()` goi UI Automator mat 15-20s/VM | CAO | Can biet: Pre-flight se lam toan bo flow cham hon nang nề. De xuat: chay Pre-flight SONG SONG (ThreadPoolExecutor) truoc khi mo TikTok |

---

## 4. CAU TRUC THAY DOI DE XUAT

```
tiktok_farmer.py
  + auto_wake_all(cfg)          <-- MOI: bat VM + vong lap cho boot
  + preflight_check(vm, proxy)  <-- MOI: kiem tra IP, bat SocksDroid neu can
  + farm_all()                  <-- SUA: them buoc 1 + 2 truoc khi run_session()
```

**Thu tu goi trong `farm_all()`:**
```
auto_wake_all()         # Buoc 1: bat tat ca VM, cho boot
  |
  v
preflight_check() * n   # Buoc 2: kiem tra IP tung VM (song song)
  |
  v
run_session() * n       # Buoc 3: chi chay VM da pass pre-flight
```

---

## 5. UOC LUONG THOI GIAN THAY DOI

| Hang muc | So dong uoc tinh |
|---|---|
| `auto_wake_all()` | ~40 dong |
| `preflight_check()` | ~60 dong |
| Sua `farm_all()` | ~20 dong |
| Test + commit | -- |
| **Tong** | **~120 dong them vao** |

---

**==> CHO LENH APPROVED TU SEP VU DE BAT DAU CODE <==**
