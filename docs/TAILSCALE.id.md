# Akses Privat dengan Tailscale

[English](TAILSCALE.md) · **Bahasa Indonesia**

Tailscale Serve adalah cara yang disarankan untuk memakai Linkdrop secara pribadi dari ponsel, tablet, atau komputer lain. Linkdrop tetap berjalan di komputer Anda, sedangkan Tailscale menyediakan alamat HTTPS terenkripsi yang hanya dapat dibuka oleh perangkat dalam tailnet yang sama.

Setup ini ditujukan untuk penggunaan pribadi. Linkdrop tidak dipindahkan ke cloud, tidak menjadi layanan publik, dan tidak dapat diakses ketika komputer host mati atau sleep.

Untuk penggunaan pribadi non-komersial, paket Personal Tailscale tersedia gratis pada saat dokumentasi ini diperbarui. Periksa kembali [paket dan ketentuan Tailscale](https://tailscale.com/pricing), karena batas layanan dapat berubah.

## Kebutuhan

- Linkdrop sudah terpasang dan dapat berjalan secara lokal
- FFmpeg dan ffprobe tersedia di komputer host
- Akun Tailscale
- Tailscale terpasang pada host dan setiap perangkat pengguna
- Semua perangkat masuk ke tailnet yang sama

## 1. Pasang Tailscale

Pada macOS, pasang aplikasi desktop:

```bash
brew install --cask tailscale-app
open -a Tailscale
```

Masuk melalui aplikasi Tailscale di menu bar. Pasang Tailscale dari App Store atau Play Store pada ponsel, lalu masuk menggunakan akun yang sama.

Installer resmi untuk sistem operasi lain tersedia di [tailscale.com/download](https://tailscale.com/download).

## 2. Jalankan Linkdrop

Dari direktori repository:

```bash
source .venv/bin/activate
export DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)"
PORT=5050 gunicorn \
  --bind 127.0.0.1:5050 \
  --workers 1 \
  --threads 4 \
  --timeout 0 \
  --access-logfile - \
  app:app
```

Biarkan terminal ini tetap terbuka. Pastikan server lokal sehat:

```bash
curl http://127.0.0.1:5050/api/health
```

Respons seharusnya memuat `"ok": true` dan `"ffmpeg": true`.

## 3. Aktifkan akses HTTPS privat

Buka terminal lain dan jalankan:

```bash
tailscale serve --bg 5050
tailscale serve status
```

Pada penggunaan pertama, perintah dapat menampilkan URL persetujuan Tailscale. Buka URL tersebut satu kali untuk mengaktifkan Serve pada tailnet, lalu ulangi perintahnya. Status akan menampilkan alamat seperti:

```text
https://nama-perangkat.nama-tailnet.ts.net
|-- / proxy http://127.0.0.1:5050
```

Buka alamat HTTPS tersebut dari perangkat yang tersambung ke tailnet yang sama.

## 4. Gunakan Linkdrop dari ponsel

1. Buka aplikasi Tailscale.
2. Pastikan statusnya **Connected**.
3. Buka alamat HTTPS dari `tailscale serve status`.
4. Tempel link media publik yang boleh diunduh, lalu pilih format.
5. Tunggu pemrosesan dan transfer mencapai 100%.
6. Pada dialog simpan, gunakan nama default atau ubah namanya; Linkdrop tetap mempertahankan ekstensi asli. Tekan **Simpan sekarang**. Pada browser ponsel, pilih **Simpan ke File** atau tindakan sejenis melalui menu bagikan.

Ponsel tidak harus berada pada Wi-Fi yang sama dengan komputer host. Data seluler dapat digunakan selama kedua perangkat terhubung ke tailnet yang sama.

## Jika membutuhkan server yang selalu aktif

Gunakan VPS atau host container persisten bila komputer pribadi tidak dapat dibiarkan menyala. VPS berbiaya dan harus dikelola seperti server: gunakan `DOWNLOAD_TOKEN_SECRET` yang unik, ruang disk sementara yang cukup, HTTPS, rate limiting, serta pembaruan sistem. Tailscale tetap dapat dipakai pada VPS untuk membatasi akses pribadi tanpa membuka Linkdrop ke internet publik. Baca [Panduan Deployment](DEPLOYMENT.md) sebelum memilih opsi ini.

## Penggunaan harian

Tailscale Serve menyimpan konfigurasi proxy, tetapi server Linkdrop tetap harus dijalankan. Setelah komputer direstart:

```bash
cd /lokasi/Linkdrop
source .venv/bin/activate
PORT=5050 gunicorn --bind 127.0.0.1:5050 --workers 1 --threads 4 --timeout 0 app:app
```

Pastikan aplikasi Tailscale tersambung. Cegah komputer host masuk ke mode sleep selama download panjang atau konversi video.

## Hentikan akses privat

Hentikan Linkdrop dengan `Ctrl+C`. Untuk menghapus konfigurasi Tailscale Serve, jalankan:

```bash
tailscale serve reset
```

Periksa hasilnya dengan:

```bash
tailscale serve status
```

## Catatan keamanan

- `tailscale serve` hanya dapat diakses dari tailnet. Jangan menggantinya dengan `tailscale funnel` kecuali Anda memang ingin membuat layanan publik.
- Gunakan bind `127.0.0.1` agar Linkdrop tidak terbuka langsung ke jaringan lokal.
- Gunakan `DOWNLOAD_TOKEN_SECRET` acak dan jangan pernah memasukkannya ke Git.
- Simpan cookie, `.env`, dan sesi login di luar repository.
- Unduh hanya media milik sendiri atau media yang boleh Anda unduh.

## Troubleshooting

### Alamat HTTPS tidak dapat dibuka

Periksa setiap lapisan secara berurutan:

```bash
curl http://127.0.0.1:5050/api/health
tailscale status
tailscale serve status
```

Pastikan Linkdrop masih berjalan, Tailscale tersambung pada kedua perangkat, keduanya berada dalam tailnet yang sama, dan komputer host tidak sleep.

### `ERR_ADDRESS_UNREACHABLE`

Biasanya perangkat pengguna belum tersambung ke Tailscale, masuk ke tailnet yang berbeda, atau membuka alamat LAN seperti `10.x.x.x`. Sambungkan perangkat ke Tailscale dan gunakan alamat `https://...ts.net` dari `tailscale serve status`.

### Halaman terbuka tetapi download berhenti

Biarkan halaman browser tetap terbuka dan cegah komputer host masuk ke mode sleep. Sumber VP9 atau AV1 beresolusi tinggi memerlukan konversi kompatibilitas H.264, sehingga progres dapat berada pada tahap pemrosesan selama beberapa waktu.

### Video lama tidak dapat diputar

File yang diunduh sebelum perbaikan kompatibilitas mungkin masih berisi VP9 di dalam container MP4. Unduh kembali media tersebut agar Linkdrop dapat memvalidasi dan, bila diperlukan, mengubahnya menjadi H.264/AAC.
