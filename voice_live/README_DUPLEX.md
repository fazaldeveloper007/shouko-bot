# Requester-only local-gated native voice, with interruption

This is a separate manual developer prototype, not bot.py integration or general server voice. The existing one-turn `run.py` and tested alternating `session.py` remain available with their previous behavior. This new `duplex.py` uses the owner's approved **gemini-3.1-flash-live-preview / Sulafat**, **10 minutes from START, then finish only a reply already pending or playing**, with local speech gating and interruption. No other model, provider, voice, billing setting, credential or normal text/music/persona/diary behavior is changed.

## Consent and data use

Only the confirmed adult requester and Kristy may be in the otherwise empty access-restricted normal VC. Stop the normal bot first. This mode continuously decodes **only the requester's** Discord receive stream locally, including during Kristy's reply, so it can detect a barge-in. It does not subscribe to other people. Any other occupant, requester movement, foreign-speaking metadata or DAVE failure stops the session. Membership events can be delayed, so do not promise nobody joining can hear even a fragment before stop.

Local idle audio is not uploaded. An energy-based detector opens an utterance after three consecutive20ms frames above fixed RMS/peak thresholds. Up to200ms of local onset pre-roll and the detected utterance, including its short within-utterance pauses, go to Google. Roughly800ms without detected speech ends the utterance. This is an energy gate, **not** proof that a human is speaking: noise can be uploaded as speech; quiet/whispered speech can be missed. Short natural pauses can still split an utterance. No echo-cancellation guarantee is made. **Use earphones**, because Kristy's audio from the phone speaker may re-enter your mic, be detected as speech and interrupt her.

Google's unpaid-service inputs/replies may be used for product improvement and human review. Keep the **whole session** non-sensitive. Only this new session and a generic AI-identifying brief Hinglish prompt form its context. No old chats, persona.py, diaries, saved memories, transcripts, recordings, tools, private creator details or past-session handles are sent. The provider can retain service data under its terms; closing the socket is not a promise of deletion there. Locally no audio/transcript/history files or memory entries are written. PCM/codec/filter queues are bounded and cleared on cancellation/exit; immutable transport strings/native allocators do not promise forensic zeroization.

The existing current-key Free-tier confirmation gate remains: `--confirmed-free-tier` records the owner's assertion about the key actually installed on this phone, not a technical billing lookup. Do not use an old account's project as proof. No key creation/reset/replacement, paid key linking, billing enablement or paid fallback. Stop if tier is uncertain or becomes paid. Google's API adult/developer/audience terms still apply; this does not authorize gaming-server rollout. See [terms](https://ai.google.dev/gemini-api/terms).

## Run

Stop the normal bot using its usual method and join the restricted VC alone. Preserve local persona edits; on a pull conflict stop, do not reset/stash blindly.

```sh
cd ~/shouko-bot
git pull --ff-only
python voice_live/duplex.py --user-id YOUR_CONFIRMED_DISCORD_USER_ID --confirmed-free-tier
```

At the prompt verify requester/guild/channel,3.1/Sulafat, new local monitoring and data-use terms. Type START. `LIVE_LISTENING` means the local gate is ready. Speak naturally, with a normal voice and earphones. `LIVE_USER_SPEECH` means local onset was detected, not proof Google understood it. `LIVE_PLAYING` indicates the player started, not a heard-speech receipt. After a reply `LIVE_WAITING` indicates readiness without a fixed8-second listening window.

When requester speech is detected during a reply, local playback and all queued old reply frames are cleared immediately by the worker. That does not retract sound already transmitted or guarantee instantaneous provider/network interruption. The input path sends manual activityStart/audio/activityEnd. One dedicated Google reader handles interrupted/generationComplete/turnComplete concurrently, and old cancelled chunks are suppressed until the old turn terminates. New output is epoch-tagged; stale local chunks cannot start another player. A delayed/ambiguous cancellation fails closed rather than guessing which reply an audio chunk belongs to. No automatic reconnect/resumption/fallback/retry. The model retains the portion already generated/sent in its own session context, which need not exactly match what the requester heard before cancellation. Do not claim perfect heard-only conversational memory.

## Bounds, idle and stop

