# Akses Publik dengan Cloudflare Tunnel

[English](CLOUDFLARE_TUNNEL.md) · **Bahasa Indonesia**

Cloudflare Tunnel memublikasikan Linkdrop dari komputer Anda sendiri pada alamat HTTPS publik. Anda tidak memerlukan VPS, IP statis, atau membuka port router. Konektor `cloudflared` membuka koneksi keluar ke Cloudflare, lalu Cloudflare meneruskan pengunjung melalui koneksi itu ke Linkdrop di `localhost`.

Menjalankan Linkdrop di rumah juga punya keuntungan lain: platform seperti YouTube jauh lebih sering memblokir IP data center dibanding koneksi rumahan. Konsekuensinya, Linkdrop hanya dapat diakses selama komputer menyala dan tidak sleep.

> **Sebelum dibuka ke publik.** Siapa pun yang mengetahui alamatnya dapat memakai bandwidth, CPU, dan disk Anda. Selalu atur `ACCESS_PASSWORD`, bagikan hanya kepada orang yang Anda percaya, dan gunakan Linkdrop hanya untuk media milik sendiri atau yang sudah Anda peroleh izinnya. Ketentuan layanan Cloudflare membatasi sebagian penggunaan jaringannya untuk menyajikan video dan file besar, jadi baca ketentuan terbaru sebelum membuka Linkdrop untuk banyak orang.

## Kebutuhan

