#!/usr/bin/env bash
# Run Linkdrop natively on macOS, without Docker, and keep it updated.
#
# Linkdrop runs as a background launchd service on 127.0.0.1:5050, so
# Tailscale Serve or Cloudflare Tunnel can keep pointing at the same port.
# A second launchd job checks GitHub every 15 minutes and updates it; a
# version that fails its health check is rolled back.
#
# Usage, from a clone of the repository:
#   ./scripts/macos-native.sh install     set up and start Linkdrop
#   ./scripts/macos-native.sh update      update now (also runs on its own)
#   ./scripts/macos-native.sh status      show the running version
#   ./scripts/macos-native.sh uninstall   stop and remove the services
#
# Written for the bash 3.2 that ships with macOS.
set -euo pipefail

# ffmpeg-full comes first: Homebrew's default ffmpeg is built without libass,
# which burned-in subtitles need. It is keg-only, so it is not on PATH itself.
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/usr/local/opt/ffmpeg-full/bin:/opt/homebrew/bin:/usr/local/bin:$PATH:/usr/bin:/bin:/usr/sbin:/sbin"

REPO_URL="${LINKDROP_REPO:-https://github.com/Zvareldric/Linkdrop.git}"
BRANCH="${LINKDROP_BRANCH:-main}"
BASE_DIR="${LINKDROP_CONFIG_DIR:-$HOME/.linkdrop}"
AGENTS_DIR="${LINKDROP_LAUNCH_AGENTS:-$HOME/Library/LaunchAgents}"
APP_DIR="$BASE_DIR/app"
VENV_DIR="$BASE_DIR/venv"
ENV_FILE="$BASE_DIR/linkdrop.env"
LOG_DIR="$BASE_DIR/logs"
WORK_DIR="$BASE_DIR/work"
SELF="$BASE_DIR/linkdrop-native.sh"
LOCK_DIR="$BASE_DIR/update.lock"
FAILED_FILE="$BASE_DIR/failed-commit"
YTDLP_STAMP="$BASE_DIR/yt-dlp-checked"
SERVER_LABEL="com.linkdrop.server"
UPDATE_LABEL="com.linkdrop.update"
DOMAIN="gui/$(id -u)"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "$*"; exit 1; }

env_value() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true; }

port() {
    local value
    value="$(env_value LINKDROP_PORT)"
    printf '%s' "${value:-5050}"
}

health() { curl --silent --fail --max-time 3 --noproxy '*' "http://127.0.0.1:$(port)/api/health" 2>/dev/null; }

