#!/data/data/com.termux/files/usr/bin/bash
# Kristy - normal production start (Gaming Impact server).
#
# Usage:
#   cd ~/shouko-bot
#   ./start-kristy.sh        (first time only: chmod +x start-kristy.sh)
#
# API keys and secrets are NOT in this file. Export them in the shell
# before starting, exactly like before (e.g. the Gemini key). This
# script only sets feature flags, clears test-mode variables, and
# starts the bot in the foreground.

set -euo pipefail
cd "$(dirname "$0")"

# Safety: never run two copies at the same time.
if pgrep -f "python bot.py" >/dev/null 2>&1; then
  echo "bot.py pehle se chal raha hai. Pehle usko band karo (Ctrl+C), phir dobara start karo."
  exit 1
fi

# Trial/test variables must never leak into a production start.
unset KRISTY_ONLY_GUILD_ID ANNOUNCE_ENABLED

# Production flags (verified launch settings, unchanged).
export MUSIC_ENABLED=1
export KRISTY_LIVE_ENABLED=1
export KRISTY_LIVE_PRIVATE_CONFIG="$HOME/.config/kristy/live.json"
export KRISTY_LIVE_NOTICE_APPROVED=1
export KRISTY_LIVE_CONFIRMED_FREE_TIER=1
export MUSIC_SYNC_COMMANDS=0
export MUSIC_TEST_GUILD_ID=
export BIRTHDAY_ENABLED=1
export PROACTIVE_ENABLED=1
export PROACTIVE_GRACE_MINUTES=1
export OWNER_ID=1217032327260274773

exec python bot.py
