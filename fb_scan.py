#!/usr/bin/env python3
"""Helper lokal untuk Scan Pasar (liemyo.github.io/scan).

Facebook Marketplace tidak punya API publik dan butuh login, jadi scan dijalankan
di komputer Anda dengan sesi Facebook Anda sendiri (Chrome via Playwright).

Setup sekali:   pip3 install playwright
Server (dipakai website):   python3 fb_scan.py
CLI:   python3 fb_scan.py --query laptop --location "Kota Magelang" --min 1000000 --max 5000000 --out laptop.json
Login Facebook sekali di jendela Chrome yang terbuka; sesi disimpan di ~/.scan-fb-profile.
"""
import argparse, json, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

PORT = 47950
PHONE_RE = re.compile(r"(?:\+?62|0)\s*8[\d\s\-.]{8,13}\d")
LOCK = threading.Lock()


def slug(loc):
    s = re.sub(r"^(kota|kabupaten|kab\.?)\s+", "", loc.strip().lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "category"


def parse_price(text):
    """'Rp1.500.000' / 'IDR 1,500,000' -> 1500000 ; 'Gratis'/'Free' -> 0"""
    if re.search(r"gratis|free", text, re.I):
        return 0
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def find_phone(text):
    m = PHONE_RE.search(text or "")
    return re.sub(r"[\s\-.]", "", m.group(0)) if m else ""


CHROME_APP = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_SRC = Path.home() / "Library/Application Support/Google/Chrome"
CHROME_COPY = Path.home() / ".scan-chrome"
CDP_PORT = 9333
PROFILE_EMAIL = "yulius.krisnawan"


def find_profile(email_part=PROFILE_EMAIL):
    """Cari folder profil Chrome yang akunnya mengandung 'yulius.krisnawan' (mis. 'Default')."""
    try:
        info = json.loads((CHROME_SRC / "Local State").read_text())["profile"]["info_cache"]
        for d, v in info.items():
            if email_part in (v.get("user_name", "") + v.get("name", "")):
                return d
    except Exception:
        pass
    return "Default"


def sync_profile(prof):
    """Salin sesi (cookie) profil Chrome Anda ke folder khusus, supaya tidak mengganggu Chrome yang sedang terbuka."""
    import shutil
    dst = CHROME_COPY
    (dst / prof).mkdir(parents=True, exist_ok=True)
    shutil.copy2(CHROME_SRC / "Local State", dst / "Local State")
    for name in ("Network", "Local Storage", "Session Storage", "IndexedDB", "Preferences", "Secure Preferences", "Cookies", "Cookies-journal"):
        src = CHROME_SRC / prof / name
        if not src.exists():
            continue
        out = dst / prof / name
        try:
            if src.is_dir():
                shutil.copytree(src, out, dirs_exist_ok=True, ignore=shutil.ignore_patterns("LOCK", "*.log.old"))
            else:
                shutil.copy2(src, out)
        except Exception:
            pass
    return dst


def open_chrome(p, headless=False):
    """Buka Google Chrome asli dengan profil yulius.krisnawan, sambungkan lewat CDP. Return (context, proc)."""
    import subprocess, urllib.request
    prof = find_profile()
    ud = sync_profile(prof)
    args = [CHROME_APP, f"--user-data-dir={ud}", f"--profile-directory={prof}", f"--remote-debugging-port={CDP_PORT}",
            "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", "about:blank"]
    if headless:
        args.insert(1, "--headless=new")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.terminate()
        raise RuntimeError("Google Chrome tidak bisa dibuka (cek /Applications/Google Chrome.app).")
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    return browser.contexts[0], proc, browser


def scan(query, location, pmin=None, pmax=None, limit=40, headless=False, log=print):
    from playwright.sync_api import sync_playwright
    params = {"query": query, "exact": "false"}
    if pmin: params["minPrice"] = pmin
    if pmax: params["maxPrice"] = pmax
    url = f"https://www.facebook.com/marketplace/{slug(location)}/search?{urlencode(params)}"
    items = []
    with sync_playwright() as p:
        ctx, proc, browser = open_chrome(p, headless)
        class _C:  # ctx.close() -> tutup Chrome yang kita buka
            def __getattr__(self, n): return getattr(ctx, n)
            def close(self):
                try: browser.close()
                except Exception: pass
                proc.terminate()
        ctx_real, ctx = ctx, _C()
        page = ctx_real.pages[0] if ctx_real.pages else ctx_real.new_page()
        # login: tunggu cookie c_user (maks 5 menit)
        def logged_in():
            return any(c["name"] == "c_user" for c in ctx_real.cookies("https://www.facebook.com"))
        page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        if not logged_in():
            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
            t0 = time.time()
            while not logged_in() and time.time() - t0 < 300:
                log("Silakan login Facebook di jendela Chrome yang terbuka… (menunggu)")
                page.wait_for_timeout(3000)
            if not logged_in():
                ctx.close()
                raise RuntimeError("Belum login Facebook. Login di jendela Chrome yang terbuka lalu scan lagi.")
            log("Login terdeteksi.")
        page.wait_for_timeout(1500)

        # lokasi: coba slug kota; kalau Facebook mengalihkan ke /category/, pilih lewat kotak lokasi
        def at_category():
            return "/marketplace/category" in page.url
        resolved = False
        for sl in dict.fromkeys([slug(location), slug(location) + "-city"]):
            u = f"https://www.facebook.com/marketplace/{sl}/search?{urlencode(params)}"
            page.goto(u, wait_until="domcontentloaded")
            page.wait_for_timeout(3500)
            if not at_category():
                resolved = True
                break
        if not resolved:
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                page.locator('[aria-label*="ocation" i], [aria-label*="okasi" i], div[role="button"]:has-text(", ")').first.click(timeout=4000)
                box = page.locator('input[aria-label*="ocation" i], input[aria-label*="okasi" i], input[placeholder*="city" i], input[placeholder*="kota" i]').first
                box.fill(location, timeout=4000)
                page.wait_for_timeout(2500)
                page.keyboard.press("ArrowDown"); page.keyboard.press("Enter")
                page.wait_for_timeout(1500)
                page.locator('div[role="button"]:has-text("Apply"), div[role="button"]:has-text("Terapkan")').first.click(timeout=4000)
                page.wait_for_timeout(4000)
                resolved = True
            except Exception as e:
                log(f"  pilih lokasi lewat UI gagal: {e}")
        page.wait_for_timeout(2500)

        # scroll sampai cukup iklan
        seen = {}
        stale = 0
        while len(seen) < limit and stale < 4:
            cards = page.evaluate("""() => [...document.querySelectorAll('a[href*="/marketplace/item/"]')].map(a => ({
                href: a.href.split('?')[0],
                lines: a.innerText.split('\\n').map(s => s.trim()).filter(Boolean),
                image: (a.querySelector('img') || {}).src || ''}))""")
            before = len(seen)
            for c in cards:
                if c["href"] not in seen and c["lines"]:
                    seen[c["href"]] = c
            stale = stale + 1 if len(seen) == before else 0
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1500)
        if not seen:
            dbg = str(Path.home() / ".scan-fb-debug.png")
            try: page.screenshot(path=dbg)
            except Exception: pass
            ctx.close()
            raise RuntimeError(f"Tidak ada iklan ditemukan (lokasi \"{location}\" mungkin tidak dikenali Facebook, atau tidak ada hasil). URL: {page.url} — screenshot: {dbg}")
        log(f"Ditemukan {len(seen)} iklan, mengambil detail…")

        for href, c in list(seen.items())[:limit]:
            lines = c["lines"]
            price_line = next((l for l in lines if re.search(r"(rp|idr|gratis|free|\d{2,})", l, re.I)), "")
            rest = [l for l in lines if l != price_line]
            item = {"title": rest[0] if rest else "", "priceText": price_line, "price": parse_price(price_line),
                    "location": rest[1] if len(rest) > 1 else "", "url": href, "image": c["image"],
                    "seller": "", "sellerUrl": "", "description": "", "phone": ""}
            try:
                page.goto(href, wait_until="domcontentloaded")
                page.wait_for_timeout(1800)
                for sel in ('div[role="button"]:has-text("Lihat selengkapnya")', 'div[role="button"]:has-text("See more")'):
                    btn = page.locator(sel).first
                    if btn.count():
                        try: btn.click(timeout=800)
                        except Exception: pass
                info = page.evaluate("""() => {
                    const main = document.querySelector('[role="main"]') || document.body;
                    const s = main.querySelector('a[href*="/marketplace/profile/"]');
                    return {text: main.innerText, seller: s ? s.innerText.trim() : '', sellerUrl: s ? s.href.split('?')[0] : ''};
                }""")
                txt = info["text"]
                m = re.search(r"(?:Deskripsi|Description)\s*\n(.*?)(?:\n(?:Informasi penjual|Seller information|Detail penjual|Seller details|Lokasi|Location)\b|$)", txt, re.S | re.I)
                desc = (m.group(1) if m else "").strip()
                item["description"] = re.sub(r"\s+", " ", desc)[:400]
                item["seller"] = info["seller"] or item["seller"]
                item["sellerUrl"] = info["sellerUrl"]
                item["phone"] = find_phone(desc) or find_phone(txt)
            except Exception as e:
                log(f"  gagal detail {href}: {e}")
            items.append(item)
            log(f"  [{len(items)}/{min(limit, len(seen))}] {item['title'][:50]}")
        ctx.close()
    return items



