# Restricted group voice test, inactive until approved

This is a separate manually started adult-only, non-sensitive group voice test.
It is not enabled by importing the modules or running the normal `bot.py`.
It does not unlock full local persona or diary access. The existing Free-source
review gate in `duplex.py` is unchanged.

## Before activation

- The owner must approve the exact notice and member-visible button/status text.
- Keep the real allowlist and usernames in a private local config OUTSIDE git.
  Do not publish Discord IDs or copy the private config into the repository.
- Confirm the exact current guild and voice channel. No old channel is a default.
- Use a genuinely adult-restricted test client and VC, not an open mixed-age
  channel. Default role must explicitly deny both Connect and View Channel.
  Explicit Connect/View Channel allows must be individual approved members and
  this bot, not broad roles. The runner refuses unresolved members/other bots.
  Discord administrators can bypass channel permissions, and events arrive with
  latency. This cannot promise that a new listener hears zero frames before stop.
- Each present human must independently assert they are 18+ and agree to Google's
  processing and the exact current audience. Owner-provided IDs are NOT consent,
  and member age assertions are not independent age verification.
- Confirm the installed key's current Free-tier project. A flag is an owner
  assertion, not a billing lookup. No billing, provider, voice or model changes.
- Stop the normal bot first. Existing dependencies must already be installed.

## Runtime behavior

Same `gemini-3.1-flash-live-preview` and `Sulafat`. No local persona, diary, text-bot
imports, chat-history reads, memory extraction/save or audio/transcript files.
Only local speech-gated audio goes to Google. Discord IDs are validated locally
and stripped from provider audio events. No name, secondary-account association
or private-history recognition is claimed.

The notice is posted in the restricted VC text chat only after the owner types
START. All present humans must click **I am 18+ and Agree** within 120 seconds.
**Decline / Stop** remains available during the session; terminal input/Ctrl+C
also stops. Nobody's audio is subscribed or sent to Google before all agree.
An audience change invalidates consent. A new session needs fresh agreements.

The current conservative resource cap is eight simultaneous humans, even if the
private allowlist is longer. This is not a measured phone capacity guarantee.
Each participant has an isolated Node worker-thread Opus decoder, with a bounded
48-packet queue. An overlapping human utterance stops the test rather than mixing
speakers. One member can interrupt Kristy's reply. Use earphones to reduce echo.

The existing 600-second boundary finishes only an already pending/playing reply;
no new utterance starts after it. Speaking during that final reply stops/leaves.
60-second input/idle bounds, provider/error/queue stops still apply. No reconnect,
paid fallback or automatic model switch.

Non-sensitive-only is a real restriction, not a guarantee made by a checkbox or
filter. Google Free inputs/replies may improve products and be human-reviewed.
Do not discuss personal, sensitive or confidential material. The prompt asks for
redirection but cannot prevent somebody from speaking private data into a live
microphone.

## Private config shape

The separately delivered private config contains schema 1, `guild_id`, `owner_id`,
`voice_channel_id`, `notice_revision` (`group-free-draft1`),
`notice_approved: false`, `maximum_concurrent_humans: 8`,
`members: [{user_id, label}]`, `source_message_id`, `consents: []`.
Labels are inert local references, not identity matching. There are no stored
member agreements. The actual voice-channel ID must be filled before launch.

After the pending inputs and notice approval are settled, the intended manual
entrypoint is `python voice_live/group.py --private-config /PRIVATE/PATH.json
--confirmed-free-tier --notice-approved`. This is documentation, not an instruction
to run the pending test now. Flags do not create permission. The owner first
reviews START locally; the runner does not deploy or restart the normal bot.

## Verification status

Synthetic/no-network coverage includes policy/actor binding, audience races,
queued-audio rejection after joins, output rejection after leave/revoke, no
connect without all grants, and the default notice/Free-tier gates. Actual pinned
Discord receiver, Opus codec and AudioPlayer are exercised with encrypted
synthetic RTP and a fake gateway. Second-member interruption, stale-output drop,
deadline drain, unknown speaker, DAVE loss and manual stop are covered.

No real Discord/Google session or phone performance test is implied. Discord UI
button rendering and real permissions still need live preflight at activation.

Sources: https://ai.google.dev/gemini-api/terms and
https://ai.google.dev/gemini-api/docs/pricing

## Requested slash workflow

The requested normal-bot entrypoint is `/kristylive`, with `/leavevckristy` stopping
Live as well as music. The implementation reuses Music's existing CommandTree,
Python gateway and per-guild voice lock. Live reserves that lock's shared state
before consent; music slash/natural joins cannot race it into a second connection.
A music session must be left first, never silently interrupted or taken over.
An unconfirmed Live leave retains a reservation and requires manual recovery.

This path is disabled by default. Activation requires existing `MUSIC_ENABLED=1`,
`KRISTY_LIVE_ENABLED=1`, a private-config path in `KRISTY_LIVE_PRIVATE_CONFIG`,
owner-approved notice via `KRISTY_LIVE_NOTICE_APPROVED=1`, and current-key assertion
via `KRISTY_LIVE_CONFIRMED_FREE_TIER=1`. These are operational gates, not permission
by themselves. The normal existing additive command-registration ledger and
`MUSIC_SYNC_COMMANDS`/test-guild setup apply; never use bulk tree sync or discard
its ownership ledger. No registration happens during import.

`/kristylive` gets the current caller's actual VC and validates its current
restrictions, exact configured guild, allowlisted caller, bot permissions and
present audience. The private config's old/manual VC field is not selected by
this slash path. The restricted VC chat receives the reviewed notice with the
current audience IDs and Agree/Decline buttons. Each real authenticated member
clicks for themselves. No manual terminal is used for the slash flow.

Normal text bot startup already imports its own text diary/persona. The precise
privacy boundary is that the Live path does not read, use or send those sources.
A fresh standalone group module import does not load them either.
