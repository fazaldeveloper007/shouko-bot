# Finite requester-only native voice prototype

This is a manually started developer prototype in a normal Discord VC, not a feature enabled in bot.py. Only the requester and Kristy may be present. It makes one native Gemini audio round trip, then leaves. It is not general server voice chat, continuous listening, or a replacement for the existing text/persona/music behavior.

## Approval and billing gate

Before starting, the owner must confirm that the **current existing Gemini key installed on the phone** uses AI Studio **Free tier**. Check the existing key's actual project and current tier in the Google account where that key was created, not an older account/project. Do not create, copy into chat, reset or replace keys; do not enable billing, buy credits or enable a paid fallback. If the current key's tier is uncertain, stop. A similar project name, a different account's dashboard or a successful request does not prove this key is free.

The runner refuses before reading credentials or connecting unless `--confirmed-free-tier` is supplied. This parameter records the owner's confirmation about the CURRENT installed key. It is **not** an automatic billing lookup and must not be used to bypass uncertainty. No billing changes are made. Stop if the current project becomes paid, access is denied or quota is exhausted. There is no reconnect, alternate key, model/provider fallback or automatic retry. A free-project dashboard entry in another Google account does not prove this installed key has model access; a setup rejection stops without fallback.

The default model stays `gemini-3.8-live`. The separately approved bounded comparison can explicitly select `--model gemini-3.1-flash-live-preview` for one run. No other model is allowed, and no failure changes models automatically. Both use built-in voice `Kore` and Google's native audio Live WebSocket endpoint. 3.1 is a legacy preview, not a proven fix or reliability guarantee; its access on the current key is unverified. The working text model is unchanged. The minimal test prompt asks for one short friendly Hinglish reply, identifies Kristy as an AI prototype and asks for no personal information. It imports no persona.py, member diaries, chat history or private creator details.

## Data use and audience

This test sends the requester's microphone PCM and the generic test prompt to Google. Generated audio is played into the same VC. Google unpaid-service inputs and responses may be used for product/model improvement and processed by human reviewers. Use only a short non-sensitive test sentence. Do not discuss private, sensitive or personal information. The local runner writes no audio files, transcript, conversation log or memory entry, but that does not mean Google retains nothing. Credentials stay in Python and are sent only to their intended services; the Node worker gets no credential environment.

Google's current API terms require adult use and prohibit API clients directed toward or likely accessed by under-18s. They describe developer professional/business use, not consumer use. This narrow adult development prototype does not clear a general gaming-server rollout. The owner must resolve intended-use and audience eligibility before broader access. See [Google terms](https://ai.google.dev/gemini-api/terms).

## Bounds and stop

- Explicit terminal START, after showing requester/guild/channel, model, voice and data-use terms. No Google connection or VC join before START.
- At most **8 seconds of requester capture**, independently capped at 256000 bytes of 16kHz mono PCM. Only the requester's Discord receive stream is subscribed.
- One reply, at most **10 seconds of generated audio** by both PCM byte count and playback timer. There is no second listen/reply turn, barge-in loop or tool use. Stop interrupts the reply.
- **30-second overall deadline from START**, including VC join and Google setup. Slow setup/generation can end the test before a full reply. Audio work stops at the deadline; bounded disconnect/cleanup can take longer to confirm leave.
- STOP + Enter, any terminal input after START, Ctrl+C, requester leaving/moving, any other occupant, foreign-speaking metadata, DAVE loss, stream/IPC/API error or timeout stops the test and clears local queues. Other users never get an audio receive subscription.
- Membership events are not instantaneous. Use an access-restricted, otherwise empty test VC. Do not promise that someone entering during output can hear absolutely nothing before the membership event stops playback.
- DAVE stays required. Unlike the local receive-only probe, this separately approved output prototype intentionally joins **unmuted**, because it must speak. It does not change server mute settings or weaken encryption.

PCM is filtered/resampled from Discord's 48kHz stereo to 16kHz mono for input. Google 24kHz mono PCM is filtered/resampled to 48kHz stereo and Opus-encoded for Discord. No ffmpeg, external TTS service or transcription cascade is used. Buffers, codecs and filters are cleared/released on exit; language/native allocators and immutable transport strings cannot promise forensic zeroization.

## Run only after the current-key Free-tier confirmation is confirmed

Stop the normal bot with its usual method. Join the otherwise empty restricted normal VC from the confirmed requester's own Discord account. Preserve local persona edits; if pulling conflicts, stop rather than resetting/stashing blindly.

```sh
cd ~/shouko-bot
git pull --ff-only
python voice_live/run.py --user-id YOUR_CONFIRMED_DISCORD_USER_ID --confirmed-free-tier
```

