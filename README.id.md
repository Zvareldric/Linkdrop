# Linkdrop

[![CI](https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml/badge.svg)](https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)
![Playwright](https://img.shields.io/badge/Diuji_dengan-Playwright-2EAD33?logo=playwright&logoColor=white)

[English](README.md) · **Bahasa Indonesia**

Linkdrop adalah aplikasi web responsif untuk menganalisis dan mengunduh media publik dari YouTube, Instagram, TikTok, X, Facebook, dan situs lain yang didukung [yt-dlp](https://github.com/yt-dlp/yt-dlp). Aplikasi menampilkan format yang benar-benar tersedia dari sumber, memberikan progres unduhan di browser, dan menghasilkan file yang mudah diputar dengan bantuan FFmpeg.

> Gunakan Linkdrop hanya untuk media milik sendiri, domain publik, atau media yang telah Anda peroleh izinnya. Linkdrop tidak menembus DRM, akses privat, CAPTCHA, maupun pembatasan platform.

## Untuk siapa?

Linkdrop dibuat untuk penggunaan pribadi: editor yang mengelola materi berizin, penggemar K-pop yang menyimpan konten publik sesuai izin pemiliknya, kreator, dan pengguna lain yang membutuhkan salinan media publik untuk koleksi atau pekerjaan pribadi. Ini bukan layanan unduh publik atau alat untuk mendistribusikan ulang karya orang lain.

## Fitur utama

- Menampilkan resolusi sumber dari terendah hingga tertinggi, termasuk 1440p dan 2160p jika tersedia.
- Menggabungkan kualitas video pilihan dengan audio terbaik yang tersedia.
- Mengonversi stream MP4 yang tidak kompatibel menjadi H.264/AAC tanpa mengubah resolusi pilihan.
- Mendukung audio sumber serta MP3 128, 192, dan 320 kbps.
- Mempertahankan foto asli dan mengemas post multi-foto menjadi ZIP.
- Mempertahankan unduhan satu link sebagai mode utama dan menyediakan antrean multi-link opsional dengan pilihan per media.
- Menggabungkan link pilihan dalam ZIP datar sesuai kapasitas penyimpanan; galeri menjadi folder, bukan ZIP di dalam ZIP.
- Menampilkan progres download, pemrosesan, dan transfer secara langsung serta dapat dibatalkan.
- Menyediakan dialog simpan yang dapat memakai atau mengubah nama file, sambil mempertahankan ekstensi asli di file picker desktop, menu bagikan ponsel, dan download browser.
- Menggunakan token bertanda tangan yang kedaluwarsa dan memblokir URL jaringan privat.
- Memiliki UI responsif, metadata PWA, dukungan safe area iOS, fokus yang aksesibel, dan reduced motion.
- Berjalan tanpa database maupun state job persisten.

## Cara kerja

```text
Browser
  ├── POST /api/info
  │     └── validasi URL → baca metadata → kirim pilihan bertanda tangan
  ├── GET /api/download/<token>?progress=1
  │     └── download → gabung/konversi → stream file → hapus data sementara
  └── POST /api/batch/download?progress=1
        └── validasi pilihan → proses berurutan → buat ZIP → hapus data sementara
```

Progres dan byte file dikirim melalui satu respons HTTP. Setiap worker sementara tetap terikat pada respons tersebut tanpa daftar job persisten atau penyimpanan hasil permanen.

### Beberapa link

`Satu link` tetap menjadi mode utama. Pilih `Beberapa link` untuk menempel satu URL publik per baris, menganalisis maksimal dua URL bersamaan, menentukan output setiap media, dan hanya mengunduh media yang dicentang dalam satu ZIP datar. Aset carousel dan galeri ditempatkan dalam folder bernama di dalam ZIP tersebut, sehingga tidak perlu ekstraksi kedua. Pilihan di atas 10 media menampilkan peringatan; setiap paket memuat maksimal 30 link pilihan dan dibagi pada 80% batas aman penyimpanan sementara. Paket diunduh berurutan agar ZIP berikutnya tidak menggantikan file yang belum disimpan.

### Menyimpan file

Saat pemrosesan mencapai 100%, Linkdrop membuka dialog simpan dengan nama file dari server. Anda dapat mengubah nama tersebut atau membiarkannya sebagai nama default. Linkdrop mempertahankan ekstensi asli agar file tetap dikenali sebagai jenis media yang benar. Lokasi akhir dipilih oleh browser atau sistem operasi: file picker desktop bila didukung, menu bagikan di ponsel bila tersedia, atau lokasi unduhan normal browser.

## Kebutuhan sistem

- Python 3.12 atau lebih baru
- FFmpeg
- Deno 2.3+ atau Node.js 22+ untuk challenge JavaScript pada format YouTube tertentu
- Node.js 20+ hanya untuk menjalankan pengujian Playwright

## Mulai cepat

```bash
git clone https://github.com/Zvareldric/Linkdrop.git
cd Linkdrop

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5000 gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 --timeout 0 app:app
```

Buka [http://127.0.0.1:5000](http://127.0.0.1:5000).

Quick start native ini untuk macOS dan Linux. Untuk Windows Docker Desktop, paket khusus platform, izin, dan akses privat dari ponsel, baca [Panduan Instalasi](docs/INSTALLATION.id.md). Gunakan `python app.py` hanya untuk development sementara karena server development tersebut bind ke antarmuka jaringan.

## Akses privat dari ponsel

Untuk penggunaan pribadi, setup yang disarankan adalah menjalankan Linkdrop di komputer lalu membukanya hanya melalui jaringan privat [Tailscale](https://tailscale.com/). Paket Personal Tailscale tersedia gratis untuk penggunaan non-komersial; periksa [ketentuan paketnya](https://tailscale.com/pricing) sebelum digunakan. Anda tidak perlu melakukan deployment publik dan aplikasi tetap dapat diakses dari data seluler maupun Wi-Fi lain.

Jalankan Linkdrop pada port `5050`:

```bash
source .venv/bin/activate
PORT=5050 gunicorn --bind 127.0.0.1:5050 --workers 1 --threads 4 --timeout 0 app:app
```

Pada terminal lain, hubungkan server lokal ke tailnet:

```bash
tailscale serve --bg 5050
tailscale serve status
```

Pasang Tailscale di ponsel, masuk ke tailnet yang sama, lalu buka alamat HTTPS yang ditampilkan oleh `tailscale serve`. Komputer harus tetap menyala, tidak dalam kondisi sleep, tersambung ke Tailscale, dan menjalankan Linkdrop.

Baca [Panduan Akses Privat Tailscale](docs/TAILSCALE.id.md) untuk instalasi, keamanan, cara menghentikan layanan, dan troubleshooting.

### Jika komputer harus selalu aktif

Gunakan VPS atau host container persisten bila Linkdrop perlu tersedia saat komputer pribadi mati. Opsi ini memerlukan biaya dari penyedia VPS dan konfigurasi keamanan tambahan; tetap batasi akses dengan Tailscale atau proxy HTTPS, jangan membuka aplikasi tanpa rate limiting. Lihat [Panduan Deployment](docs/DEPLOYMENT.md).

## Konfigurasi

| Variabel | Default | Fungsi |
|---|---:|---|
| `DOWNLOAD_TOKEN_SECRET` | Fallback development | Kunci token; wajib diubah di production |
| `DOWNLOAD_TOKEN_MAX_AGE` | `900` | Masa berlaku token dalam detik |
| `MAX_MEDIA_BYTES` | 2 GB lokal; 220 MB di Vercel | Batas ukuran media kerja |
| `HTTP_TIMEOUT` | `30` | Timeout koneksi sumber dalam detik |
| `DOWNLOAD_DIR` | `downloads/` atau `/tmp/linkdrop` | Folder kerja sementara |
| `YTDLP_COOKIES_B64` | Kosong | File cookie Netscape dalam base64 untuk server |
| `YTDLP_COOKIES_FILE` | Kosong | Lokasi file cookie Netscape lokal |
| `YTDLP_COOKIES_FROM_BROWSER` | Kosong | Sumber cookie browser lokal |
| `FFMPEG_LOCATION` | `PATH` sistem | Lokasi FFmpeg kustom |
| `YTDLP_JS_RUNTIME` | Deteksi otomatis | Runtime JavaScript, misalnya `deno` |

Jangan commit `.env`, cookie browser, atau sesi akun yang diekspor. Jangan gunakan cookie akun utama pada instance publik.

## Pengujian

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Pengujian browser desktop dan ponsel:

```bash
npm ci
npx playwright install chromium webkit
npm run test:e2e
```

Matriks browser mencakup Chrome desktop, Pixel 7, dan iPhone 13/WebKit.

## Deployment container

`Dockerfile` utama dapat digunakan pada Docker host, Dokku, Coolify, dan CapRover:

```bash
docker build -t linkdrop .
docker run --rm \
  -p 5050:8080 \
  -e DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)" \
  linkdrop
```

Untuk penggunaan publik, VM atau container host persisten lebih andal daripada serverless function berdurasi pendek. Pemrosesan media besar membutuhkan CPU, ruang disk sementara, dan koneksi streaming yang stabil. Baca [Panduan Deployment](docs/DEPLOYMENT.md) untuk konfigurasi production dan perbandingan provider.

## Struktur proyek

```text
Linkdrop/
├── .github/workflows/ci.yml   # Unit test dan browser test otomatis
├── docs/                      # Arsitektur, deployment, PRD, dan tooling
├── downloads/                 # Folder kerja lokal yang diabaikan Git
├── static/                    # Manifest PWA dan aset visual
├── templates/                 # Antarmuka web responsif
├── tests/
│   └── e2e/                   # Pengujian desktop dan ponsel Playwright
├── app.py                     # Aplikasi Flask dan pipeline media
├── Dockerfile                 # Image production umum
├── Dockerfile.vercel          # Image khusus Vercel
├── playwright.config.js       # Matriks pengujian browser
└── requirements.txt           # Dependency runtime Python
```

## Endpoint

| Method | Endpoint | Keterangan |
|---|---|---|
| `GET` | `/` | Antarmuka web |
| `GET` | `/api/health` | Pemeriksaan runtime dan FFmpeg |
| `POST` | `/api/info` | Validasi URL publik dan daftar format |
| `GET` | `/api/download/<token>` | Menyiapkan dan men-stream output pilihan |
| `POST` | `/api/batch/download` | Memproses pilihan bertanda tangan secara berurutan dan men-stream paket ZIP |

Tambahkan `?progress=1` pada endpoint download untuk menggunakan progress stream Linkdrop.

## Batasan

- Extractor dapat berhenti berfungsi ketika platform sumber berubah.
- Beberapa sumber meminta login, cookie, PO token, atau alamat IP residensial.
- IP data center dapat dibatasi atau diblokir oleh platform sumber.
- Konversi MP3 tidak dapat mengembalikan detail yang tidak tersedia pada sumber.
- Label kualitas adalah batas resolusi output, bukan target upscale.

## Dokumentasi

- [Arsitektur](docs/ARCHITECTURE.md)
- [Panduan Instalasi](docs/INSTALLATION.id.md)
- [Panduan Deployment](docs/DEPLOYMENT.md)
- [Akses Privat Tailscale](docs/TAILSCALE.id.md)
- [Product Requirements](docs/PRD.md)
- [Keputusan Tooling](docs/TOOLING.md)

## Maintainer

Dikelola oleh [Zvareldric](https://github.com/Zvareldric).
