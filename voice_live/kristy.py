"""Manual natural Live Kristy entrypoint. Approved own-ID diary context, no autojoin."""
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
    parser.add_argument('--memory-selection', type=Path, help='Optional narrower per-note selection; default is approved automatic own-ID diary')
    parser.add_argument('--confirmed-free-tier', action='store_true')
    args = parser.parse_args()
    # Automatic own-diary scope is approved only for the bound owner/guild.
    # Explicit selection remains an optional narrower legacy route.
    asyncio.run(run(args.user_id, args.guild_id, args.confirmed_free_tier, args.memory_selection,
                    full_persona=True, automatic_diary=args.memory_selection is None))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped manually. Check bot left VC before restart. Memory save not guaranteed.')
    except Exception as error:
        report_error(error)
        sys.exit(1)
