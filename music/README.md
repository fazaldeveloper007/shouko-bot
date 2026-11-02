# Kristy VC music

Opt-in YouTube audio player. See setup notes below.

## Design

One existing Python Discord client owns the main gateway. Each active guild has its own queue and token-free Node worker. The worker receives only required voice-session data over local pipes, never the bot token or AI/search keys. All guild/channel IDs come from the caller's current voice state. There is no owner/test-VC hardcoding.

Ordinary voice channels only. Music commands must be invoked from that VC's built-in text chat. Only members currently in that same VC may use controls; another VC cannot hijack an active guild player. No music output or music-state context goes to ordinary standalone text channels. When transient music context is injected into a reply, that bot reply is omitted from diary extraction, and current-playback clauses are filtered from the member text. Other personal facts/preferences from VC chat still use the existing diary pipeline. The music runtime context itself is never added to the diary input.

Regular YouTube video/audio-only, not YouTube Music or Bloomee. No AI calls in music search/queue/playback/control. Normal conversational chat still uses the existing AI brain, with current-track context only in the connected VC chat. Only one natural-language bridge exists: "Kristy <title> bajao/play/laga do" in the VC text chat runs the same code path as /play (first result; queue if busy). No AI decides or controls anything.

## Commands and buttons

- /joinvckristy: join caller VC, show player.
- /search query: up to5 YouTube results, each with Play and Add to queue buttons; expires3min.
- /play query: first supported search result/link added, starts when idle. Does not interrupt an existing track.
- /play without query: resume.
- /pause, /next, /queue, /stop, /leavevckristy.
- Stop stops and clears queue, stays connected. Leave clears and disconnects.
- Player buttons: Pause/Resume, Skip, Queue, Stop, Leave. All callbacks revalidate membership/session.

Play appends and starts when idle; Add to queue only queues while idle. /play or the idle-panel Play button starts a waiting queue. During playback either appends without interrupting the current track.

## Resource safeguards for review

Default max120min/video,256MiB audio download,50 waiting items/guild,2 concurrent metadata/download jobs across guilds.No voluntary auto-leave: the bot stays connected when idle or alone in VC and disconnects only via the Leave button/command, a platform drop, or shutdown. No playlists, live/upcoming streams, private/login-required videos, visual output, seeking or autoplay recommendations. YouTube bot checks and unavailable/no-Opus content can fail honestly. Downloaded files are temporary and deleted after track/cleanup. Download-before-play trades startup latency/storage for predictable local Opus playback without FFmpeg/transcoding.

These limits/behaviors are design choices to review before deployment, not evidence that arbitrary content always works. Pausing can hold the current session until user action. Queues are in-memory, not persisted across restart. Player controls remain valid for the active session and old views are stopped on panel refresh/session end. DAVE participant transitions suppress packet preparation/dispatch and pause media until the new executed encrypted epoch is ready, bounded10sec. A successful transition resumes only if the user had not paused; timeout clears/leaves. Real multi-user crypto continuity still needs a separate live test before any reliability promise.

## DAVE / cleanup

Exact voice0.19.2 + Davey0.1.12 pinned. Transport Ready alone is insufficient. Wait up to10sec for nonzero protocol, native ready, privacy present, executed transition, no pending transition/reinit. Every audio prepare+dispatch rechecks this proof synchronously and stops on loss. No unencrypted fallback. Python connect20sec; leave requires own voice-state confirmation, not just successful send. Pending joins are drained before explicit leave. Main-gateway disconnect clears voice sessions instead of silently resuming them.

DAVE gate uses inspected exact-version library internals. A dependency upgrade needs explicit compatibility review; current Davey>=0.2 docs expose APIs absent from0.1.12.

## Deployment is separately gated

MUSIC_ENABLED defaults off. MUSIC_SYNC_COMMANDS defaults off. Setting both to1 eventually creates global slash commands on startup, so this must happen only after separately approved deployment/registration. Existing chat bot features remain usable with music off. No startup command/phone mutation is included in this review delivery.

Dependencies for a future approved setup: existing Node and Python yt-dlp/EJS, pinned npm packages in this directory. No new Python voice native modules or FFmpeg needed for this WebM/Opus-only path. Do not infer that npm dependencies are installed in the main phone repo from earlier isolated tests. Setup route still needs review before user commands.

## Local tests

`python -W error -m unittest discover -s tests -v` from repo root.
`node tests/test_music_worker.cjs` with pinned packages in music/node_modules.
Tests use dummy IDs/media and mocked Discord/YouTube except actual offline Node package loading/adapter signalling/native initial-state checks. No login or live audio test occurs.

## Verification limits

Offline tests cover cancellation, queue controls, same-VC permissions, failure messages, encrypted voice readiness and member-transition gating. Short owner-only phone playback has been exercised. Long media, real multi-user encrypted transitions and full-bot production regression are not claimed verified. Playback waits for full validated audio download; startup delay depends on extraction and fetch. Exact-link metadata may be reused once for30sec in memory with signed-expiry/format/identity validation, never persisted or logged. No instant-play guarantee.

## Setup

Music remains off by default. Python extraction needs yt-dlp with EJS and Node. Use pinned voice0.19.2/Davey0.1.12; Android native addons need platform-compatible setup, not a generic unsupported install. Run offline tests before enabling. Existing base chat dependencies are unchanged.

Set MUSIC_ENABLED=1 only after installing music dependencies. MUSIC_SYNC_COMMANDS=1 registers commands additively, with an ignored local ownership ledger. Blank MUSIC_TEST_GUILD_ID selects global commands for all installed servers; set a server ID to scope registration. Existing same-name commands without recorded ownership cause a safe collision error, never bulk overwrite. Keep .music-command-ownership.json for later updates/rollback. Global command visibility may take time. Disable sync after successful registration if desired; commands remain registered. Disabling music stops runtime handlers but does not delete remote commands. Rollback removal must use verified owned IDs, never delete unrelated commands.
