# Tooling dan keputusan arsitektur

Setiap alat pengembangan dipakai sesuai fungsi aslinya tanpa menambah dependency runtime yang tidak diperlukan.

## Playwright

Playwright menjadi test runner end-to-end. Matriks lokal mencakup Chrome desktop, Pixel 7, dan iPhone 13/WebKit. Pengujian memeriksa overflow horizontal, ukuran target sentuh, alur analisis, protokol progres, dan hasil download.

```bash
npm install
npx playwright install chromium webkit
npm run test:e2e
```

## Pedoman frontend

Antarmuka mengikuti prinsip hierarki visual yang jelas, copy yang langsung, visible focus, reduced motion, target sentuh minimal, dan layout responsif. Seluruh implementasi UI disimpan langsung di repository ini.

## Strix

Strix adalah alat penetration testing, bukan paket aplikasi. Jalankan terhadap repository atau deployment staging hanya setelah Docker dan provider LLM dikonfigurasi oleh operator:

```bash
strix --target .
```

Jangan menjalankan autonomous pentest terhadap domain yang tidak dimiliki atau tanpa izin tertulis.

## Referensi dokumentasi

Dokumentasi library terbaru diperiksa selama development untuk mengurangi ketergantungan pada API usang. Alat referensi tersebut tidak dikirim ke browser dan tidak diperlukan saat Linkdrop berjalan.

## Supabase plugin

Linkdrop saat ini sengaja stateless: tidak ada akun, riwayat server, atau database. Supabase baru relevan pada fase akun, rate limiting persisten, job queue, atau object storage. Menambahkannya sekarang akan memperluas data pengguna dan beban operasional tanpa menyelesaikan kebutuhan utama aplikasi.