import math

def hav(a, b, c, d):
    t = math.radians
    h = math.sin(t(c - a) / 2) ** 2 + math.cos(t(a)) * math.cos(t(c)) * math.sin(t(d - b) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def scan_maps(query, location, radius=20, min_rating=0, limit=60, log=print):
    """Scan Google Maps tanpa API key: buka hasil pencarian Maps di Chrome (tanpa login) dan baca daftarnya."""
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as p:
        try:
            br = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            br = p.chromium.launch(headless=True)
        ctx = br.new_context(locale="id-ID", viewport={"width": 1280, "height": 900}, geolocation=None)
        page = ctx.new_page()
        # 1. titik pusat lokasi
        page.goto("https://www.google.com/maps/search/" + urlencode({"q": location})[2:] + "?hl=id", wait_until="domcontentloaded")
        for sel in ('button:has-text("Terima semua")', 'button:has-text("Accept all")'):
            try: page.locator(sel).first.click(timeout=1500)
            except Exception: pass
        m = None
        for _ in range(20):
            m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", page.url) or re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", page.url)
            if m: break
            page.wait_for_timeout(750)
        if not m:
            br.close(); raise RuntimeError(f'Lokasi "{location}" tidak ditemukan di Google Maps.')
        lat, lng = float(m.group(1)), float(m.group(2))
        zoom = max(8, min(17, round(math.log2(1280 * 156543.03 * math.cos(math.radians(lat)) / (2 * radius * 1000)))))
        log(f"Pusat {lat:.4f},{lng:.4f} zoom {zoom}")
        # 2. pencarian kategori di sekitar titik itu
        page.goto(f"https://www.google.com/maps/search/{urlencode({'q': query})[2:]}/@{lat},{lng},{zoom}z?hl=id", wait_until="domcontentloaded")
        feed = page.locator('div[role="feed"]')
        feed.wait_for(timeout=20000)
        last, stale = 0, 0
        while stale < 4:
            n = page.locator('div[role="feed"] a.hfpxzc').count()
            if n >= limit * 2 or page.locator('text=/Anda telah mencapai akhir|reached the end/').count():
                break
            stale = stale + 1 if n == last else 0
            last = n
            feed.evaluate("e => e.scrollBy(0, 4000)")
            page.wait_for_timeout(1300)
        links = page.evaluate("""() => [...document.querySelectorAll('div[role="feed"] a.hfpxzc')].map(a => ({name: a.getAttribute('aria-label'), href: a.href}))""")
        cand = []
        for l in links:
            mm = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", l["href"])
            d = hav(lat, lng, float(mm.group(1)), float(mm.group(2))) if mm else None
            if d is None or d <= radius:
                cand.append({**l, "dist": d})
        cand.sort(key=lambda x: (x["dist"] is None, x["dist"] or 0))
        log(f"{len(links)} hasil, {len(cand)} dalam radius {radius} km")
        # 3. detail tiap tempat
        for c in cand[:limit]:
            row = {"name": c["name"], "maps": c["href"].split("?")[0] if False else c["href"], "dist": c["dist"], "rating": 0, "reviews": 0,
                   "address": "", "phone": "", "today": "", "week": [], "openNow": None}
            try:
                page.goto(c["href"], wait_until="domcontentloaded")
                page.locator("h1").first.wait_for(timeout=8000)
                page.wait_for_timeout(900)
                try:
                    page.locator('[aria-label*="Tampilkan jam buka"], [aria-label*="Show open hours"], [data-item-id="oh"]').first.click(timeout=1200)
                    page.wait_for_timeout(400)
                except Exception:
                    pass
                d = page.evaluate("""() => {
                    const t = s => { const e = document.querySelector(s); return e ? (e.getAttribute('aria-label') || e.innerText || '').trim() : ''; };
                    const rows = [...document.querySelectorAll('table tr')].map(r => r.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean);
                    const rt = document.querySelector('div.F7nice');
                    return {addr: t('button[data-item-id="address"]'), phone: t('button[data-item-id^="phone"]'),
                            rating: rt ? (rt.parentElement.innerText || rt.innerText) : '', hours: rows, status: t('div.OqCZI, [data-item-id="oh"]'),
                            share: location.href};
                }""")
                row["address"] = re.sub(r"^(Alamat|Address):\s*", "", d["addr"])
                ph = re.sub(r"^(Telepon|Phone):\s*", "", d["phone"])
                row["phone"] = ph.strip()
                rm = re.search(r"(\d[.,]\d)\s*\(?([\d.,]*)", d["rating"].replace("\n", " "))
                if rm:
                    row["rating"] = float(rm.group(1).replace(",", "."))
                    row["reviews"] = int(re.sub(r"\D", "", rm.group(2)) or 0)
                row["week"] = d["hours"][:7]
                dow = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"][time.localtime().tm_wday]
                tr = next((h for h in d["hours"] if h.startswith(dow)), "")
                row["today"] = tr[len(dow):].strip(" :") if tr else ""
                st = d["status"].lower()
                row["openNow"] = True if "buka" in st and "tutup" not in st.split("buka")[0] else (False if "tutup" in st else None)
            except Exception as e:
                log(f"  gagal detail {c['name']}: {e}")
            if row["rating"] >= min_rating:
                out.append(row)
            log(f"  [{len(out)}] {row['name'][:40]}")
        br.close()
    return out