running_version() { health | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'; }

# Wait up to 90 seconds for the server to answer, optionally with a specific
# version. A crashing app can keep the port open while requests hang, so the
# limit is on total time rather than on the number of attempts.
wait_healthy() {
    local expected="${1:-}" deadline=$((SECONDS + 90)) version
    while [ "$SECONDS" -lt "$deadline" ]; do
        if version="$(running_version)" && [ -n "$version" ]; then
            if [ -z "$expected" ] || [ "$version" = "$expected" ]; then
                return 0
            fi
        fi
        sleep 2
    done
    return 1
}

find_python() {
    local candidate
    for candidate in "${LINKDROP_PYTHON:-}" python3.12 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
        if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

# Read the filter list into a variable first: "ffmpeg | grep -q" can report a
# failure under pipefail when grep exits early and ffmpeg gets SIGPIPE.
ffmpeg_can_burn() {
    local filters
    filters="$(ffmpeg -hide_banner -filters 2>/dev/null)" || return 1
    case "$filters" in *" subtitles "*) return 0 ;; esac
    return 1
}

install_dependencies() {
    local missing=""
    find_python >/dev/null || missing="$missing python@3.12"
    if ! command -v ffmpeg >/dev/null 2>&1 || ! ffmpeg_can_burn; then
        missing="$missing ffmpeg-full"
    fi
    command -v deno >/dev/null 2>&1 || command -v node >/dev/null 2>&1 || missing="$missing deno"
    command -v git >/dev/null 2>&1 || missing="$missing git"
    if [ -z "$missing" ]; then
        return 0
    fi
    command -v brew >/dev/null 2>&1 \
        || die "Homebrew belum terpasang. Pasang dari https://brew.sh lalu jalankan perintah ini lagi."
    log "Memasang:$missing"
    # Word splitting is intended: one package name per word.
    # shellcheck disable=SC2086
    brew install $missing
    hash -r
    ffmpeg_can_burn || log "Peringatan: FFmpeg masih tanpa libass; subtitle permanen tidak akan tersedia."
}

pip_install() {
    "$VENV_DIR/bin/python" -m pip install --quiet --disable-pip-version-check "$@"
}

write_plist() {
    local label="$1" file="$AGENTS_DIR/$1.plist" command="$2" schedule="$3" log_file="$4"
    mkdir -p "$AGENTS_DIR"
    cat > "$file" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$SELF</string><string>$command</string></array>
  <key>RunAtLoad</key><true/>
$schedule
  <key>StandardOutPath</key><string>$log_file</string>
  <key>StandardErrorPath</key><string>$log_file</string>
</dict>
</plist>
EOF
}

load_agent() {
    launchctl bootout "$DOMAIN/$1" >/dev/null 2>&1 || true
    launchctl bootstrap "$DOMAIN" "$AGENTS_DIR/$1.plist"
}

restart_server() { launchctl kickstart -k "$DOMAIN/$SERVER_LABEL"; }

port_owner() { lsof -nP -iTCP:"$(port)" -sTCP:LISTEN 2>/dev/null | sed -n '2p' || true; }

stop_docker_setup() {
    local agent="$AGENTS_DIR/com.linkdrop.autoupdate.plist"
    if [ -f "$agent" ]; then
        launchctl bootout "$DOMAIN/com.linkdrop.autoupdate" >/dev/null 2>&1 || true
        mkdir -p "$BASE_DIR/backup"
        mv "$agent" "$BASE_DIR/backup/"
        log "Pembaruan otomatis versi Docker dimatikan."
    fi
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker info >/dev/null 2>&1; then
        log "Docker Desktop tidak berjalan. Jika container linkdrop lama masih ada, ia akan merebut port $(port) saat Docker dibuka; hapus dengan: docker rm -f linkdrop"
    elif docker container inspect linkdrop >/dev/null 2>&1; then
        docker rm --force linkdrop >/dev/null
        log "Container Docker linkdrop dihentikan. Docker Desktop boleh ditutup."
    fi
}

cmd_serve() {
    local line key
    # The env file uses Docker's KEY=VALUE format, where values may contain
    # spaces or '=', so it is read line by line instead of being sourced.
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        key="${line%%=*}"
        case "$key" in *[!A-Z0-9_]*|'') continue ;; esac
        export "$key=${line#*=}"
    done < "$ENV_FILE"
    LINKDROP_VERSION="$(git -C "$APP_DIR" rev-parse HEAD)"
    export LINKDROP_VERSION DOWNLOAD_DIR="$WORK_DIR"
    mkdir -p "$WORK_DIR"
    cd "$APP_DIR"
    exec "$VENV_DIR/bin/gunicorn" --bind "127.0.0.1:$(port)" --workers 1 --threads 4 \
        --timeout 0 --access-logfile - app:app
}

