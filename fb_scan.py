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
PROFILE = str(Path.home() / ".scan-fb-profile")
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


def scan(query, location, pmin=None, pmax=None, limit=40, headless=False, log=print):
    from playwright.sync_api import sync_playwright
    params = {"query": query, "exact": "false"}
    if pmin: params["minPrice"] = pmin
    if pmax: params["maxPrice"] = pmax
    url = f"https://www.facebook.com/marketplace/{slug(location)}/search?{urlencode(params)}"
    items = []
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(PROFILE, channel="chrome", headless=headless,
                                                       viewport={"width": 1280, "height": 900}, locale="id-ID")
        except Exception:
            ctx = p.chromium.launch_persistent_context(PROFILE, headless=headless,
                                                       viewport={"width": 1280, "height": 900}, locale="id-ID")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # login: tunggu cookie c_user (maks 5 menit)
        def logged_in():
            return any(c["name"] == "c_user" for c in ctx.cookies("https://www.facebook.com"))
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
    a = ap.parse_args()
    if a.query and a.location:
        data = scan(a.query, a.location, a.min, a.max, a.limit)
        out = a.out or f"fb-{slug(a.query)}-{slug(a.location)}.json"
        Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=1))
        print(f"{len(data)} iklan -> {out} (impor ke website lewat tombol 'Impor JSON')")
        return
    print(f"Helper aktif di http://127.0.0.1:{PORT}  — buka https://liemyo.github.io/scan lalu klik 'Scan otomatis'. Ctrl+C untuk berhenti.")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