class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/ping":
            return self._json(200, {"ok": True})
        if u.path == "/maps":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not q.get("query") or not q.get("location"):
                return self._json(400, {"error": "query & location wajib"})
            if not LOCK.acquire(blocking=False):
                return self._json(429, {"error": "scan lain sedang berjalan"})
            try:
                self._json(200, {"items": scan_maps(q["query"], q["location"], float(q.get("radius") or 20),
                                                    float(q.get("minRating") or 0), int(q.get("limit") or 60))})
            except Exception as e:
                self._json(500, {"error": str(e)})
            finally:
                LOCK.release()
            return
        if u.path != "/scan":
            return self._json(404, {"error": "not found"})
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not q.get("query") or not q.get("location"):
            return self._json(400, {"error": "query & location wajib"})
        if not LOCK.acquire(blocking=False):
            return self._json(429, {"error": "scan lain sedang berjalan"})
        try:
            data = scan(q["query"], q["location"], q.get("min") or None, q.get("max") or None, int(q.get("limit") or 40))
            self._json(200, {"items": data})
        except Exception as e:
            self._json(500, {"error": str(e)})
        finally:
            LOCK.release()

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a): pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query"); ap.add_argument("--location")
    ap.add_argument("--min"); ap.add_argument("--max")
    ap.add_argument("--limit", type=int, default=40); ap.add_argument("--out")
    ap.add_argument("--maps", action="store_true", help="scan Google Maps (pakai --radius)"); ap.add_argument("--radius", type=float, default=20)
    a = ap.parse_args()
    if a.query and a.location and a.maps:
        data = scan_maps(a.query, a.location, a.radius, 0, a.limit)
        out = a.out or f"maps-{slug(a.query)}-{slug(a.location)}.json"
        Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=1)); print(f"{len(data)} tempat -> {out}"); return
    if a.query and a.location:
        data = scan(a.query, a.location, a.min, a.max, a.limit)
        out = a.out or f"fb-{slug(a.query)}-{slug(a.location)}.json"
        Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=1))
        print(f"{len(data)} iklan -> {out} (impor ke website lewat tombol 'Impor JSON')")
        return
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        print(f"Helper sudah berjalan di port {PORT} — tidak perlu dijalankan lagi. Langsung buka https://liemyo.github.io/scan")
        return
    print(f"Helper aktif di http://127.0.0.1:{PORT}  — buka https://liemyo.github.io/scan lalu klik 'Scan otomatis'. Ctrl+C untuk berhenti.")
    srv.serve_forever()


if __name__ == "__main__":
    main()
