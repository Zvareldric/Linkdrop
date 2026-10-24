#!/usr/bin/env bash
# Keep the local Linkdrop container on the newest published image.
#
# Safe to run repeatedly (for example every 15 minutes from cron). It pulls
# the image, and only when a newer one exists replaces the container while
# keeping the same settings and port. If the new version fails its health
# check, the previous version is started again.
#
# Settings live in ~/.linkdrop/linkdrop.env. On the first run they are copied
# from an existing "linkdrop" container, or a new token secret is generated.
set -euo pipefail

# cron and launchd start with a minimal PATH. Docker Desktop for Mac puts
# docker and its credential helper in one of these, depending on the install.
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin"

IMAGE="${LINKDROP_IMAGE:-ghcr.io/zvareldric/linkdrop:latest}"
NAME="${LINKDROP_CONTAINER:-linkdrop}"
CONFIG_DIR="${LINKDROP_CONFIG_DIR:-$HOME/.linkdrop}"
ENV_FILE="$CONFIG_DIR/linkdrop.env"
LOCK_DIR="$CONFIG_DIR/update.lock"
FAILED_FILE="$CONFIG_DIR/failed-image"
KEEP_KEYS='^(DOWNLOAD_TOKEN_SECRET|DOWNLOAD_TOKEN_MAX_AGE|MAX_MEDIA_BYTES|HTTP_TIMEOUT|FFMPEG_TIMEOUT|ACCESS_USERNAME|ACCESS_PASSWORD|TRUSTED_PROXY_HEADER|RATE_LIMIT_INFO|RATE_LIMIT_DOWNLOAD|GALLERY_CONCURRENCY|BATCH_CONCURRENCY|YTDLP_[A-Z0-9_]+|LINKDROP_PORT)='

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

container_exists() { docker container inspect "$NAME" >/dev/null 2>&1; }

env_value() { grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true; }

if ! command -v docker >/dev/null 2>&1; then
    log "Perintah docker tidak ditemukan. Pasang Docker Desktop, buka aplikasinya sekali sampai selesai, lalu jalankan skrip ini lagi."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    log "Docker belum berjalan. Buka Docker Desktop, tunggu sampai statusnya running, lalu jalankan skrip ini lagi."
    exit 1
fi

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
# A lock left behind by a run that was killed (sleep, power loss) expires.
if [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +60 2>/dev/null)" ]; then
    rmdir "$LOCK_DIR"
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "Update lain masih berjalan; lewati."
    exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

if [ ! -f "$ENV_FILE" ]; then
    umask 077
    : > "$ENV_FILE"
    if container_exists; then
        docker container inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" \
            | grep -E "$KEEP_KEYS" >> "$ENV_FILE" || true
        port="$(docker container inspect \
            --format '{{with index .HostConfig.PortBindings "8080/tcp"}}{{(index . 0).HostPort}}{{end}}' \
            "$NAME" 2>/dev/null || true)"
        if [ -n "$port" ] && ! grep -q '^LINKDROP_PORT=' "$ENV_FILE"; then
            printf 'LINKDROP_PORT=%s\n' "$port" >> "$ENV_FILE"
        fi
        log "Pengaturan container lama disalin ke $ENV_FILE."
    fi
    if ! grep -q '^DOWNLOAD_TOKEN_SECRET=' "$ENV_FILE"; then
        printf 'DOWNLOAD_TOKEN_SECRET=%s\n' "$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')" >> "$ENV_FILE"
    fi
    grep -q '^MAX_MEDIA_BYTES=' "$ENV_FILE" || printf 'MAX_MEDIA_BYTES=2147483648\n' >> "$ENV_FILE"
    grep -q '^LINKDROP_PORT=' "$ENV_FILE" || printf 'LINKDROP_PORT=5050\n' >> "$ENV_FILE"
fi

PORT="${LINKDROP_PORT:-$(env_value LINKDROP_PORT)}"
PORT="${PORT:-5050}"

if ! docker pull --quiet "$IMAGE" >/dev/null; then
    log "Gagal mengambil $IMAGE. Periksa koneksi internet dan apakah package-nya publik."
    exit 1
fi
latest="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
current=""
running="false"
if container_exists; then
    current="$(docker container inspect --format '{{.Image}}' "$NAME")"
    running="$(docker container inspect --format '{{.State.Running}}' "$NAME")"
fi

if [ "$latest" = "$current" ] && [ "$running" = "true" ]; then
    log "Linkdrop sudah versi terbaru."
    exit 0
fi
# Do not retry an image that already failed: each attempt means downtime.
if [ "$running" = "true" ] && [ -f "$FAILED_FILE" ] && [ "$(cat "$FAILED_FILE")" = "$latest" ]; then
    log "Versi terbaru sebelumnya gagal berjalan; tetap memakai versi saat ini sampai versi baru terbit."
    exit 0
fi

start_container() {
    local output
    if output="$(docker run --detach --name "$NAME" --restart unless-stopped \
        --publish "127.0.0.1:$PORT:8080" \
        --env-file "$ENV_FILE" \
        "$1" 2>&1)"; then
        return 0
    fi
    docker rm --force "$NAME" >/dev/null 2>&1 || true
    if printf '%s' "$output" | grep -qiE 'address already in use|port is already allocated|ports are not available'; then
        log "Port $PORT sudah dipakai program lain, misalnya Linkdrop lama yang berjalan dengan gunicorn."
        log "Hentikan program itu dulu (cari dengan: lsof -nP -iTCP:$PORT -sTCP:LISTEN), lalu jalankan skrip ini lagi."
    else
        log "Container gagal dibuat: $output"
    fi
    return 1
}

healthy() {
    local _
    for _ in $(seq 1 30); do
        if curl --silent --fail --noproxy '*' "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

log "Memasang versi baru Linkdrop..."
docker rm --force "$NAME" >/dev/null 2>&1 || true
if start_container "$latest"; then
    if healthy; then
        version="$(curl --silent --noproxy '*' "http://127.0.0.1:$PORT/api/health" \
            | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p')"
        log "Linkdrop diperbarui ke versi ${version:-baru} di http://127.0.0.1:$PORT."
        rm -f "$FAILED_FILE"
        # Remove the replaced image only when nothing names it any more (an old
        # pull); an image the user built and tagged themselves is kept.
        if [ -n "$current" ] && [ "$current" != "$latest" ] \
            && [ "$(docker image inspect --format '{{len .RepoTags}}' "$current" 2>/dev/null)" = "0" ]; then
            docker image rm "$current" >/dev/null 2>&1 || true
        fi
        exit 0
    fi
    # Only an image that started but never answered is remembered as broken;
    # a busy port is not the image's fault.
    log "Versi baru tidak merespons. Log terakhir:"
    docker logs --tail 5 "$NAME" 2>&1 || true
    printf '%s\n' "$latest" > "$FAILED_FILE"
    docker rm --force "$NAME" >/dev/null 2>&1 || true
fi

if [ -n "$current" ]; then
    if start_container "$current" && healthy; then
        log "Versi sebelumnya dijalankan kembali."
    else
        log "Versi sebelumnya juga tidak dapat dijalankan. Periksa: docker logs $NAME"
    fi
fi
exit 1
