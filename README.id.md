# Linkdrop

[![CI](https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml/badge.svg)](https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)

[English](README.md) · **Bahasa Indonesia**

Linkdrop adalah aplikasi web responsif untuk menganalisis dan mengunduh media publik yang Anda miliki atau berhak unduh. Linkdrop mendukung YouTube, Instagram, TikTok, X, Facebook, serta situs lain yang didukung [yt-dlp](https://github.com/yt-dlp/yt-dlp).

![Animasi antarmuka Linkdrop](docs/assets/linkdrop-demo.gif)

> Gunakan Linkdrop hanya untuk media milik sendiri, domain publik, atau media yang sudah Anda peroleh izinnya. Linkdrop tidak menembus DRM, akses privat, CAPTCHA, maupun pembatasan platform.

## Yang dapat dilakukan

- Menampilkan format dan resolusi asli yang tersedia dari link publik.
- Mengunduh video, audio sumber atau MP3, dan foto asli; post multi-foto dikemas sebagai ZIP.
- Menampilkan progres unduhan dan memberi pilihan lokasi penyimpanan setelah file siap.
- Menyediakan mode beberapa link opsional yang menghasilkan satu paket ZIP.
- Nyaman digunakan di desktop maupun ponsel, dalam Bahasa Indonesia dan Inggris.

## Mulai cepat

**Kebutuhan:** Python 3.12+, FFmpeg, serta Deno 2.3+ atau Node.js 22+ untuk beberapa sumber dengan pemeriksaan JavaScript.

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

Buka [http://127.0.0.1:5000](http://127.0.0.1:5000). Setup ini untuk macOS dan Linux; panduan instalasi juga mencakup Windows.

## Dokumentasi

- [Instalasi](docs/INSTALLATION.id.md) — macOS, Linux, dan Windows/Docker Desktop.
- [Akses privat dari ponsel](docs/TAILSCALE.id.md) — akses aman melalui Tailscale.
- [Deployment](docs/DEPLOYMENT.md) — hosting persisten dan batas operasional.
- [Arsitektur](docs/ARCHITECTURE.md) — alur media, file sementara, dan model keamanan.

## Pengujian

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v

npm ci
npx playwright install chromium webkit
npm run test:e2e
```

## Batasan

Situs sumber dan extractornya dapat berubah atau memblokir request. Platform dapat meminta sesi akun yang sah, cookie, PO token, atau wilayah yang didukung; Linkdrop tidak melewati persyaratan tersebut. Konversi MP3 tidak dapat menambah kualitas yang tidak ada pada sumber.

## Maintainer

Dikelola oleh [Zvareldric](https://github.com/Zvareldric).
