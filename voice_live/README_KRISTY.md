# Kristy natural Live voice with shared own-ID memory

This is a manual voice entrypoint, not always-on listening or general server
rollout. It uses natural `duplex.py`, not the older 8-second alternating runner.
Voice remains Gemini 3.1 Flash Live Preview / Sulafat, 60-second utterance cap,
no per-reply cutoff, 60-second idle excluding actual playback, and 600 seconds (then finish the already pending reply only)
from START. The normal text model and local `persona.py` are unchanged.

The owner reported a successful natural-voice conversation and speaking
interruption after the receive-reporting update. The earlier intermittent
receive-error cause is not established, and the reporting change is not claimed
as its root-cause fix. Fixed receive-stage diagnostics remain available. No
automatic retry/fallback is added. These new persona, diary and reply-duration
changes are offline-tested, not a new successful phone run.

## Privacy and review gates

Only the confirmed adult requester and Kristy may occupy the restricted VC.
Keep the whole session non-sensitive and use earphones. Existing receiver,
foreign-speaker, membership and DAVE fail-closed controls remain unchanged.
Membership changes can be delayed; stop is not proof that no joined person
could hear a fragment. No owner guard removal, autojoin or background listening.

1. For the explicitly approved current owner/guild, the dedicated Kristy entrypoint automatically reads that speaker's existing canonical diary. No per-note picker is required. An explicit --memory-selection retains a narrower legacy review route. Automatic read access is bound to a separate generation record and stops after forget/revoke.
   Only facts from that requester's canonical diary are used in automatic mode,
   bound to their Discord ID, guild and forget generation. No other member
   diary, group history, recap or Kristy self-diary lessons are imported. Names
   or IDs spoken to the model cannot choose a diary file. Facts are possibly
   outdated data, never instructions or permission to act.
2. The full local text persona and selected notes go to Google along with detected
   requester audio. The same Live session returns input transcription. Bounded
   temporary text is held in memory, not raw audio/full-transcript files.
3. At an ordinary safe session end only, one extra Google Gemini 3.5 Flash-Lite
   text request selects useful own-user notes. It processes temporary input
   segments. No per-reply extraction, local ML, search, Groq or paid fallback.
   A setup rejection, quota/error, unsafe ending, overflow or bad extraction
   means no new note save. Crash/force-stop can lose unsaved notes.
4. New proposed notes appear **privately in the local Termux terminal after
   voice has ended**. They are not diagnostics to forward. Type `SAVE` to save
   that entire shown selection; another answer or 120-second timeout discards
   it. For partial selection, discard and make a separate explicit text-chat
   save request. No automatic new-note write and no claim of perfect semantic
   sensitive-information detection. Candidate review is required because speech
   recognition and extraction can be wrong. The terminal review is not a full
   transcript log, but its scrollback can contain the displayed proposed notes.
5. Save is confirmed only after locked merge and readback succeed. The existing
   shared member diary keeps 40 short notes, prunes oldest and merges similar
   wording. New confirmed voice notes are eligible in later voice sessions and
   ordinary text chat. New or reworded TEXT notes need another private selection
   preview before voice use; old digest approval does not cover replacement
   content. Revocation/forget invalidates in-flight writes and voice context.

Google's Free-tier data-use/product-improvement and human-review terms still
apply. Selected notes and temporary text are provider processing, not a promise
of provider deletion when a socket closes. The current-key Free-tier assertion
is retained, not a billing lookup or unlimited allowance. No paid billing or
fallback is enabled. Normal text-model selection is unchanged.

## Local preview workflow, not a phone test instruction

Do not run until the intended private-review workflow is accepted. These
commands are reference mechanics; they are not evidence the receive blocker is
fixed. Keep the normal bot stopped before any eventual voice session. Preserve
local persona edits and stop on pull conflicts, never reset/stash blindly.

The review utility uses the existing `MEMORY_DIR` after `.env` loading. No keys
are needed. Substitute the already verified Discord ID and guild, not a
username. In a private local terminal, run:

```sh
python -m voice_live.memory_review pick --user-id YOUR_ID --guild-id YOUR_GUILD
```

This is a numbered local picker, no JSON or digest editing needed. It displays
only that member's existing diary notes. Enter chosen numbers such as `1 3 5`,
or `NONE` for an explicitly empty selection. Nothing is selected by default.
Then type `CONFIRM` only after checking the shown choice numbers. Empty input,
`CANCEL`, another confirmation answer or EOF leaves prior selection unchanged.
Bad/duplicate/out-of-range choices fail without writing. Choose only your
non-sensitive self-facts; exclude secrets and other people's private facts.

The picker requires an interactive terminal and warns not to screenshot/share
its private screen. Its scrollback can contain note text. No telemetry, Google,
VC, audio or canonical-diary write occurs. It stores only selected exact fact
digests in a0600 file under the existing gitignored memory directory:
`voice-selection-YOUR_ID-YOUR_GUILD.json`. An optional `--selection-file` can
choose another private location, but never put it in a shared repository.
No plaintext fact-preview file is created by `pick`.

`MEMORY_SELECTION_READY count=N` confirms local eligibility, not transmission or
voice start. `MEMORY_NO_EXISTING_NOTES` leaves selection unchanged. If an empty
diary is unexpected, check the configured memory directory instead of assuming
notes were deleted. A note edited/deleted or a forget generation changed during
review invalidates the selection rather than approving a replacement. Preferred
name is not silently imported; it can be supplied as an ordinary reviewed fact.
Legacy `preview`/`approve` JSON export remains available but is not needed for
the normal numbered flow.

