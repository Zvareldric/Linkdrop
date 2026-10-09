# Linkdrop

[![CI](https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml/badge.svg)](https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)
![Playwright](https://img.shields.io/badge/Diuji_dengan-Playwright-2EAD33?logo=playwright&logoColor=white)

[English](README.md) · **Bahasa Indonesia**

Linkdrop adalah aplikasi web responsif untuk menganalisis dan mengunduh media publik dari YouTube, Instagram, TikTok, X, Facebook, dan situs lain yang didukung [yt-dlp](https://github.com/yt-dlp/yt-dlp). Aplikasi menampilkan format yang benar-benar tersedia dari sumber, memberikan progres unduhan di browser, dan menghasilkan file yang mudah diputar dengan bantuan FFmpeg.

> Gunakan Linkdrop hanya untuk media milik sendiri, domain publik, atau media yang telah Anda peroleh izinnya. Linkdrop tidak menembus DRM, akses privat, CAPTCHA, maupun pembatasan platform.

## Fitur utama

- Menampilkan resolusi sumber dari terendah hingga tertinggi, termasuk 1440p dan 2160p jika tersedia.
- Menggabungkan kualitas video pilihan dengan audio terbaik yang tersedia.
- Mendukung audio sumber serta MP3 128, 192, dan 320 kbps.
- Mempertahankan foto asli dan mengemas post multi-foto menjadi ZIP.
- Menampilkan progres download, pemrosesan, dan transfer secara langsung serta dapat dibatalkan.
- Menggunakan token bertanda tangan yang kedaluwarsa dan memblokir URL jaringan privat.
- Memiliki UI responsif, metadata PWA, dukungan safe area iOS, fokus yang aksesibel, dan reduced motion.
- Berjalan tanpa database maupun state job persisten.

## Cara kerja

```text
Browser
  ├── POST /api/info
  │     └── validasi URL → baca metadata → kirim pilihan bertanda tangan
  └── GET /api/download/<token>?progress=1
        └── download → gabung/konversi → stream file → hapus data sementara
```

Progres dan byte file dikirim melalui satu respons HTTP. Dengan cara ini, aplikasi tidak bergantung pada background thread, daftar job di memori, atau filesystem persisten.

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
python app.py
```

Buka [http://127.0.0.1:5000](http://127.0.0.1:5000).

Untuk macOS, pasang dependency native dengan:

```bash
brew install ffmpeg deno
```

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

Tambahkan `?progress=1` pada endpoint download untuk menggunakan progress stream Linkdrop.

## Batasan

- Extractor dapat berhenti berfungsi ketika platform sumber berubah.
- Beberapa sumber meminta login, cookie, PO token, atau alamat IP residensial.
- IP data center dapat dibatasi atau diblokir oleh platform sumber.
- Konversi MP3 tidak dapat mengembalikan detail yang tidak tersedia pada sumber.
- Label kualitas adalah batas resolusi output, bukan target upscale.

## Dokumentasi

- [Arsitektur](docs/ARCHITECTURE.md)
- [Panduan Deployment](docs/DEPLOYMENT.md)
- [Product Requirements](docs/PRD.md)
- [Keputusan Tooling](docs/TOOLING.md)

## Maintainer

Dikelola oleh [Zvareldric](https://github.com/Zvareldric).
