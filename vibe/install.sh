#!/usr/bin/env bash
# vibe installer.
#
# Installs `vibe` + the `vb` alias into ~/.local/bin, and fixes the certificate
# problem that python.org's macOS build ships with. Needs no admin rights and
# downloads nothing.
#
#   ./install.sh
#
set -uo pipefail

BIN="${VIBE_BIN:-$HOME/.local/bin}"
SHARE="$HOME/.local/share/vibe"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SRC_DIR/vibe.py"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mnote\033[0m %s\n' "$*"; }
err()  { printf '  \033[31mfail\033[0m %s\n' "$*" >&2; }

say ""
say "Installing vibe"
say "==============="

# --- 1. source file -------------------------------------------------------
if [ ! -f "$SRC" ]; then
    err "vibe.py is not next to this script."
    err "Put vibe.py and install.sh in the same folder, then run it again."
    exit 1
fi
ok "found vibe.py"

# --- 2. python ------------------------------------------------------------
PY=""
for candidate in python3 python3.14 python3.13 python3.12 python3.11 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' 2>/dev/null; then
            PY="$(command -v "$candidate")"
            break
        fi
    fi
done
if [ -z "$PY" ]; then
    err "Python 3.8 or newer is required but wasn't found."
    say ""
    say "  macOS:  install from https://www.python.org/downloads/"
    say "  Linux:  sudo pacman -S python   (or: sudo apt install python3)"
    exit 1
fi
ok "using $PY ($("$PY" -c 'import platform;print(platform.python_version())'))"

# --- 3. install the command ----------------------------------------------
mkdir -p "$BIN"
if ! install -m 755 "$SRC" "$BIN/vibe" 2>/dev/null; then
    err "could not write to $BIN"
    exit 1
fi
ln -sf "$BIN/vibe" "$BIN/vb"

# Point the shebang at the interpreter we just validated.
if ! "$PY" - "$BIN/vibe" "$PY" <<'PYEOF'
import sys
path, interpreter = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as fh:
    lines = fh.readlines()
if not lines or not lines[0].startswith("#!"):
    sys.exit("vibe.py has no #! line to replace")
lines[0] = "#!%s\n" % interpreter
with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(lines)
PYEOF
then
    err "could not set the interpreter line on $BIN/vibe"
    exit 1
fi
ok "installed $BIN/vibe and $BIN/vb"

# --- 4. certificates ------------------------------------------------------
# python.org's macOS build looks for its own cert.pem and often ships without
# one, so every HTTPS request dies with CERTIFICATE_VERIFY_FAILED. macOS already
# has all the root CAs in its keychain - export them to a file we control.
CERT_LINE=""
PEM="$SHARE/ca-certs.pem"
if "$PY" -c 'import ssl,sys; sys.exit(0 if ssl.create_default_context().cert_store_stats()["x509_ca"] > 0 else 1)' 2>/dev/null; then
    ok "certificate store looks healthy"
else
    warn "Python can't see any root certificates - fixing that"
    mkdir -p "$SHARE"
    FIXED=0

    if [ "$(uname -s)" = "Darwin" ] && command -v security >/dev/null 2>&1; then
        : > "$PEM"
        for keychain in \
            /System/Library/Keychains/SystemRootCertificates.keychain \
            /Library/Keychains/System.keychain
        do
            [ -f "$keychain" ] && security find-certificate -a -p "$keychain" >> "$PEM" 2>/dev/null
        done
        COUNT=$(grep -c "BEGIN CERTIFICATE" "$PEM" 2>/dev/null || true)
        COUNT=${COUNT:-0}
        if [ "$COUNT" -gt 20 ]; then
            ok "exported $COUNT root certificates from your macOS keychain"
            FIXED=1
        fi
    else
        for system_pem in \
            /etc/ssl/certs/ca-certificates.crt \
            /etc/pki/tls/certs/ca-bundle.crt \
            /etc/ssl/cert.pem
        do
            if [ -f "$system_pem" ]; then
                cp "$system_pem" "$PEM" && FIXED=1 && ok "copied system certificates from $system_pem"
                break
            fi
        done
    fi

    if [ "$FIXED" -eq 1 ]; then
        export SSL_CERT_FILE="$PEM"
        CERT_LINE="export SSL_CERT_FILE=\"$PEM\""
    else
        err "couldn't build a certificate bundle automatically"
        warn "see the 'If TLS still fails' section of SETUP.md"
    fi
fi

# --- 5. shell config ------------------------------------------------------
RC=""
case "${SHELL##*/}" in
    zsh)  RC="$HOME/.zshrc" ;;
    bash) if [ -f "$HOME/.bash_profile" ]; then RC="$HOME/.bash_profile"; else RC="$HOME/.bashrc"; fi ;;
    fish) RC="$HOME/.config/fish/config.fish" ;;
    *)    RC="$HOME/.profile" ;;
esac

# "$HOME/.local/bin" reads better than a hardcoded /Users/you/... path, and
# survives a dotfile being copied to another machine.
if [ "$BIN" = "$HOME/.local/bin" ]; then
    BIN_FOR_RC='$HOME/.local/bin'
    BIN_FOR_FISH='$HOME/.local/bin'
else
    BIN_FOR_RC="$BIN"
    BIN_FOR_FISH="$BIN"
fi

add_line() {
    line="$1"
    file="$2"
    [ -z "$line" ] && return 0
    mkdir -p "$(dirname "$file")"
    touch "$file"
    if ! grep -Fqx "$line" "$file" 2>/dev/null; then
        printf '\n# added by the vibe installer\n%s\n' "$line" >> "$file"
        ok "updated ${file}"
    fi
}

if [ "${RC##*/}" = "config.fish" ]; then
    case ":$PATH:" in
        *":$BIN:"*) ok "$BIN is already on your PATH" ;;
        *)          add_line "set -gx PATH \"$BIN_FOR_FISH\" \$PATH" "$RC" ;;
    esac
    [ -n "$CERT_LINE" ] && add_line "set -gx SSL_CERT_FILE \"$PEM\"" "$RC"
else
    case ":$PATH:" in
        *":$BIN:"*) ok "$BIN is already on your PATH" ;;
        *)          add_line "export PATH=\"$BIN_FOR_RC:\$PATH\"" "$RC" ;;
    esac
    [ -n "$CERT_LINE" ] && add_line "$CERT_LINE" "$RC"
fi

# --- 6. smoke test --------------------------------------------------------
say ""
if "$BIN/vibe" --version >/dev/null 2>&1; then
    ok "$("$BIN/vibe" --version) is working"
else
    err "vibe installed but won't run - try: $PY $BIN/vibe --version"
    exit 1
fi

say ""
say "Done. Open a new terminal, or run:  source $RC"
say ""
say "Then:"
say "  vb login                                   # Gitea username + access token"
say "  vb config --global user.name  \"Your Name\""
say "  vb config --global user.email \"you@example.com\""
say "  vb clone                                   # makes ./Chronos"
say "  vb github-pull https://github.com/OWNER/REPO # import every GitHub branch, once"
say "  vb versions                                # list recoverable versions"
say "  vb recover <id>                            # recover + push to Gitea"
say ""
