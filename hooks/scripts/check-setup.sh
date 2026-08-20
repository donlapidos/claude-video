#!/usr/bin/env bash
# SessionStart hook for /watch — one-line status so users know what's wired up.
# Silent on ready state to avoid spam. Points at the installer when something
# is missing.
set -euo pipefail

CONFIG_FILE="$HOME/.config/watch/.env"

# Warn if the secrets file has loose permissions.
#
# POSIX only. Git Bash on Windows reports 0666 for an ordinary file regardless
# of the NTFS ACL, so this check would warn on every session start for every
# Windows user. Access control there comes from the profile ACL, not mode bits.
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*|Windows_NT) SKIP_PERM_CHECK=1 ;;
  *) SKIP_PERM_CHECK=0 ;;
esac

if [[ "$SKIP_PERM_CHECK" == "0" && -f "$CONFIG_FILE" ]]; then
  perms=$(stat -c '%a' "$CONFIG_FILE" 2>/dev/null || stat -f '%Lp' "$CONFIG_FILE" 2>/dev/null || echo "")
  if [[ -n "$perms" && "$perms" != "600" && "$perms" != "400" ]]; then
    echo "/watch: WARNING — $CONFIG_FILE has permissions $perms (should be 600)."
    echo "  Fix: chmod 600 $CONFIG_FILE"
  fi
fi

# Report only WHETHER a value is set - never the value itself.
#
# This hook only ever needs presence, so the secret must not transit a shell
# variable, the process table, or this hook's stdout (which is fed to the
# agent). awk does the emptiness test and prints a fixed "yes".
has_key() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    echo "yes"
    return
  fi
  if [[ -f "$CONFIG_FILE" ]]; then
    awk -F= -v k="$name" '
      /^[[:space:]]*#/ { next }
      $1 == k {
        sub(/^[[:space:]]*/, "", $2); sub(/[[:space:]]*$/, "", $2);
        gsub(/^["'\'']|["'\'']$/, "", $2);
        if ($2 != "") { print "yes" }
        exit
      }
    ' "$CONFIG_FILE"
  fi
}

# Non-secret marker, so reading its value is fine.
read_setting() {
  local name="$1"
  if [[ -f "$CONFIG_FILE" ]]; then
    awk -F= -v k="$name" '
      /^[[:space:]]*#/ { next }
      $1 == k {
        sub(/^[[:space:]]*/, "", $2); sub(/[[:space:]]*$/, "", $2);
        gsub(/^["'\'']|["'\'']$/, "", $2);
        print $2; exit
      }
    ' "$CONFIG_FILE"
  fi
}

HAS_FFMPEG=""
HAS_YTDLP=""
command -v ffmpeg >/dev/null 2>&1 && HAS_FFMPEG="yes"
command -v yt-dlp >/dev/null 2>&1 && HAS_YTDLP="yes"

HAS_GROQ="$(has_key GROQ_API_KEY)"
HAS_OPENAI="$(has_key OPENAI_API_KEY)"
SETUP_COMPLETE="$(read_setting SETUP_COMPLETE)"

# Fully configured → silent (Claude can surface status on demand via --check).
if [[ "$SETUP_COMPLETE" == "true" && -n "$HAS_FFMPEG" && -n "$HAS_YTDLP" ]]; then
  exit 0
fi

# First-run / partially-configured → one-line hint.
if [[ -z "$HAS_FFMPEG" || -z "$HAS_YTDLP" ]]; then
  echo "/watch: needs ffmpeg + yt-dlp. Run \`python3 \$CLAUDE_PLUGIN_ROOT/skills/watch/scripts/setup.py\` once to install and scaffold config."
elif [[ -z "$HAS_GROQ" && -z "$HAS_OPENAI" ]]; then
  echo "/watch: ready for videos with native captions. Add GROQ_API_KEY (preferred) or OPENAI_API_KEY to ~/.config/watch/.env to unlock Whisper fallback."
else
  echo "/watch: ready."
fi
