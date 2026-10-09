# Tooling dan keputusan arsitektur

Repo eksternal yang dipilih pengguna dipakai sesuai fungsi aslinya, tanpa menambah dependency runtime yang tidak dibutuhkan.

## Playwright

Playwright menjadi test runner end-to-end. Matriks lokal mencakup Chrome desktop, Pixel 7, dan iPhone 13/WebKit. Pengujian memeriksa overflow horizontal, ukuran target sentuh, alur analisis, protokol progres, dan hasil download.

```bash
npm install
npx playwright install chromium webkit
npm run test:e2e
```

## Anthropic frontend-design

Panduan dipakai untuk merancang identitas visual yang spesifik terhadap proses transfer media, hierarki tipografi, copy yang langsung, visible focus, reduced motion, dan layout responsif. Aplikasi tidak menyalin aset atau kode dari repo tersebut.

## Strix

Strix adalah alat penetration testing, bukan paket aplikasi. Jalankan terhadap repository atau deployment staging hanya setelah Docker dan provider LLM dikonfigurasi oleh operator:

```bash
strix --target .
```

Jangan menjalankan autonomous pentest terhadap domain yang tidak dimiliki atau tanpa izin tertulis.

## Context7

Context7 dipakai pada tahap development untuk mengambil dokumentasi library terbaru. Ia tidak dikirim ke browser dan tidak diperlukan agar Linkdrop berjalan.

## Supabase plugin

Repo `supabase-community/supabase-plugin` berisi skill dan panduan coding-agent. Linkdrop saat ini sengaja stateless: tidak ada akun, riwayat server, atau database. Supabase baru relevan pada fase akun, rate limiting persisten, job queue, atau object storage. Menambahkannya sekarang akan menambah data pengguna dan operational surface tanpa menyelesaikan kebutuhan mobile.