The explicitly approved 3.1 comparison uses the same steps, replacing the final command with:

```sh
python voice_live/run.py --user-id YOUR_CONFIRMED_DISCORD_USER_ID --confirmed-free-tier --model gemini-3.1-flash-live-preview
```

Check the printed model is `gemini-3.1-flash-live-preview` before START. This does not change any saved model, key, billing setting or the normal bot. If setup/access/quota fails, stop without a retry, key change or fallback. Free-tier listing is not proof of this key's tier or access. The same data-use terms, requester restriction and 8s/10s/30s limits apply.

The already-installed pinned voice_probe dependencies are reused. No npm reinstall or new SDK/key is required. Python uses aiohttp, already required by discord.py. Existing normal bot env/nearest-ancestor .env credentials are used, with the same strictly literal documented Termux boot-file fallback; boot scripts are never executed/sourced. Missing or ambiguous existing credentials stop locally without printing or creating them.

At the actual prompt, check IDs and type START. Speak **only after LIVE_LISTENING**, one non-sensitive line such as asking for a short greeting. After the 8-second input window, LIVE_REPLY_PENDING means requester capture is stopped. LIVE_PLAYING means the Discord player began handling generated audio, not proof the requester heard intelligible speech. Do not rerun automatically on failure.

Share only `LIVE_ERROR`, `LIVE_RESULT` and leave-status lines, never .env, keys, raw API/crypto logs or audio. `LEAVE_UNCONFIRMED` means manually check/disconnect Kristy before restarting the normal bot using its usual command.

`COMPLETE` means the bounded worker processed the one-turn output and reached player idle. `OUTPUT_LIMIT` means a configured output cap ended it, so the reply may be cut short. Numeric outputFramesRead counts frames read by the player pipeline, not a receipt proving they were heard. Only the requester's actual report establishes audibility/intelligibility. Neither success guarantees ongoing conversational latency or production reliability. `OVERALL_LIMIT` can simply mean this bounded trial ran out of time; do not silently extend it or repeat.

## Offline verification

```sh
python -m pytest tests/test_voice_live*.py tests/test_voice_probe*.py -q
```

Tests use the actual pinned VoiceReceiver/AudioReceiveStream, synthetic encrypted RTP, real opusscript encode/decode and actual Discord AudioPlayer scheduling with a synthetic connection. They verify input conversion, anti-alias rejection, streaming chunk invariance, output encode/decode, single-requester subscription, manual/foreign-speaker/DAVE/deadline stops, cleanup ordering, fixed protocol/setup, malformed events, full-part processing, byte caps, no-key-before-Free-tier-confirmation gate, real discord.py channel selection and header auth without a key-bearing URL. Google sockets are fakes. DAVE session cryptography/network/phone compatibility, Google model access, microphone intelligibility and real Discord playback remain live-unverified until one authorized run. No cloud call is made by tests.

## References checked for this prototype

