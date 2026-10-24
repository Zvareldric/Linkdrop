# Linkdrop di macOS tanpa Docker

[English](MACOS.md) · **Bahasa Indonesia**

Di Mac, Linkdrop paling ringan dijalankan langsung dengan Python, tanpa Docker. Docker Desktop menjalankan mesin virtual Linux yang memesan beberapa GB RAM, sedangkan Linkdrop sendiri hanya memakai sekitar 100–200 MB saat menganggur.

`scripts/macos-native.sh` memasang Linkdrop sebagai layanan latar belakang macOS (launchd):

- Linkdrop berjalan di `http://127.0.0.1:5050`, menyala sendiri saat login, dan dijalankan ulang otomatis jika berhenti. Tailscale Serve atau Cloudflare Tunnel tetap mengarah ke port yang sama.
- Setiap 15 menit, Linkdrop mengecek GitHub dan memperbarui dirinya. Jika versi baru gagal berjalan, versi sebelumnya dijalankan kembali dan versi gagal itu dilewati sampai ada perbaikan.
- yt-dlp diperbarui sekali sehari, karena platform video sering berubah.

Tidak ada ikon di Dock atau menu bar; Linkdrop berjalan diam-diam di latar belakang.

## Kebutuhan

- [Homebrew](https://brew.sh)
- Clone repository ini, misalnya di `~/development/Linkdrop`

Python 3.12, FFmpeg, dan Deno dipasang otomatis lewat Homebrew jika belum ada.

## Memasang

Jika Linkdrop lama masih berjalan di port 5050 (misalnya layanan launchd buatan sendiri atau `gunicorn` di Terminal), hentikan dulu. Cek dengan:

```bash
lsof -nP -iTCP:5050 -sTCP:LISTEN
```

Lalu, dari folder repository:

```bash
git pull
./scripts/macos-native.sh install
```

Perintah ini menyiapkan salinan aplikasi di `~/.linkdrop/app`, Python virtualenv di `~/.linkdrop/venv`, dan memakai pengaturan di `~/.linkdrop/linkdrop.env` (dibuat jika belum ada). Jika sebelumnya Anda memakai Linkdrop lewat Docker, container `linkdrop` dan pembaruan otomatis versi Docker ikut dihentikan, dan pengaturannya dipakai ulang. Setelah itu Docker Desktop boleh ditutup, dan matikan **Start Docker Desktop when you sign in** jika tidak dipakai untuk hal lain.

Hasil yang diharapkan:

```text
Linkdrop berjalan tanpa Docker di http://127.0.0.1:5050 (versi 1a2b3c4).
Pembaruan otomatis aktif setiap 15 menit. Log: ~/.linkdrop/logs/update.log
```

## Perintah lain

```bash
./scripts/macos-native.sh status      # versi yang berjalan dan layanan launchd
./scripts/macos-native.sh update      # perbarui sekarang tanpa menunggu 15 menit
./scripts/macos-native.sh uninstall   # hentikan dan hapus layanan
```

Log ada di `~/.linkdrop/logs/server.log` (aplikasi) dan `~/.linkdrop/logs/update.log` (pembaruan).

## Mengubah pengaturan

Edit `~/.linkdrop/linkdrop.env` (misalnya `ACCESS_PASSWORD` atau `MAX_MEDIA_BYTES`), lalu jalankan ulang Linkdrop:

```bash
launchctl kickstart -k gui/$(id -u)/com.linkdrop.server
```

`LINKDROP_PORT` di file yang sama menentukan port; jalankan `./scripts/macos-native.sh install` lagi setelah mengubahnya.

## Penggunaan harian

Cukup login ke Mac. Linkdrop dan Tailscale menyala sendiri (aktifkan **Launch at login** di menu Tailscale). Jaga Mac tetap menyala saat ingin dipakai dari perangkat lain: di **System Settings → Battery**, aktifkan **Prevent automatic sleeping when the display is off** untuk mode charger.