- **600 seconds from START**, including setup/wait/speech/replies, then finish only a reply already pending/playing. The Node worker independently enforces this boundary from init. No new utterance or reply starts after it. Speaking during this final reply stops playback and leaves instead of opening another turn. An utterance still being spoken at the boundary does not extend the session. Manual stop, safety changes, provider/transport errors and no-progress timeouts still stop immediately. Neither idle nor interruption resets the deadline. Cleanup/leave confirmation can take longer after audio stops.
- An utterance ends on a natural local pause (~800ms), with a **60-second/1,920,000-byte input ceiling**, including onset pre-roll. Hitting a hard input bound ends the session, rather than pretending all speech was heard.
- No per-reply duration, PCM-byte or application output-token cap is imposed. Answers continue until the provider ends them or the requester interrupts. At the 600-second session boundary only the current pending/playing reply may finish. Queues remain bounded to 30,000 Opus frames (600 seconds), and IPC backpressure fails closed. Session aggregate upload remains bounded to19.2MB. Output total is counted, not capped to a reply duration; the bounded frame queue remains a memory-safety stop.
- **60 seconds of requester inactivity causes IDLE_LIMIT**, excluding time the Discord player is actually playing Kristy's reply. Detected requester speech resets that idle budget. Waiting for Google while not playing counts toward inactivity. No-requester packets or digital silence no longer cause an8-second exit. Detection/noise caveats apply to this timer too.
- A pending response with no provider progress is bounded by an8-second receive-idle timer. After generation ends, up to12seconds is allowed for the separate turnComplete signal while playback drains. Interrupted-generation terminal state has a4-second fail-closed wait. These are client safety bounds, not Free-tier allowances or latency guarantees.
- STOP + Enter, **any terminal input after START**, or Ctrl+C ends immediately. Membership/DAVE/transport/format/queue/provider/API error also ends. `GoAway`, quota or connection expiry stops without reconnect. An early end is possible even when the600-second boundary has not been reached.

`SESSION_LIMIT`, `IDLE_LIMIT`, `INPUT_LIMIT` explain the corresponding limit; `MANUAL_STOP` is user stop. `GOOGLE_INTERRUPTION_TIMEOUT` or `GOOGLE_UNSOLICITED_OUTPUT` means the safe protocol could not associate a provider response with the expected utterance; it is not proof the requester caused anything wrong. `GOOGLE_AUDIO_TOO_SHORT` and `GOOGLE_AUDIO_SILENT` are not success. A cancelled reply is not counted as completed. `turnsCompleted`, output frames and nonzero sample counts are local pipeline metrics, not proof of relevance or audibility.

Check **LEAVE_CONFIRMED** before restarting the normal bot by its usual method. For LEAVE_UNCONFIRMED manually check/disconnect first. Share only `LIVE_ERROR`, `LIVE_RESULT`, leave-status and a short quality report, not raw logs, keys, crypto debug, audio or transcripts. Do not automatically rerun errors.

## Verification and limits of proof

Offline tests use fake Google sockets and the actual pinned Discord VoiceReceiver/AudioPlayer/Opus with synthetic encrypted RTP. They cover concurrent input while reply is buffered/playing, cancellation and stale chunks, next reply after interruption, late provider control barriers, rapid interruption, silence/no packets, onset pre-roll, longer-than8second input,60second utterance/idle bounds, idle pause during playback, STOP/DAVE/foreign speaker/session limits and replies beyond the old 10-second limit, tiny/silent output, no idle uploads/empty Google turns and cleanup. Real cancellation latency, acoustic echo, noisy/quiet speech quality and phone barge-in remain unverified until an authorized live run. Earlier3.1/Sulafat12-turn alternating success does not verify this new full-duplex path.

```sh
python -m pytest tests/test_voice_live*.py tests/test_voice_probe*.py -q
```

[Live guide](https://ai.google.dev/gemini-api/docs/live-guide) documents manual VAD/interruption and500–800ms silence guidance. [API reference](https://ai.google.dev/api/live) defines activityHandling and interruption/turn-complete ordering. [Session management](https://ai.google.dev/gemini-api/docs/live-session) describes approximate connection/session lifetimes, not a daily free-minutes allowance. [Pricing](https://ai.google.dev/gemini-api/docs/pricing) lists Free-tier rates; actual project quota/capacity is not guaranteed.3.1 is a legacy preview, not a production reliability promise.
