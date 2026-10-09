# Panduan Instalasi

[English](INSTALLATION.md) · **Bahasa Indonesia**

Panduan ini memasang Linkdrop untuk penggunaan pribadi. Panduan ini tidak membuka aplikasi ke internet publik. Gunakan hanya media publik yang Anda miliki atau berhak unduh.

## Kebutuhan semua instalasi

- Komputer 64-bit dengan koneksi internet stabil.
- Minimal 2 CPU core, RAM 4 GB, dan ruang kosong 20 GB untuk pemrosesan media sesekali. Video resolusi tinggi membutuhkan ruang sementara lebih besar.
- Izin memasang paket sistem atau Docker, sesuai platform.
- Izin tulis untuk pengguna aplikasi pada direktori `downloads/` di repository atau penyimpanan sementara container.
- `DOWNLOAD_TOKEN_SECRET` yang unik. Nilai ini wajib untuk deployment aman dan tidak boleh masuk Git.

Linkdrop membutuhkan akses internet keluar ke sumber media publik. Setelah dependency terpasang, Linkdrop tidak membutuhkan akses administrator. Browser atau sistem operasi, bukan Linkdrop, yang menentukan lokasi akhir file unduhan.

## macOS — Python native

### 1. Pasang prasyarat

Pasang Homebrew bila belum tersedia, lalu pasang Python 3.12, FFmpeg, Deno, dan Git:

```bash
brew install python@3.12 ffmpeg deno git
```

Homebrew dapat meminta kata sandi administrator macOS ketika memasang paket. Deno dipakai `yt-dlp` untuk challenge JavaScript dari beberapa sumber.

### 2. Pasang Linkdrop

```bash
git clone https://github.com/Zvareldric/Linkdrop.git
cd Linkdrop

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5000 gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 --timeout 0 app:app
```

Buka `http://127.0.0.1:5000` pada komputer yang sama.

### 3. Akses privat dari ponsel (opsional)

Pasang Tailscale di Mac dan ponsel, masuk ke tailnet yang sama, lalu ikuti [Akses Privat Tailscale](TAILSCALE.id.md). Gunakan Gunicorn pada loopback, bukan server development:

```bash
source .venv/bin/activate
export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5050 gunicorn --bind 127.0.0.1:5050 --workers 1 --threads 4 --timeout 0 app:app
```

## Linux — Python native

Perintah ini ditujukan untuk Ubuntu 24.04 atau distribusi lain yang menyediakan Python 3.12. Gunakan perintah package manager yang setara untuk distribusi lain.

### 1. Pasang prasyarat

```bash
sudo apt update
sudo apt install --yes git python3.12 python3.12-venv ffmpeg
```

Pasang Deno 2.3+ atau Node.js 22+ melalui sumber paket resmi distribusi Anda. Deno disarankan untuk sumber yang memerlukan runtime JavaScript.

### 2. Pasang Linkdrop

```bash
git clone https://github.com/Zvareldric/Linkdrop.git
cd Linkdrop

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5000 gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 --timeout 0 app:app
```

Buka `http://127.0.0.1:5000` dari komputer yang sama. `sudo` hanya diperlukan untuk memasang paket; jangan menjalankan Linkdrop sebagai root.

Untuk akses privat dari perangkat lain, pasang Tailscale lalu gunakan perintah Gunicorn loopback pada bagian macOS atau ikuti [Akses Privat Tailscale](TAILSCALE.id.md).

## Windows — Docker Desktop

Gunicorn adalah server berorientasi Unix, sehingga Docker Desktop adalah jalur Windows yang didukung. Docker menjalankan Linkdrop dalam container Linux dari repository ini dan hanya membuka port ke komputer lokal.

### 1. Pasang prasyarat

Pasang:

- [Git for Windows](https://git-scm.com/download/win)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) dengan backend WSL 2

Docker Desktop dapat meminta kata sandi administrator dan Windows dapat meminta akses firewall. Pertahankan port Docker pada `127.0.0.1` seperti contoh di bawah; jangan membuat aturan firewall masuk publik untuk Linkdrop.

### 2. Build dan jalankan Linkdrop

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

Buka `http://127.0.0.1:5050`. Periksa runtime dengan:

```powershell
Invoke-RestMethod http://127.0.0.1:5050/api/health
```

### 3. Akses privat dari ponsel (opsional)

Pasang Tailscale di Windows dan ponsel, masuk ke tailnet yang sama, lalu atur Tailscale Serve untuk meneruskan port lokal `5050`. Ikuti [Akses Privat Tailscale](TAILSCALE.id.md) untuk model keamanan dan troubleshooting. Perintah Docker di atas sudah menjaga Linkdrop tidak terbuka ke LAN karena bind ke loopback.

## Verifikasi instalasi

Sebelum memproses media nyata, pastikan endpoint kesehatan melaporkan `"ok": true`, `"ffmpeg": true`, serta runtime JavaScript bila sudah dipasang:

```bash
curl http://127.0.0.1:5000/api/health
```

Untuk Docker atau Tailscale, ganti `5000` dengan `5050`.

## Izin dan akses opsional

| Item | Wajib? | Alasan |
|---|---|---|
| Administrator atau `sudo` | Hanya saat memasang dependency | Memasang package manager, FFmpeg, Docker, atau Tailscale. |
| Izin tulis ke penyimpanan sementara | Ya | Unduhan dan konversi menggunakan berkas kerja sementara yang dibersihkan setelah selesai. |
| Internet keluar | Ya | `yt-dlp` membaca metadata dan mengambil media publik dari sumber yang dipilih. |
| Tindakan unduh atau bagikan di browser | Ya, ketika menyimpan | Browser atau OS menampilkan lokasi simpan akhir. |
| Login Tailscale | Hanya untuk akses privat jarak jauh | Membatasi akses ke perangkat dalam tailnet yang sama. |
| Cookie situs sumber | Tidak, opsional | Beberapa sumber dapat meminta sesi; perlakukan cookie sebagai secret dan jangan gunakan akun utama pada server publik. |

Jangan membuka instalasi default ini ke publik. Layanan publik memerlukan HTTPS, autentikasi, rate limiting, batas CPU/RAM/disk, monitoring, dan peninjauan ketentuan platform sebelum menerima pengguna tidak tepercaya.