The later gated voice command is `python voice_live/kristy.py` with required
`--user-id`, `--guild-id` and `--confirmed-free-tier` flags. The reviewed selection
file defaults to the same canonical diary directory; `--memory-selection` may
explicitly select its private path. A missing own-user diary fails clearly rather
than creating a blank replacement.
It does not run on import. The existing generic developer runners remain
available, never an automatic fallback from this entrypoint.

To invalidate voice access without deleting member notes:

```sh
python -m voice_live.memory_review revoke --user-id YOUR_ID --guild-id YOUR_GUILD
```

Revoke increments a shared generation. A running memory-enabled voice session
checks revocation/generation about once per second and stops on mismatch.
That cadence is not an instantaneous-deletion promise. End-of-session writes
also check generation under lock. Normal text `forget me` uses the same locked
store and reports failure instead of claiming a failed delete succeeded.

## Performance and observable results

Standard library only for memory, no local model/embeddings/vector store/new
packages. At most 40 facts and 6,400 context characters. Temporary input has a
24,000-character and 120-segment bound; overflow disables extraction, not a
silent partial save. Character limits are not measured Python heap usage.
Clearing references is not forensic zeroization of immutable strings/allocators.

Input transcription is independent of response/control events without guaranteed
ordering. Received input fragments are separate source segments, NOT local
utterance/response epoch IDs. Output/interim transcripts are ignored. No
chronology is invented to resolve conflicting statements. Offset-bearing or
malformed input currently disables note extraction rather than guessing.
A candidate must contain an exact source quote; code checks obvious sensitive,
third-party, quoted and instruction-like content, and private SAVE review is
still required. This can skip useful notes; no perfect multilingual classifier
is claimed.

`LIVE_TIMINGS` reports count/median/max for input-end-to-first-audio,
first-audio-to-player-start and input-end-to-player-start, using local monotonic
timestamps. These omit the local speech-end silence threshold and do not isolate
network versus model time. No transcript/audio in timing metrics. Real phone
latency is unmeasured, no zero-overhead/Snapdragon 680 performance promise.

`LIVE_MEMORY` uses fixed status and counts. `MEMORY_SAVED` means diary write and
readback completed. `MEMORY_NOT_SAVED_REVIEW` means discarded/unconfirmed.
`MEMORY_SAVED_ACCESS_UPDATE_FAILED` means diary saved but next-voice selection
update failed; rerun private selection, do not duplicate the save blindly.
Errors are not success, and receive failures do not trigger end-session saves.

## Verification

Synthetic tests cover exact selection, scope mismatch, new source processing,
combined audio/transcript event preservation, one-call budget, bounded memory,
revocation, stale extraction/forget, concurrent writers, corrupt data, save and
delete failure, review confirmation, next-session selection, cancellation and
safe/unsafe runner shutdown. Existing actual Node voice tests are rerun. No live
provider request, private-note import, phone deployment or receive repair is
proved by them.

Sources:
- https://ai.google.dev/api/live
- https://ai.google.dev/gemini-api/docs/live-guide
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/terms


### Receive failure reporting

Natural duplex still stops and leaves on a receive error. It now adds only
`receiveStage` and `errorClass` fixed labels to `LIVE_RESULT`, never exception
text, stack, packet bytes, audio, tokens or keys. `TRANSPORT_DECRYPT`,
`RTP_PARSE`, `DAVE_DECRYPT`, and `STREAM_OTHER` identify where the original
exception escaped, not why it happened. `CONTRACT_MISMATCH` or
`DIAGNOSTIC_CONTRACT_MISMATCH` means the pinned-library wrapper could not
be safely installed or used, and the session stops.

This is reporting, not a receive fix. Original parsing/decryption and DAVE
failure tolerance are unchanged. Instance methods are restored on exit; the
current DAVE decrypt method is wrapped only during its synchronous parse call
and restored in `finally`. Offline synthetic tests are not a successful phone
or provider test. No new live retry is implied by this change.


The Kristy entrypoint now reads the full local `persona.py` SYSTEM_PROMPT as a
string without executing it or importing bot.py. Local persona edits stay local,
and the startup START gate discloses that the persona goes to Google. The voice
overlay limits claims to actual available voice capabilities and supplied
context. Reviewed own-member diary selection remains required. This does not
automatically import channel recaps, server history or other members' diaries.

Storage matches text bot's import-time `diary.MEMORY_DIR`, from the hosting
process environment or the repository's default memory directory, before dotenv
loading. Voice and the picker do not override this with a later .env-only value.
Use the same launch environment as text bot. Missing canonical own-ID files stop
with `MEMORY_EXISTING_DIARY_NOT_FOUND`, not a new empty diary.


### Automatic own-ID diary mode

The owner explicitly chose normal-text-like access to their own existing diary
instead of selecting notes one by one. The dedicated entrypoint defaults to this
approved owner/guild scope. It ignores the old one-note picker selection for this
mode; it does not delete or edit it. It reads the same existing phone file, not
other member files, channel recaps or old chats. Diary context goes to Google.
All stored facts in this bounded own-ID diary are eligible at session start,
including future text-saved notes. No automatic semantic privacy filter is
claimed. Keep this diary/session non-sensitive; stored claims may be wrong or
about someone mentioned and are not proof of who is speaking. Discord ID binds
the speaker. The preferred-name metadata is not silently promoted into a fact.

The private scope file stores owner/guild/generation/mode only, not note text.
Forget/revoke invalidates it and never silently reauthorizes it. A previous
revocation before first use also fails closed. New voice-note writes still need
the separate private end-session SAVE confirmation. No per-note read approval
is requested on ordinary runs. Other owners/guilds are not granted access.
