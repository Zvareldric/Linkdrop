<div align="center">
  <img src="static/mark.svg" width="56" alt="Logo Linkdrop">

  <h1>Linkdrop</h1>

  <h3>Simpan media publik dalam format yang benar-benar tersedia dari sumbernya.</h3>

  <p><a href="README.md">English</a> · <strong>Bahasa Indonesia</strong></p>

  <p>
    <a href="#mulai-cepat-di-windows-direkomendasikan">Mulai cepat</a> ·
    <a href="#macos-dan-linux">macOS &amp; Linux</a> ·
    <a href="#dokumentasi">Dokumentasi</a> ·
    <a href="#batasan">Batasan</a>
  </p>

  <p>
    <a href="https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml"><img src="https://github.com/Zvareldric/Linkdrop/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&amp;logoColor=white" alt="Python 3.12">
    <img src="https://img.shields.io/badge/Flask-3.x-000000?logo=flask&amp;logoColor=white" alt="Flask 3">
  </p>
</div>

---

<p align="center">
  <img src="docs/assets/linkdrop-demo-id.gif" width="960" alt="Demo animasi antarmuka Linkdrop">
</p>

Linkdrop adalah aplikasi web responsif untuk menganalisis dan mengunduh media publik yang Anda miliki atau berhak unduh. Linkdrop mendukung YouTube, Instagram, TikTok, X, Facebook, serta situs lain yang didukung [yt-dlp](https://github.com/yt-dlp/yt-dlp).

> Gunakan Linkdrop hanya untuk media milik sendiri, domain publik, atau media yang sudah Anda peroleh izinnya. Linkdrop tidak menembus DRM, akses privat, CAPTCHA, maupun pembatasan platform.

## Yang dapat dilakukan

- Menampilkan format dan resolusi asli yang tersedia dari link publik.
- Mengunduh video, audio sumber atau MP3, dan foto asli; post multi-foto dikemas sebagai ZIP.
- Menampilkan progres unduhan dan memberi pilihan lokasi penyimpanan setelah file siap.
- Menyediakan mode beberapa link opsional yang menghasilkan satu paket ZIP.
- Nyaman digunakan di desktop maupun ponsel, dalam Bahasa Indonesia dan Inggris.

## Mulai cepat di Windows (direkomendasikan)

**Kebutuhan:** [Git for Windows](https://git-scm.com/download/win) dan [Docker Desktop](https://www.docker.com/products/docker-desktop/) dengan backend WSL 2.

Buka PowerShell lalu jalankan:

```powershell
git clone https://github.com/Zvareldric/Linkdrop.git
Set-Location Linkdrop

$secret = [Convert]::ToHexString((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
docker build -t linkdrop .
docker run --detach --name linkdrop --restart unless-stopped `
  --publish 127.0.0.1:5050:8080 `
  --env "DOWNLOAD_TOKEN_SECRET=$secret" `
  --env MAX_MEDIA_BYTES=2147483648 `
  linkdrop
```

Buka [http://127.0.0.1:5050](http://127.0.0.1:5050). Container hanya membuka port di komputer Anda. Untuk setup Python native macOS/Linux atau troubleshooting Windows, baca [Panduan Instalasi](docs/INSTALLATION.id.md).

## macOS dan Linux

Pasang Python 3.12, FFmpeg, Git, dan Deno terlebih dahulu. Di macOS dengan Homebrew:

```bash
brew install python@3.12 ffmpeg deno git
```

Di Ubuntu 24.04 atau distribusi Linux lain yang menyediakan Python 3.12:

```bash
sudo apt update
sudo apt install --yes git python3.12 python3.12-venv ffmpeg
```

Lalu, di kedua sistem, pasang dan jalankan Linkdrop:

```bash
git clone https://github.com/Zvareldric/Linkdrop.git
cd Linkdrop
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5000 gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 --timeout 0 app:app
```

Buka [http://127.0.0.1:5000](http://127.0.0.1:5000). Pengguna Linux perlu memasang Deno 2.3+ atau Node.js 22+ melalui sumber paket resmi distribusi bila runtime JavaScript diperlukan.

## Dokumentasi

- [Instalasi](docs/INSTALLATION.id.md) — Windows/Docker Desktop terlebih dahulu, lalu macOS dan Linux.
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
