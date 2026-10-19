# Scan Pasar — liemyo.github.io/scan

Riset pasar & harga dalam tabel: **Facebook Marketplace** dan **Google Maps**.

## Google Maps
Isi kategori (mis. `bakso`), lokasi (`Kota Magelang`), radius (km) → tabel nama, rating, jam operasional, alamat, jarak, WhatsApp/telepon (link wa.me), link Google Maps.
Butuh Google API key (Places API (New)) — tempel di website, disimpan hanya di browser Anda. Maks 60 hasil per scan.

## Facebook Marketplace
Facebook tidak punya API publik & wajib login, jadi website tidak bisa scan sendiri. Helper lokal memakai sesi Facebook Anda:

```bash
pip3 install playwright
python3 fb_scan.py          # server di 127.0.0.1:47950
```
Buka website → tab Facebook → isi kategori, lokasi, harga → **Scan otomatis**. Login Facebook sekali di jendela Chrome yang terbuka.
Alternatif tanpa server: `python3 fb_scan.py --query laptop --location "Kota Magelang" --out laptop.json`, lalu **Impor JSON** di website.

Tabel: nama barang, harga (urut termurah→termahal, klik header untuk urut lain), penjual, lokasi, deskripsi, WhatsApp (diambil dari nomor di deskripsi bila ada), link iklan. Ringkasan harga: termurah/median/rata-rata/termahal.
Catatan: nomor WhatsApp hanya muncul bila penjual menulisnya di deskripsi. Selector Facebook bisa berubah sewaktu-waktu.
