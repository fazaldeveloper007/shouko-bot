# Local requester-only voice receive test

This is an isolated diagnostic, not Gemini Live integration and not a listening feature in the normal bot.

## What it does

- Uses the existing Discord bot token locally. Python keeps the token; the Node voice worker receives no environment credentials.
- Finds the supplied requester's current normal VC. Refuses ambiguous guilds, stage channels, other occupants, or a bot already connected to voice.
- Requires manual `START` in the terminal. Joins self-muted but not self-deafened. Waits for the existing DAVE readiness gate, then subscribes to this requester only.
- Decodes Opus into transient 48kHz stereo PCM and prints aggregate counts and peak amplitude. No recording, transcript, playback, Gemini call, or external AI upload is made.
- Stops after 20 seconds of receive readiness, any terminal input after start, Ctrl+C, requester leaving, another occupant joining, other-user speaking metadata, receive/decode failure or DAVE readiness loss. Full runner has a 120-second ceiling and no automatic reconnect/retry.
- Releases stream/decoder and leaves VC. Transient PCM and decoded packet buffers are overwritten after each decode. Runtime/native allocator memory cannot be promised forensic zeroization; no audio files are written by this test.

Only run in an otherwise empty VC with the requester and bot. Discord networking necessarily receives voice transport and membership metadata. Other users never get a receive subscription or Opus decoding; on known other-speaker activity it stops. Membership event delivery is not instantaneous, so the subscription allowlist is the primary audio isolation boundary.

## Install, then run manually

Keep any local `persona.py` edits. If pulling reports a conflict, stop and inspect rather than resetting/stashing blindly.

```sh
cd ~/shouko-bot
git pull --ff-only
cd voice_probe
npm ci --ignore-scripts
cd ..
```

Node must meet the version in package.json. Dependencies are isolated under voice_probe, not added to the music package. opusscript decoding has been tested on a Linux development environment, not verified on Termux. No ffmpeg is needed.

Stop the normal bot using its usual method. Do not run two copies with the same token. Join an empty normal VC from the requester's own Discord account. Run, substituting the user ID explicitly confirmed by that requester:

```sh
python voice_probe/receive.py --user-id YOUR_DISCORD_USER_ID
```

If the requester is in multiple visible guild VCs, add `--guild-id THE_INTENDED_GUILD_ID` rather than allowing the program to guess.

The terminal shows the selected requester/guild/channel numeric IDs. Check them, then type `START`. Wait for `RECEIVE READY`, speak for a few seconds, then type `STOP` plus Enter or let the 20-second timer end. Ctrl+C also stops. **The bot will not speak back during this test.**

After it ends, confirm Kristy left VC. If `LEAVE_UNCONFIRMED` appears, manually disconnect her before restarting the normal bot. Restart that bot using the usual command. No normal text/music code or settings are changed by the test.

## Startup diagnostics

The probe now matches the normal bot's nearest-ancestor `.env` lookup: start at the folder containing bot.py and walk upward to the first existing `.env`, without searching arbitrary directories. A nested voice_probe/.env is not used. Existing process environment values retain priority. This matters when the normal bot works from a parent `.env` (for example in the home folder) rather than the repo folder.

Startup now prints fixed stages/reason codes, never raw third-party exception text. For local checks without any Discord login or VC join, run `python voice_probe/receive.py --user-id YOUR_DISCORD_USER_ID --check-only`. It exits after checking the terminal, Node modules and availability of the existing credential (never printing it). `TERMINAL_REQUIRED` means run directly in a terminal. `EXISTING_TOKEN_SOURCE_NOT_FOUND` means neither this process/root .env nor the documented `~/.termux/boot/start-shouko.sh` supplied the existing credential. The loader reads only a single literal DISCORD_TOKEN assignment from that exact boot script; it never executes/sources the script, reads other process environments, imports unrelated credentials or writes a credential anywhere. Shell expansion/chained commands or duplicate assignments are refused with a fixed BOOT_* code. Environment/root .env retain priority. Do not paste or recreate credentials if the source is unavailable; establish the existing launch method instead. `NODE_DEPENDENCIES_UNAVAILABLE` means the Node preflight could not load the installed modules. Unexpected errors show only their class and stage.

No VC join is requested before terminal `START` and the `VC_JOIN_AFTER_START` marker. `PROBE_JOIN_NOT_REQUESTED` says only this run did not request a join, not that another bot process is absent. Any `LEAVE_UNCONFIRMED` requires manually checking/disconnecting the bot before restarting. Stop on a startup error and share only the fixed `PROBE_ERROR`/stage/leave lines. Do not repeatedly retry or share raw exception traces or .env.

## Reading the result

Share only the `RECEIVE_RESULT` and leave-confirmation line, not `.env` or full gateway logs.

- `TIME_LIMIT` or `STOPPED`, with `packets > 0`, `samples > 0`, and `nonzeroSamples > 0`, supports successful requester audio transport/DAVE decryption/Opus decoding in that run. It does not prove intelligible speech, STT, conversational latency or Gemini Live behavior.
- All-zero counts: no decoded requester audio was demonstrated. This is not success.
- `DAVE_WAIT_FAILED`, `DAVE_NOT_READY`, `RECEIVE_OR_DECRYPT_ERROR`, `DECODE_ERROR`, `OTHER_SPEAKER`, or another failure code: test stopped, do not infer receive success.

No automatic move to Gemini Live follows. Sending microphone audio to an AI service is a separate step requiring agreement, including its data-use and free-quota caveats.

## Offline tests

```sh
python -m pytest tests/test_voice_probe.py -q
```

Worker tests use a fake Discord connection and real opusscript encode/decode on synthetic audio. They cover requester subscription, unknown-speaker stop, DAVE-loss stop, timer/manual cleanup, numeric identity validation, membership isolation and background-launch refusal. They do not exercise a real Discord server or Termux microphone stream.
