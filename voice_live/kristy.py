"""Manual natural Live Kristy entrypoint. Review gate is mandatory, no autojoin."""
import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voice_live.duplex import run, report_error
from voice_probe.receive import snowflake
import diary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', type=snowflake, required=True)
    parser.add_argument('--guild-id', type=snowflake, required=True)
    parser.add_argument('--memory-selection', type=Path, help='Optional reviewed selection path; defaults to canonical diary directory')
    parser.add_argument('--confirmed-free-tier', action='store_true')
    args = parser.parse_args()
    selection = args.memory_selection or Path(diary.MEMORY_DIR) / f'voice-selection-{args.user_id}-{args.guild_id}.json'
    asyncio.run(run(args.user_id, args.guild_id, args.confirmed_free_tier, selection, full_persona=True))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped manually. Check bot left VC before restart. Memory save not guaranteed.')
    except Exception as error:
        report_error(error)
        sys.exit(1)