cmd_install() {
    local python
    [ "$(uname -s)" = "Darwin" ] || [ -n "${LINKDROP_ALLOW_NON_MACOS:-}" ] \
        || die "Skrip ini untuk macOS. Di Windows dan Linux gunakan panduan Docker."
    install_dependencies
    python="$(find_python)" || die "Python 3.12 tidak ditemukan."

    mkdir -p "$BASE_DIR" "$LOG_DIR" "$WORK_DIR"
    chmod 700 "$BASE_DIR"
    if [ -d "$APP_DIR/.git" ]; then
        git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
        git -C "$APP_DIR" reset --quiet --hard "origin/$BRANCH"
    else
        log "Mengunduh Linkdrop ke $APP_DIR..."
        git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    fi
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        "$python" -m venv "$VENV_DIR"
    fi
    log "Memasang dependensi Python..."
    pip_install --upgrade pip
    pip_install --upgrade -r "$APP_DIR/requirements.txt"
    date +%s > "$YTDLP_STAMP"

    if [ ! -f "$ENV_FILE" ]; then
        (
            umask 077
            {
                printf 'DOWNLOAD_TOKEN_SECRET=%s\n' "$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')"
                printf 'MAX_MEDIA_BYTES=2147483648\n'
                printf 'LINKDROP_PORT=%s\n' "${LINKDROP_PORT:-5050}"
            } > "$ENV_FILE"
        )
    fi

    cp "$APP_DIR/scripts/macos-native.sh" "$SELF.tmp" && mv "$SELF.tmp" "$SELF"
    stop_docker_setup

    launchctl bootout "$DOMAIN/$SERVER_LABEL" >/dev/null 2>&1 || true
    if [ -n "$(port_owner)" ]; then
        log "Port $(port) masih dipakai program lain:"
        port_owner
        die "Hentikan program itu dulu (misalnya layanan Linkdrop lama), lalu jalankan install lagi."
    fi

    write_plist "$SERVER_LABEL" serve "  <key>KeepAlive</key><true/>" "$LOG_DIR/server.log"
    write_plist "$UPDATE_LABEL" update "  <key>StartInterval</key><integer>900</integer>" "$LOG_DIR/update.log"
    load_agent "$SERVER_LABEL"
    if ! wait_healthy "$(git -C "$APP_DIR" rev-parse HEAD)"; then
        tail -n 20 "$LOG_DIR/server.log" 2>/dev/null || true
        die "Linkdrop tidak merespons. Log di atas diambil dari $LOG_DIR/server.log."
    fi
    load_agent "$UPDATE_LABEL"
    log "Linkdrop berjalan tanpa Docker di http://127.0.0.1:$(port) (versi $(running_version | cut -c1-7))."
    log "Pembaruan otomatis aktif setiap 15 menit. Log: $LOG_DIR/update.log"
}

refresh_ytdlp() {
    local now last before after
    now="$(date +%s)"
    last="$(cat "$YTDLP_STAMP" 2>/dev/null || echo 0)"
    [ $((now - last)) -ge 86400 ] || return 0
    before="$("$VENV_DIR/bin/python" -m pip show yt-dlp 2>/dev/null | sed -n 's/^Version: //p')"
    pip_install --upgrade "yt-dlp[default]" || { log "yt-dlp gagal diperbarui; dicoba lagi nanti."; return 0; }
    echo "$now" > "$YTDLP_STAMP"
    after="$("$VENV_DIR/bin/python" -m pip show yt-dlp 2>/dev/null | sed -n 's/^Version: //p')"
    [ "$before" != "$after" ] || return 0
    log "yt-dlp diperbarui dari $before ke $after; menjalankan ulang Linkdrop..."
    restart_server
    if wait_healthy; then
        log "Linkdrop berjalan dengan yt-dlp $after."
    else
        log "Linkdrop tidak merespons dengan yt-dlp $after; kembali ke $before."
        pip_install "yt-dlp[default]==$before"
        restart_server
        wait_healthy || log "Linkdrop masih tidak merespons. Periksa $LOG_DIR/server.log"
    fi
}

