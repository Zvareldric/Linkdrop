# Keep the local Linkdrop container on the newest published image.
#
# Safe to run repeatedly (for example every 15 minutes from Task Scheduler).
# It pulls the image, and only when a newer one exists replaces the container
# while keeping the same settings and port. If the new version fails its
# health check, the previous version is started again.
#
# Settings live in %USERPROFILE%\.linkdrop\linkdrop.env. On the first run they
# are copied from an existing "linkdrop" container, or a new token secret is
# generated. Compatible with Windows PowerShell 5.1 and PowerShell 7.

$ErrorActionPreference = "Stop"

function Get-Setting([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

$homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$Image = Get-Setting "LINKDROP_IMAGE" "ghcr.io/zvareldric/linkdrop:latest"
$Name = Get-Setting "LINKDROP_CONTAINER" "linkdrop"
$ConfigDir = Get-Setting "LINKDROP_CONFIG_DIR" (Join-Path $homeDir ".linkdrop")
$EnvFile = Join-Path $ConfigDir "linkdrop.env"
$LockDir = Join-Path $ConfigDir "update.lock"
$FailedFile = Join-Path $ConfigDir "failed-image"
# Task Scheduler keeps no output, so every message is also appended here.
$LogFile = Join-Path $ConfigDir "update.log"
$KeepKeys = '^(DOWNLOAD_TOKEN_SECRET|DOWNLOAD_TOKEN_MAX_AGE|MAX_MEDIA_BYTES|HTTP_TIMEOUT|FFMPEG_TIMEOUT|ACCESS_USERNAME|ACCESS_PASSWORD|TRUSTED_PROXY_HEADER|RATE_LIMIT_INFO|RATE_LIMIT_DOWNLOAD|GALLERY_CONCURRENCY|BATCH_CONCURRENCY|YTDLP_[A-Z0-9_]+|LINKDROP_PORT)='

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Output $line
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

# Native commands do not stop the script on failure; check exit codes instead.
function Invoke-Docker {
    # Windows PowerShell 5.1 turns native stderr into terminating errors under
    # "Stop", so relax it while Docker runs and rely on the exit code.
    $ErrorActionPreference = "Continue"
    $output = & docker @args 2>&1
    return @{ Ok = ($LASTEXITCODE -eq 0); Output = (($output | ForEach-Object { "$_" }) -join "`n").Trim() }
}

function Test-Container { return (Invoke-Docker container inspect $Name).Ok }

function Get-EnvValue([string]$Key) {
    $line = Get-Content $EnvFile | Where-Object { $_ -like "$Key=*" } | Select-Object -Last 1
    if ($line) { return $line.Substring($Key.Length + 1) }
    return $null
}

function Add-EnvLine([string]$Line) {
    # UTF-8 without BOM: Docker would read a BOM as part of the first key.
    [System.IO.File]::AppendAllText($EnvFile, "$Line`n", (New-Object System.Text.UTF8Encoding($false)))
}

function New-Secret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $generator.GetBytes($bytes)
    $generator.Dispose()
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

# A lock left behind by a run that was killed (sleep, power loss) expires.
if ((Test-Path $LockDir) -and ((Get-Item $LockDir).CreationTime -lt (Get-Date).AddMinutes(-60))) {
    Remove-Item $LockDir -Force
}
try {
    New-Item -ItemType Directory -Path $LockDir -ErrorAction Stop | Out-Null
} catch {
    Write-Log "Update lain masih berjalan; lewati."
    exit 0
}

try {
    if (-not (Test-Path $EnvFile)) {
        New-Item -ItemType File -Path $EnvFile | Out-Null
        if (Test-Container) {
            $envLines = (Invoke-Docker container inspect --format '{{range .Config.Env}}{{println .}}{{end}}' $Name).Output -split "`r?`n"
            $envLines | Where-Object { $_ -match $KeepKeys } | ForEach-Object { Add-EnvLine $_ }
            $port = (Invoke-Docker container inspect --format '{{with index .HostConfig.PortBindings "8080/tcp"}}{{(index . 0).HostPort}}{{end}}' $Name).Output
            if ($port -and -not (Get-EnvValue "LINKDROP_PORT")) { Add-EnvLine "LINKDROP_PORT=$port" }
            Write-Log "Pengaturan container lama disalin ke $EnvFile."
        }
        if (-not (Get-EnvValue "DOWNLOAD_TOKEN_SECRET")) { Add-EnvLine ("DOWNLOAD_TOKEN_SECRET=" + (New-Secret)) }
        if (-not (Get-EnvValue "MAX_MEDIA_BYTES")) { Add-EnvLine "MAX_MEDIA_BYTES=2147483648" }
        if (-not (Get-EnvValue "LINKDROP_PORT")) { Add-EnvLine "LINKDROP_PORT=5050" }
    }

    $Port = Get-Setting "LINKDROP_PORT" (Get-EnvValue "LINKDROP_PORT")
    if (-not $Port) { $Port = "5050" }
    $HealthUrl = "http://127.0.0.1:$Port/api/health"

    if (-not (Invoke-Docker pull --quiet $Image).Ok) {
        Write-Log "Gagal mengambil $Image. Pastikan Docker Desktop berjalan, internet tersambung, dan package-nya publik."
        exit 1
    }
    $latest = (Invoke-Docker image inspect --format '{{.Id}}' $Image).Output
    $current = ""
    $running = "false"
    if (Test-Container) {
        $current = (Invoke-Docker container inspect --format '{{.Image}}' $Name).Output
        $running = (Invoke-Docker container inspect --format '{{.State.Running}}' $Name).Output
    }

    if ($latest -eq $current -and $running -eq "true") {
        Write-Log "Linkdrop sudah versi terbaru."
        exit 0
    }
    # Do not retry an image that already failed: each attempt means downtime.
    if ($running -eq "true" -and (Test-Path $FailedFile) -and ((Get-Content $FailedFile -Raw).Trim() -eq $latest)) {
        Write-Log "Versi terbaru sebelumnya gagal berjalan; tetap memakai versi saat ini sampai versi baru terbit."
        exit 0
    }

    function Start-Linkdrop([string]$ImageId) {
        Invoke-Docker run --detach --name $Name --restart unless-stopped `
            --publish "127.0.0.1:${Port}:8080" --env-file $EnvFile $ImageId | Out-Null
    }

    function Get-Health {
        $request = @{ Uri = $HealthUrl; TimeoutSec = 5; ErrorAction = "Stop" }
        # Windows PowerShell 5.1 has no -NoProxy; it already bypasses proxies for localhost.
        if ($PSVersionTable.PSVersion.Major -ge 6) { $request.NoProxy = $true }
        try { return Invoke-RestMethod @request } catch { return $null }
    }

    function Wait-Healthy {
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            $health = Get-Health
            if ($health -and $health.ok) { return $health }
            Start-Sleep -Seconds 2
        }
        return $null
    }

    Write-Log "Memasang versi baru Linkdrop..."
    Invoke-Docker rm --force $Name | Out-Null
    Start-Linkdrop $latest

    $health = Wait-Healthy
    if ($health) {
        $version = if ($health.version) { $health.version } else { "baru" }
        Write-Log "Linkdrop diperbarui ke versi $version di http://127.0.0.1:$Port."
        Remove-Item $FailedFile -Force -ErrorAction SilentlyContinue
        # Remove the replaced image only when nothing names it any more (an old
        # pull); an image the user built and tagged themselves is kept.
        if ($current -and $current -ne $latest -and (Invoke-Docker image inspect --format '{{len .RepoTags}}' $current).Output -eq "0") {
            Invoke-Docker image rm $current | Out-Null
        }
        exit 0
    }

    Write-Log ("Versi baru tidak merespons. Log terakhir:`n" + (Invoke-Docker logs --tail 5 $Name).Output)
    Set-Content -Path $FailedFile -Value $latest
    Invoke-Docker rm --force $Name | Out-Null
    if ($current) {
        Start-Linkdrop $current
        if (Wait-Healthy) {
            Write-Log "Versi sebelumnya dijalankan kembali."
        } else {
            Write-Log "Versi sebelumnya juga tidak merespons. Periksa: docker logs $Name"
        }
    }
    exit 1
} finally {
    Remove-Item $LockDir -Force -ErrorAction SilentlyContinue
}