- Linkdrop berjalan dengan Docker (lihat [Instalasi](INSTALLATION.id.md)) atau secara native
- [Akun Cloudflare](https://dash.cloudflare.com/sign-up) gratis
- Untuk alamat permanen: domain yang DNS-nya dikelola Cloudflare. Quick Tunnel (langkah 3) dapat dipakai tanpa domain, khusus untuk uji coba.

## 1. Jalankan Linkdrop dengan proteksi akses

Ikat Linkdrop ke `127.0.0.1` supaya hanya `cloudflared` di komputer yang sama yang dapat menjangkaunya.

Windows PowerShell:

```powershell
$bytes = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$secret = -join ($bytes | ForEach-Object { $_.ToString("x2") })
docker rm --force linkdrop 2>$null
docker run --detach --name linkdrop --restart unless-stopped `
  --publish 127.0.0.1:5050:8080 `
  --env "DOWNLOAD_TOKEN_SECRET=$secret" `
  --env "ACCESS_PASSWORD=ganti-dengan-kata-sandi-panjang" `
  --env TRUSTED_PROXY_HEADER=CF-Connecting-IP `
  --env MAX_MEDIA_BYTES=2147483648 `
  linkdrop
```

macOS dan Linux:

```bash
docker rm --force linkdrop 2>/dev/null
docker run --detach --name linkdrop --restart unless-stopped \
  --publish 127.0.0.1:5050:8080 \
  --env DOWNLOAD_TOKEN_SECRET="$(openssl rand -hex 32)" \
  --env ACCESS_PASSWORD="ganti-dengan-kata-sandi-panjang" \
  --env TRUSTED_PROXY_HEADER=CF-Connecting-IP \
  --env MAX_MEDIA_BYTES=2147483648 \
  linkdrop
```

- `ACCESS_PASSWORD` mengaktifkan dialog masuk di browser. Nama penggunanya `linkdrop`, kecuali Anda mengatur `ACCESS_USERNAME`.
- `TRUSTED_PROXY_HEADER=CF-Connecting-IP` membuat rate limiter melihat alamat asli setiap pengunjung, bukan alamat lokal `cloudflared`. Atur ini hanya jika Linkdrop semata-mata dapat dijangkau melalui Cloudflare seperti di atas; jika tidak, pengunjung dapat memalsukan header tersebut.

Buka [http://127.0.0.1:5050](http://127.0.0.1:5050) dan pastikan browser meminta nama pengguna dan kata sandi.

## 2. Pasang cloudflared

Windows (PowerShell):

```powershell
winget install --id Cloudflare.cloudflared
```

macOS:

```bash
brew install cloudflared
```

Linux: ikuti petunjuk paket di [halaman unduhan cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) milik Cloudflare.

## 3. Coba Quick Tunnel (opsional, tanpa domain)

```bash
cloudflared tunnel --url http://localhost:5050
```

`cloudflared` menampilkan alamat acak `https://….trycloudflare.com`. Alamat ini berubah setiap kali perintah dijalankan dan berhenti bekerja saat terminal ditutup, jadi gunakan hanya untuk menguji setup.

## 4. Buat tunnel permanen

1. Di [dashboard Cloudflare](https://dash.cloudflare.com/), buka **Zero Trust**, lalu halaman **Tunnels** di bagian jaringan. Cloudflare sesekali mengganti nama menu ini; dokumentasi tunnel Cloudflare selalu menautkan lokasi terbarunya.
2. Buat tunnel bertipe **Cloudflared** dengan nama `linkdrop`.
3. Pilih sistem operasi Anda. Cloudflare menampilkan perintah berbentuk `cloudflared service install <token>`. Jalankan di PowerShell administrator (Windows) atau dengan `sudo` (macOS/Linux). Konektor dipasang sebagai service dan ikut menyala bersama komputer.
4. Tambahkan **public hostname**:
   - Subdomain: `linkdrop`, domain: domain Anda
   - Tipe service: `HTTP`, URL: `localhost:5050`
5. Simpan. Setelah beberapa detik, `https://linkdrop.domain-anda` akan menampilkan Linkdrop.

Token pada langkah 3 memberi kendali atas tunnel. Perlakukan seperti kata sandi dan jangan pernah memasukkannya ke Git.

## 5. Verifikasi

- `https://linkdrop.domain-anda/api/health` mengembalikan `"ok": true` tanpa kata sandi.
- `https://linkdrop.domain-anda/` meminta nama pengguna dan kata sandi.
- Analisis satu link yang boleh Anda unduh dan selesaikan unduhannya.

## Login lebih kuat dengan Cloudflare Access (opsional)

Daripada membagikan satu kata sandi, Anda dapat memasang aplikasi Cloudflare Access di depan hostname sehingga pengunjung masuk memakai kode sekali pakai lewat email atau penyedia identitas. Cloudflare Access memiliki tier gratis untuk tim kecil. Tetap atur `ACCESS_PASSWORD` sebagai lapisan kedua.

## Catatan operasional

- **Jaga komputer tetap menyala.** Matikan sleep selama Linkdrop harus online. `--restart unless-stopped` milik Docker dan service `cloudflared` akan menyalakan semuanya kembali setelah reboot.
- **Bandwidth.** Setiap unduhan mengalir dari sumber ke komputer Anda, lalu dari komputer Anda ke pengunjung. Kecepatan upload koneksi Anda menentukan seberapa cepat pengunjung menerima file.
- **Unduhan panjang.** Cloudflare menutup request yang origin-nya tidak mengirim apa pun selama sekitar 100 detik. Antarmuka Linkdrop memakai progress stream yang langsung mengirim status, sehingga unduhan dan konversi yang lama tetap tersambung.
- **Rate limit.** Secara default, setiap pengunjung dapat menganalisis 30 link per menit dan memulai 20 unduhan per 10 menit. Ubah dengan `RATE_LIMIT_INFO` dan `RATE_LIMIT_DOWNLOAD` (`jumlah/detik`, atau `off`). Sepuluh kata sandi salah memblokir alamat tersebut selama 10 menit.
- **Proxy keluar.** Linkdrop memblokir request media yang mengarah ke alamat jaringan privat atau lokal. Jika Anda mengonfigurasi proxy HTTP keluar, proxy itulah yang me-resolve hostname, jadi proxy harus menerapkan pembatasan yang sama.

## Troubleshooting

| Gejala | Solusi |
|---|---|
| `502 Bad Gateway` atau error 1033 | Linkdrop tidak berjalan atau tunnel mengarah ke port yang salah. Periksa `docker ps` dan pastikan URL service adalah `localhost:5050`. |
| Dialog kata sandi terus muncul | Nama pengguna atau kata sandi salah. Nama pengguna default adalah `linkdrop`. |
| `429 Too Many Requests` | Rate limit atau kunci gagal-masuk tercapai. Tunggu sesuai waktu pada pesan. |
| Semua pengunjung berbagi satu rate limit | `TRUSTED_PROXY_HEADER=CF-Connecting-IP` belum diatur. |
| YouTube meminta konfirmasi bukan bot | Platform membatasi IP Anda. Coba lagi nanti atau konfigurasi cookies seperti dijelaskan di [Deployment](DEPLOYMENT.md#cookies). |
