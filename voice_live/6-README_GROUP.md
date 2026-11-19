# Restricted group Live, disabled until approved

Normal-bot `/kristylive` starts a restricted adult non-sensitive voice flow in
the authenticated caller's current VC. `/leavevckristy` stops Live or music and
leaves. `/kristyliverevoke` removes the caller's saved Live permission. This code
is opt-in and disabled by default. No running phone deployment is implied.

## Before activation

- The owner must approve the member-visible notice/details and routine status
  text. Permission to build the feature is not a member's actual agreement.
- Keep the real allowlist/usernames and consent store private, OUTSIDE git.
- Channel gate mode comes from the private config `channel_gate`:
  `restricted` (default when the key is absent) keeps the original locked-VC
  requirement: default role must explicitly deny Connect AND View Channel, and
  explicit allows must be individual allowed members and this bot, not broad
  roles. `open` leaves an existing public VC's permissions completely
  untouched; enforcement is then the fixed allowlist at runtime. In BOTH modes
  every roster refresh, agreement, input and output re-checks that every human
  present is allowed and agreed, a non-allowlisted speaker's audio is zeroed
  and stops the session, and any join/leave during an active session stops it.
  Unresolved members/other bots are rejected. Administrators can bypass
  channel permissions and membership events arrive late, so zero frames heard
  by an unexpected listener cannot be guaranteed in either mode; in `open`
  mode that stop latency is the only barrier for walk-in listeners.
- The Gemini API Free age clause (https://ai.google.dev/gemini-api/terms)
  forbids API Clients directed towards or likely to be accessed by under-18s.
  The fixed adult-only allowlist, per-member 18+ assertion and
  stop-on-any-unapproved-presence are our mitigation of that clause in `open`
  mode, not a verified safe harbor; Google has published no guidance either
  way for an adult-gated feature inside a general-audience app.
- Each member independently asserts 18+ and permits Google processing and
  future sessions with the fixed allowed audience. IDs from the owner are NOT
  consent. These assertions are not independent age verification.
- Confirm the installed key's current Free-tier project. The flag is an owner
  assertion, not a billing lookup. No paid fallback/provider/model switch.
- Preserve the existing command ownership ledger and additive registration
  workflow. Never run bulk tree sync or silently take over command names.

## Once per member, not every session

First-use notice names Google/free-data handling, no private data/diary,
permission for future sessions with this fixed allowed group, and revocation.
Details shows the full fixed allowed audience IDs and processing scope.
The current roster is also listed. Each actor clicks for themselves.

A local POSIX-locked, atomic file with mode 0600 stores only Discord IDs, the
hashed permission scope and explicit adult/processing/future booleans. No
recordings, transcripts, source notes, user facts or persona are stored there.
A normal restart or different present subset of the SAME allowed group reuses
valid grants without asking again. A compact start status still gives Details,
Revoke and Stop. Anyone without a matching grant blocks all audio before start.

Revoke deletes that member's grant and stops an active session. The revoke slash
command works after the session notice has closed, even for a removed member in
the configured guild. A changed guild/bot/fixed allowlist/data-use notice version
invalidates grants. Seeing a changed scope clears old grants so reverting it does
not resurrect approval. Do not edit the store to manufacture consent. Material
processing changes require bumping the notice revision.

Any member join/leave during active voice still stops it. Restart with an already
approved subset reuses their grants. Unknown/unconsented people are never a reason
to process their microphone while waiting for agreement.

## Shared voice ownership

Live uses Music's existing CommandTree, Python gateway and per-guild lock plus
reservation. It reserves before consent; music slash/natural joins cannot race
into a second connection. Leave music before Live; no silent takeover. Live uses
the existing `/leavevckristy` stop flow and bot voice/disconnect/close hooks.
Unconfirmed leave retains a reservation and needs manual recovery before music.

The normal text bot already imports its text diary/persona during startup. The
precise boundary is that Live does not read, use or send those sources. Fresh
standalone Live-module imports do not load them either. The old manual terminal
group entrypoint is disabled: use the requested normal-bot slash workflow.
The existing full-persona/diary Free-source gate remains unchanged.

## Voice bounds

Same `gemini-3.1-flash-live-preview` / `Sulafat`. No diary/full local persona,
chat history, memory extraction/save, or audio/transcript files. Discord IDs
are validated locally and stripped from provider audio events. No claimed name,
secondary-account link or private-history recognition.

Eight concurrent humans is a conservative unmeasured resource cap, not a phone
capacity guarantee; allowlists may be longer. Each input Opus decoder has an
isolated Node worker-thread heap and bounded 48-packet queue. Human overlap stops
rather than merging speakers. Another member can interrupt Kristy's reply.
Use earphones. At 600 seconds only an already pending/playing reply may finish;
new speech during that final reply stops/leaves. Input/idle/provider/queue/error
stops remain. No reconnect or automatic model fallback.

Non-sensitive-only is a real restriction. A notice, prompt or allowlist cannot
prevent somebody from speaking private material into a live mic. Google Free
inputs/replies may improve products and be human-reviewed. Do not discuss
personal/confidential/sensitive information.

## Private setup, pending approval

Private config schema 1: `guild_id`, `owner_id`, `voice_channel_id` (may be null
for slash, which uses the caller's actual VC),
`notice_revision: group-free-future-v2`, `notice_approved: false`,
`maximum_concurrent_humans: 8`, `members: [{user_id, label}]`,
`source_message_id`, `consents: []`. Optional `channel_gate` is `restricted`
(locked VC, the default) or `open` (existing public VC untouched, runtime
allowlist gating only). Switching `channel_gate` changes the hashed permission
scope, so saved member agreements are invalidated and re-asked. Labels are
inert references, not identity
matching. Never prepopulate consents. The separate local consent store is derived
from this private config path plus `.consent.json` and only real clicks write it.

Existing `MUSIC_ENABLED=1`, opt-in `KRISTY_LIVE_ENABLED=1`, private config path in
`KRISTY_LIVE_PRIVATE_CONFIG`, owner-reviewed copy via
`KRISTY_LIVE_NOTICE_APPROVED=1`, current-key assertion via
`KRISTY_LIVE_CONFIRMED_FREE_TIER=1` are required. Operational flags do not create
permission. Existing `MUSIC_SYNC_COMMANDS` and test-guild additive registration
apply, with the ownership ledger preserved. No sync occurs on import.

## Verification

Offline/no-network tests cover consent persistence/restart/subsets/revocation,
scope changes without resurrection, corruption/save failures, private file modes,
actor binding, join/revoke/output races, shared music reservations and slash leave.
Actual pinned receiver, Opus codec and AudioPlayer use encrypted synthetic RTP
and a fake gateway for second-member interruption, stale-output rejection,
deadline drain, unknown speaker, DAVE loss and manual stop.

No real Discord/Google session, phone performance test or verified live button
rendering is implied. Live UI, actual permissions and model access still require
preflight when activation is approved.

Sources: https://ai.google.dev/gemini-api/terms and
https://ai.google.dev/gemini-api/docs/pricing