- [Live API reference](https://ai.google.dev/api/live), WSS setup, manual activity boundaries and realtime audio.
- [Live guide](https://ai.google.dev/gemini-api/docs/live-guide), audio formats and 3.8 model-specific settings. 3.8 does not accept thinkingLevel or disabling proactive audio; neither flag is sent.
- [3.1 model page](https://aistudio.google.com/docs/models/gemini-3.1-flash-live-preview), legacy native audio preview selected only by an explicit comparison flag.
- [Pricing](https://ai.google.dev/gemini-api/docs/pricing), Free-tier availability does not prove this installed key's tier.
- [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), project-scoped limits/capacity are not unlimited guarantees.
- [Google terms](https://ai.google.dev/gemini-api/terms), unpaid data use and audience restrictions.

## Response lifecycle and failure outcomes

The client ends this fixed non-background-reasoning single turn on either `generationComplete` or `turnComplete`, after processing all audio parts in that event. It does not wait for a delayed turnComplete after generation has already finished. This repairs a client lifecycle bug, not a claim that Gemini will produce audible speech.

`GOOGLE_RESPONSE_IDLE_TIMEOUT` means no Google event arrived within the existing 8-second per-event receive limit. It is distinct from the unchanged 30-second `OVERALL_LIMIT`. `GOOGLE_AUDIO_TOO_SHORT` means less than one 20ms source frame arrived; no synthetic padding is used to turn a single sample into a claimed voice response. `GOOGLE_AUDIO_SILENT` means all returned PCM samples were zero. Neither is success. `outputNonzeroSamples` is an aggregate metric, not proof of intelligibility. When an upstream error or outer stop caused cleanup, LIVE_RESULT preserves it in `code`; `workerCode` records a worker cleanup STOPPED separately.

The d77b15c phone run reached setup/listen and sent requester input but received only two PCM bytes and no playable Opus output. The exact provider-side reason remains unknown. Community reports describe similar tiny silent outputs for 3.8 Live, but do not establish this run's root cause or a verified workaround. These client fixes alone do not justify repeated diagnostic phone runs, model substitution or longer bounds. The owner separately approved one explicit 3.1 comparison under the same limits; the CLI option above enables only that choice, never automatic fallback. Real speech and current-key access remain unproven until that authorized run.

## Separately approved alternating session, maximum 10 minutes

The one-turn `run.py` above remains unchanged in behavior. A separate `session.py` now implements the owner's chosen maximum **600-second session** with **gemini-3.1-flash-live-preview / Sulafat**. Sulafat is his provisional voice choice, not a different model/provider. Normal bot.py, text, persona, music and diary behavior are not modified or imported. This is still a manually launched adult developer prototype, not a server voice feature or Discord command.

Stop the normal bot using its usual method, then join the otherwise empty access-restricted normal VC. Only the confirmed requester and Kristy may be present. Preserve local edits and stop on any pull conflict.

```sh
cd ~/shouko-bot
git pull --ff-only
python voice_live/session.py --user-id YOUR_CONFIRMED_DISCORD_USER_ID --confirmed-free-tier
```

Check requester/guild/channel, **3.1 / Sulafat** and data-use terms at the prompt, then type START. Each `LIVE_LISTENING turn=N` begins a new **8-second maximum** requester capture window. Speak only then. `LIVE_REPLY_PENDING` means capture has stopped. Kristy replies, at most **10 seconds**, then the next listening window opens automatically only after both Google's turn-complete signal and the local Discord player's idle state. This is alternating listen/reply, not barge-in: requester speech during a reply is not captured or queued. A short reply may finish before10 seconds. Replies are prompted to be brief. If you stay silent for a whole listening window, no requester packets ends the session rather than retrying.

The hard600-second limit starts at START and includes connection/setup, listening and replies. It can cut off the current utterance/reply. Audio work is stopped at the deadline; bounded connection cleanup/leave confirmation can take longer. The Node helper has its own600-second backstop from init. There is also a75-turn defensive ceiling, per-turn PCM/queue limits, and8-second response-idle checks. These are local safety bounds, not Google's Free-tier allowance. 600 seconds is a maximum, never a promise of uninterrupted time: Google connection lifetime is around10 minutes, and GoAway, quota/access errors, timeouts, invalid/tiny/silent output, output cap, DAVE loss, requester movement or any other occupant ends the whole session early. No automatic reconnect, session resumption, compression, retry, alternate key, paid model or fallback is configured.

The live model context contains only this new session's conversation and the generic AI-prototype instruction, with the same unpaid-service data use/human-review disclosure above. No old chats, diaries, saved memories, transcripts, previous session handles or persona file are sent. Locally there are no audio/transcript/history files or memory writes. Audio/codec/filter queues are cleared between turns; the Google socket is closed and local references dropped on exit. This does not promise deletion of Google's retained service data. Keep the entire session non-sensitive.

STOP + Enter, any terminal input after START, or Ctrl+C ends the session. A new turn never begins on `generationComplete` alone: the client first drains the separate `turnComplete` barrier, and the worker must separately signal completed playback. Turn IDs reject stale commands/events. If any output/time cap cuts off a reply, the session ends, so an unheard/truncated reply does not silently become context for a new user turn. An output cap is not a completed conversation turn.

`LIVE_TURN_COMPLETE` and aggregate `turnsCompleted` indicate bounded pipeline progress, not proof of audibility, relevance or model understanding. Only the requester's report can establish those. A normal deadline is `SESSION_LIMIT`; user stop is `MANUAL_STOP` (worker cleanup may say STOPPED separately). Request only `LIVE_ERROR`, `LIVE_RESULT`, leave-status and the requester's own quality report. Do not share raw logs, credentials, transcripts or audio. `LEAVE_UNCONFIRMED` requires checking/manually disconnecting before the usual normal-bot restart. Do not rerun automatically after a failure.

Offline tests cover two ordered turns over one fake Google socket, fixed3.1/Sulafat setup, provider completion versus playback barriers, actual pinned receive/Opus/player cycles, stale/overlapping commands, session and output deadlines, DAVE/foreign speaker stops, tiny/silent output, no-key-before-confirmation and cleanup. No test calls Google or Discord. The earlier3.1/Kore one-turn run was heard and confirmed clear/relevant by the owner; it does **not** live-verify Sulafat or this new multi-turn mode. No ten-minute uptime/latency guarantee is made. Session mechanics reference: [Live session management](https://ai.google.dev/gemini-api/docs/live-session).
