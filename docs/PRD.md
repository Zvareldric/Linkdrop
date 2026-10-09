# PRD — Linkdrop Media Downloader

- **Status:** Implementasi v1.1
- **Tanggal:** 9 Oktober 2026
- **Pemilik:** Product & Engineering
- **Target platform:** Web responsif, deployment utama Vercel

## 1. Ringkasan

Linkdrop adalah web app untuk menganalisis link konten publik dan mengunduh foto, video, audio, atau carousel dari YouTube, Instagram, TikTok, X/Twitter, Facebook, dan situs lain yang didukung `yt-dlp`. Pengguna dapat melihat pilihan kualitas yang benar-benar tersedia dari sumber, dari kualitas terendah hingga tertinggi, lalu memilih format keluaran yang sesuai.

Versi lama memakai thread latar belakang, state job dalam memori, dan penyimpanan lokal. Pola itu bekerja terbatas di komputer lokal, tetapi tidak andal pada Vercel karena request yang berbeda dapat masuk ke instance berbeda, proses latar belakang dapat dihentikan setelah respons, dan filesystem instance tidak persisten. V1 menggantinya dengan proses stateless: analisis menghasilkan link unduhan bertanda tangan, lalu satu invocation menyiapkan dan men-stream file ke browser.

## 2. Problem Statement

Pengguna sering ingin menyimpan salinan konten publik milik sendiri atau konten yang telah diberi izin, tetapi platform sosial menyediakan format dan kualitas yang tidak konsisten. Tool yang ada sering menampilkan kualitas palsu, gagal tanpa pesan yang jelas, atau tidak cocok dengan hosting serverless sehingga berhenti setelah deployment.

Tanpa solusi ini, pengguna harus memakai beberapa layanan berbeda, berhadapan dengan iklan berisiko, atau menjalankan perintah teknis sendiri. Produk harus menyederhanakan alur tersebut tanpa menjanjikan kemampuan yang diblokir oleh platform sumber.

## 3. Target Users

1. **Kreator konten** yang ingin mengarsipkan konten publik milik sendiri.
2. **Social media manager** yang memiliki izin untuk menyimpan aset kampanye.
3. **Pengguna umum nonteknis** yang perlu menyimpan media publik secara sesekali.
4. **Developer/operator** yang ingin men-deploy instance pribadi dengan konfigurasi sendiri.

## 4. Goals

1. Minimal 85% sesi analisis link publik yang didukung menghasilkan daftar format atau pesan kegagalan yang dapat ditindaklanjuti.
2. Minimal 80% unduhan yang dimulai dan berada di bawah batas ukuran/waktu server selesai tanpa error aplikasi.
3. Semua pilihan kualitas video ditampilkan berurutan dari terendah ke tertinggi dan sesuai metadata sumber.
4. Tidak ada job yang bergantung pada memory state, background thread, atau filesystem persisten.
5. Deployment Vercel dapat dijalankan dari repository dengan FFmpeg tersedia melalui `Dockerfile.vercel`.

## 5. Non-Goals

1. **Membuka konten privat atau paywalled.** V1 hanya memproses konten yang pengguna berhak akses dan server dapat ambil secara sah.
2. **Menembus DRM, CAPTCHA, atau anti-bot.** Sistem hanya menggunakan kemampuan extractor yang tersedia; kegagalan platform dijelaskan secara jujur.
3. **Download playlist besar.** V1 fokus pada satu post/video dan carousel maksimal 20 item agar sesuai batas compute.
4. **Penyimpanan cloud permanen.** File bersifat sementara dan langsung dikirim ke pengguna.
5. **Jaminan semua situs selalu kompatibel.** Extractor platform dapat berubah sewaktu-waktu.

## 6. User Stories

### Pengguna

- Sebagai pengguna, saya ingin menempel link agar aplikasi otomatis membaca judul, thumbnail, platform, dan format yang tersedia.
- Sebagai pengguna, saya ingin melihat kualitas dari terendah ke tertinggi agar dapat menyeimbangkan kualitas dan ukuran.
- Sebagai pengguna, saya ingin mengunduh video MP4 dengan audio agar dapat diputar di perangkat umum.
- Sebagai pengguna, saya ingin memilih audio asli atau MP3 128/192/320 kbps agar sesuai kebutuhan.
- Sebagai pengguna Instagram, saya ingin mengunduh foto tunggal atau carousel dalam kualitas sumber agar aset tidak terkompres ulang oleh aplikasi.
- Sebagai pengguna, saya ingin pesan error dalam bahasa Indonesia agar tahu apakah link salah, konten privat, server timeout, atau platform memblokir akses.

### Operator