cmd_update() {
    local old new
    [ -d "$APP_DIR/.git" ] || die "Linkdrop belum dipasang. Jalankan: ./scripts/macos-native.sh install"
    # A lock left behind by a run that was killed (sleep, power loss) expires.
    if [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +60 2>/dev/null)" ]; then
        rmdir "$LOCK_DIR"
    fi
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        log "Update lain masih berjalan; lewati."
        return 0
    fi
    trap 'rmdir "$LOCK_DIR"' EXIT

    if ! git -C "$APP_DIR" fetch --quiet origin "$BRANCH"; then
        log "Gagal menghubungi GitHub; dicoba lagi nanti."
        return 0
    fi
    old="$(git -C "$APP_DIR" rev-parse HEAD)"
    new="$(git -C "$APP_DIR" rev-parse "origin/$BRANCH")"

    if [ "$old" = "$new" ]; then
        if [ -z "$(running_version)" ]; then
            log "Linkdrop tidak merespons; menjalankan ulang..."
            restart_server
            wait_healthy || log "Linkdrop masih tidak merespons. Periksa $LOG_DIR/server.log"
        fi
        refresh_ytdlp
        log "Linkdrop sudah versi terbaru ($(printf '%s' "$old" | cut -c1-7))."
        return 0
    fi
    # Do not retry a commit that already failed: each attempt means downtime.
    if [ -f "$FAILED_FILE" ] && [ "$(cat "$FAILED_FILE")" = "$new" ]; then
        log "Versi $(printf '%s' "$new" | cut -c1-7) sebelumnya gagal berjalan; tetap memakai versi saat ini."
        return 0
    fi

    log "Memperbarui Linkdrop $(printf '%s' "$old" | cut -c1-7) -> $(printf '%s' "$new" | cut -c1-7)..."
    git -C "$APP_DIR" reset --quiet --hard "$new"
    if pip_install -r "$APP_DIR/requirements.txt" && restart_server && wait_healthy "$new"; then
        rm -f "$FAILED_FILE"
        # Replace this script by moving a new file into place: bash keeps
        # reading the old one while it runs.
        cp "$APP_DIR/scripts/macos-native.sh" "$SELF.tmp" && mv "$SELF.tmp" "$SELF"
        log "Linkdrop diperbarui ke versi $(printf '%s' "$new" | cut -c1-7)."
        return 0
    fi

    log "Versi baru tidak merespons. Log terakhir:"
    tail -n 10 "$LOG_DIR/server.log" 2>/dev/null || true
    printf '%s\n' "$new" > "$FAILED_FILE"
    git -C "$APP_DIR" reset --quiet --hard "$old"
    pip_install -r "$APP_DIR/requirements.txt" || true
    restart_server
    if wait_healthy "$old"; then
        log "Versi sebelumnya ($(printf '%s' "$old" | cut -c1-7)) dijalankan kembali."
    else
        log "Versi sebelumnya juga tidak merespons. Periksa $LOG_DIR/server.log"
    fi
    return 1
}

cmd_status() {
    local version
    if version="$(running_version)" && [ -n "$version" ]; then
        log "Linkdrop berjalan di http://127.0.0.1:$(port), versi $(printf '%s' "$version" | cut -c1-7)."
    else
        log "Linkdrop tidak merespons di port $(port). Log: $LOG_DIR/server.log"
    fi
    launchctl list 2>/dev/null | grep -E "$SERVER_LABEL|$UPDATE_LABEL" || log "Layanan launchd belum terpasang."
}

cmd_uninstall() {
    local label
    for label in "$UPDATE_LABEL" "$SERVER_LABEL"; do
        launchctl bootout "$DOMAIN/$label" >/dev/null 2>&1 || true
        rm -f "$AGENTS_DIR/$label.plist"
    done
    log "Layanan Linkdrop dihentikan. Data dan pengaturan tetap ada di $BASE_DIR; hapus folder itu jika tidak diperlukan."
}

case "${1:-}" in
    install) cmd_install ;;
    update) cmd_update ;;
    status) cmd_status ;;
    uninstall) cmd_uninstall ;;
    serve) cmd_serve ;;
    *)
        printf 'Penggunaan: %s {install|update|status|uninstall}\n' "$0" >&2
        exit 2
        ;;
esac
