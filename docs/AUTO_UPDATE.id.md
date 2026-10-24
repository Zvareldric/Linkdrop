# Pembaruan Otomatis

[English](AUTO_UPDATE.md) · **Bahasa Indonesia**

Linkdrop di komputer Anda dapat memperbarui dirinya sendiri setiap kali repository GitHub berubah. Tailscale Serve dan Cloudflare Tunnel tetap meneruskan ke port lokal yang sama, sehingga alamat privat atau publik Anda menampilkan versi baru tanpa perubahan lain.

## Cara kerja

1. Setiap perubahan di `main` yang lulus tes akan diterbitkan GitHub Actions sebagai image Docker ke `ghcr.io/zvareldric/linkdrop:latest`. Image juga dibangun ulang setiap Senin, sehingga yt-dlp tetap terbaru walaupun kodenya tidak berubah.
2. Skrip update di komputer Anda berjalan setiap 15 menit. Skrip mengambil image tersebut dan, hanya jika ada versi yang lebih baru, mengganti container `linkdrop` dengan pengaturan dan port yang sama.
3. Jika versi baru tidak menjawab health check dalam satu menit, skrip menjalankan kembali versi sebelumnya dan melewati image tersebut sampai ada versi baru lagi.

Versi baru biasanya sampai di komputer Anda sekitar 30 menit setelah di-merge: sekitar 10–15 menit untuk membangun image, ditambah paling lama 15 menit sampai pengecekan terjadwal berikutnya.

## Kebutuhan

- Docker Desktop (Windows, macOS) atau Docker Engine (Linux) yang berjalan selama Anda login
- Clone repository ini, karena skrip update ada di dalamnya

Jika saat ini Linkdrop berjalan native dengan `gunicorn`, hentikan dulu (tekan Ctrl+C di terminalnya). Skrip update lalu menjalankan Linkdrop di Docker pada port yang sama, `5050`, sehingga `tailscale serve` tetap berfungsi.

## 1. Jadikan image publik (pemilik repository, sekali saja)

Penerbitan pertama membuat package `linkdrop` di profil GitHub pemilik repository. Jika `docker pull ghcr.io/zvareldric/linkdrop:latest` menampilkan `denied` atau `unauthorized`, buka package tersebut di GitHub (profil → **Packages** → **linkdrop**), pilih **Package settings** → **Change visibility**, lalu ubah menjadi **Public**.

## 2. Jalankan skrip update sekali

Perbarui clone Anda dulu supaya skripnya tersedia.

Windows (PowerShell, di folder `Linkdrop`):

```powershell
git pull
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\update-linkdrop.ps1
```

macOS dan Linux:

```bash
git pull
./scripts/update-linkdrop.sh
```

Pada run pertama, skrip menyalin pengaturan container `linkdrop` yang sudah ada (token secret, kata sandi, batas ukuran, port, dan sejenisnya) ke `~/.linkdrop/linkdrop.env` (`%USERPROFILE%\.linkdrop\linkdrop.env` di Windows). Jika belum ada container, skrip membuat file itu dengan token secret baru dan port `5050`. File ini berisi rahasia, jadi jangan dibagikan.

Skrip menampilkan `Linkdrop diperbarui ke versi …` saat memasang versi baru dan `Linkdrop sudah versi terbaru.` jika tidak ada perubahan.

## 3. Jadwalkan setiap 15 menit

Windows (PowerShell, di folder `Linkdrop`):

```powershell
$script = (Resolve-Path .\scripts\update-linkdrop.ps1).Path
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "Linkdrop auto-update" -Action $action -Trigger $trigger `
  -Description "Memperbarui Linkdrop dari ghcr.io"
```

Tugas ini berjalan selama Anda login, sama seperti Docker Desktop. Jendela PowerShell mungkin muncul sekilas setiap kali tugas berjalan. Pesan dicatat di `%USERPROFILE%\.linkdrop\update.log`.

macOS dan Linux: jalankan `crontab -e` lalu tambahkan baris ini, dengan path diganti ke clone Anda:

```text
*/15 * * * * /path/ke/Linkdrop/scripts/update-linkdrop.sh >> "$HOME/.linkdrop/update.log" 2>&1
```

## Cek versi yang sedang berjalan

Buka `/api/health` di alamat Linkdrop Anda, misalnya `https://linkdrop.tailnet-anda.ts.net/api/health`. Field `version` menunjukkan commit Git yang dipakai untuk membangun image; nilainya sama dengan commit terbaru di `main` setelah update sampai. Image hasil build lokal menampilkan `dev`.

## Mengubah pengaturan

Edit `~/.linkdrop/linkdrop.env` (misalnya `ACCESS_PASSWORD`), lalu hapus container dan jalankan skrip sekali agar Linkdrop dijalankan ulang dengan pengaturan baru:

```bash
docker rm --force linkdrop
./scripts/update-linkdrop.sh
```

Di Windows, jalankan skrip `.ps1`. `LINKDROP_PORT` di file yang sama menentukan port lokal.

## Mematikan pembaruan otomatis

- Windows: `Unregister-ScheduledTask -TaskName "Linkdrop auto-update" -Confirm:$false`
- macOS dan Linux: hapus barisnya lewat `crontab -e`

Container yang sedang berjalan tetap bekerja; hanya pembaruannya yang berhenti.

## Catatan

- GitHub menjeda workflow terjadwal di repository yang tidak aktif selama 60 hari. Merge tetap menerbitkan image, dan **Actions → CI → Run workflow** dapat menerbitkan image baru kapan saja.
- Image dibangun untuk `linux/amd64` dan `linux/arm64`, sehingga berjalan di PC Intel/AMD maupun Mac Apple Silicon.
- Jika komputer sleep, pengecekan berikutnya berjalan setelah komputer menyala kembali.