- Sebagai operator, saya ingin health endpoint agar dapat memeriksa runtime, FFmpeg, dan batas file.
- Sebagai operator, saya ingin rahasia token serta konfigurasi cookie berada di environment variable.
- Sebagai operator, saya ingin file sementara terhapus setelah respons agar ruang disk tidak bocor.

## 7. Requirements

### P0 — Must Have

#### P0.1 Analisis link

- Menerima URL HTTP/HTTPS publik.
- Menolak localhost, IP privat, credential di URL, dan port nonstandar untuk mengurangi risiko SSRF.
- Mengembalikan judul, uploader, durasi, thumbnail, platform, jumlah item, dan pilihan format.

**Acceptance criteria**

- Given URL publik yang didukung, when pengguna menekan “Analisis link”, then metadata dan minimal satu opsi unduhan tampil.
- Given URL privat/lokal, when dianalisis, then server menolak dengan HTTP 422 dan pesan yang aman.
- Given extractor gagal, then UI menampilkan pesan tanpa traceback atau secret server.

#### P0.2 Pilihan kualitas video

- Menampilkan semua resolusi unik yang dilaporkan extractor secara ascending.
- Mengunduh video maksimal pada tinggi yang dipilih, menggabungkan audio terbaik, dan menghasilkan MP4 bila FFmpeg diperlukan.
- Tidak mengklaim resolusi lebih tinggi daripada format sumber.

**Acceptance criteria**

- Given sumber memiliki 360p, 720p, dan 1080p, then UI menampilkan urutan 360p → 720p → 1080p.
- Given 720p dipilih, then output tidak melebihi 720p.
- Given format video/audio terpisah, then output berisi video dan audio setelah merge.

#### P0.3 Audio

- Mendukung audio sumber terbaik.
- Mendukung output MP3 128, 192, dan 320 kbps.
- UI menjelaskan bahwa transcode 320 kbps tidak menciptakan detail melebihi sumber.

#### P0.4 Foto dan carousel

- Mengambil file gambar dengan resolusi terbaik yang dilaporkan extractor.
- Foto tunggal dikirim sebagai file asli.
- Carousel dikemas sebagai ZIP tanpa recompression media.
- Maksimal 20 item per post pada v1.

#### P0.5 Arsitektur Vercel-compatible

- Tidak menggunakan thread daemon, global in-memory job store, atau polling status.
- Setiap unduhan selesai dalam satu request dan dikirim sebagai streaming response.
- Menggunakan `/tmp`/working directory sementara dengan cleanup setelah streaming.
- Menyertakan `Dockerfile.vercel` berisi Python, Gunicorn, dan FFmpeg.
- Ukuran media default maksimal 220 MB agar source video, source audio, dan output merge dapat berada bersamaan dalam kuota `/tmp`.

#### P0.6 Security & compliance

- Token unduhan ditandatangani dan kedaluwarsa dalam 15 menit.
- Header respons mencegah caching file dan MIME sniffing.
- UI menampilkan peringatan hak cipta dan hanya mendukung konten publik.
- Cookies/secret tidak pernah dikirim ke client atau log.

### P1 — Nice to Have

1. Estimasi ukuran yang lebih akurat sebelum unduh.
2. Rate limiting per IP menggunakan storage eksternal.
3. Riwayat lokal di browser, bukan server.
4. Pilihan container WebM/MKV selain MP4.
5. Monitoring terstruktur untuk error per extractor.
6. Upload hasil ke object storage untuk file besar dan signed URL.

### P0.7 Pengalaman ponsel

- Layout tidak menimbulkan overflow horizontal pada viewport ponsel modern.
- Semua tombol utama dan pilihan kualitas memiliki target sentuh minimal 44 px.
- Input URL memakai ukuran teks 16 px agar Safari iOS tidak melakukan zoom otomatis.
- Safe area, reduced motion, keyboard focus, dan label screen reader didukung.
- Browser yang mendukung OPFS menulis stream hasil ke penyimpanan sementara perangkat untuk menekan penggunaan RAM.
- Download dapat dibatalkan oleh pengguna.

**Acceptance criteria**

- Matriks Playwright lulus pada Chrome desktop, emulasi Pixel 7, dan iPhone 13/WebKit.
- Tidak ada horizontal overflow pada viewport ponsel.
- Progres mencapai 100%, file mendapat nama yang benar, dan seluruh kontrol kembali aktif setelah selesai atau dibatalkan.

### P2 — Future

1. Queue dan worker terpisah untuk file >220 MB atau proses >5 menit.
2. Akun pengguna, kuota, dan dashboard riwayat.
3. Batch download dan playlist dengan persetujuan eksplisit.
4. Aplikasi mobile/PWA dan share extension.
5. Penyimpanan privat terenkripsi dengan auto-expiry.

## 8. UX Flow

1. Pengguna membuka landing page.
2. Pengguna menempel URL dan memilih “Analisis link”.
3. Sistem memvalidasi URL dan mengambil metadata.
4. UI menampilkan preview serta tab Video, Audio, atau Foto yang relevan.
5. Pengguna memilih kualitas/format.
6. Browser membuka endpoint unduhan bertanda tangan.
7. Server menyiapkan file, men-stream ke browser, lalu menghapus file sementara.
8. Jika gagal, endpoint mengembalikan pesan error yang dapat ditindaklanjuti.

## 9. Technical Architecture

```text
Browser
  ├─ POST /api/info ──> Flask + yt-dlp metadata
  │                       └─ signed download choices
  └─ GET /api/download/<token>
                          ├─ validate token + URL
                          ├─ yt-dlp download
                          ├─ FFmpeg merge/transcode (bila perlu)
                          ├─ stream response
                          └─ delete temporary files

Vercel Container Function
  ├─ Python 3.12 + Gunicorn
  ├─ yt-dlp
  ├─ FFmpeg
  └─ /tmp working space (ephemeral)
```

### Constraints

- Vercel Function Hobby memiliki durasi maksimum sekitar 5 menit.
- Filesystem hanya writable pada scratch space `/tmp`, sekitar 500 MB.
- Non-streaming function payload dibatasi 4.5 MB; respons file harus streaming.
- Platform sumber dapat mewajibkan login, cookies, PO Token, CAPTCHA, atau memblokir IP data center.
- Cookie akun pemilik tidak boleh dipakai pada instance publik tanpa threat model dan isolasi tambahan.

## 10. Success Metrics

### Leading indicators

- Analysis success rate ≥85% untuk URL publik yang masuk daftar uji.
- Download completion rate ≥80% untuk file <200 MB.
- Application-originated 5xx rate <2%.
- Median metadata response <8 detik.
- 100% file sementara terhapus setelah respons selesai atau error.

### Lagging indicators

- ≥30% pengguna yang berhasil menganalisis link memulai unduhan.
- Repeat usage 30 hari ≥20% untuk instance publik.
- Keluhan “kualitas tidak sesuai pilihan” <3% dari unduhan selesai.

Pengukuran dilakukan melalui Vercel Observability tanpa menyimpan URL lengkap; log hanya platform, kelas error, durasi, dan ukuran.

## 11. Risks and Mitigations

| Risiko | Dampak | Mitigasi |
|---|---|---|
| YouTube/IG memblokir IP server | Analisis/download gagal | Pesan jujur, update yt-dlp, opsi PO Token/cookie server yang aman |
| Timeout/ruang Vercel | File besar gagal | Batas 220 MB, satu-item v1, future external worker/object storage |
| Penyalahgunaan bandwidth | Biaya tinggi | Signed token pendek, WAF/rate limit P1, batas ukuran |
| SSRF | Akses jaringan internal | Validasi skema, DNS/IP publik, port allowlist |
| Pelanggaran hak cipta | Risiko legal | Hanya konten publik, notice penggunaan berizin, tanpa DRM bypass |
| Cookie server bocor/disalahgunakan | Pengambilalihan akun | Secret env, jangan gunakan akun utama, nonaktif secara default |

## 12. Rollout Plan

### Phase 1 — MVP

- Stateless analyze/download.
- Video MP4, source audio/MP3, photo/carousel.
- Docker deployment di Vercel.
- Security baseline, health check, unit tests.

### Phase 1.1 — Stabilization

- Uji matriks platform dan format.
- Observability dan error taxonomy.
- Rate limiting.

### Phase 2 — Large media

- Queue + dedicated worker atau Vercel Workflow.
- Object storage dan signed URL.
- Progress status persisten.

## 13. Open Questions

1. **[Legal — blocking sebelum publik luas]** Apakah instance hanya untuk penggunaan pribadi atau tersedia untuk publik?
2. **[Product]** Apakah batas 220 MB cukup untuk target pengguna?
3. **[Engineering]** Perlukah worker eksternal untuk video panjang/4K sejak peluncuran?
4. **[Security]** Apakah autentikasi pengguna dan rate limiting wajib sebelum domain dibagikan?
5. **[Business]** Siapa yang menanggung bandwidth dan compute jika trafik meningkat?

## 14. Definition of Done

- Unit test lulus.
- Health endpoint melaporkan FFmpeg aktif di image deployment.
- Uji manual minimal satu URL publik dari YouTube, Instagram, TikTok, dan X.
- Kualitas video rendah dan tinggi menghasilkan file yang dapat diputar dengan audio.
- MP3 dapat diputar dan carousel ZIP dapat dibuka.
- Tidak ada file sisa setelah request sukses/gagal.
- README deployment dan environment variables lengkap.
