# Kristy

A sweet, affectionate Genshin Impact chat bot for your Discord server.
She chats when you @her, reply to her, or say her name, remembers the last few
messages in each channel, and throws GIFs for greetings, hype moments and gacha pain.

Built with Python + [discord.py](https://discordpy.readthedocs.io/) + the
[Gemini](https://aistudio.google.com) API (free tier, called directly over HTTPS - no SDK needed).
[Groq](https://console.groq.com) still works as an optional fallback brain.

## What she does

- Replies when someone **@mentions her**, **replies to her message**, or says "**kristy**"
- Remembers the last 20 messages per channel, so conversations feel continuous
- Sends a random GIF for: hi/hello, good morning, good night, hype/pog, sad, lol, and gacha/pulls/luck
- Has GIFs for **every playable Genshin character (124 total)**: ask "send a furina gif",
  or just mention a character by name and she may drop one (one per message, with a
  cooldown so it never spams). Short "NEUVILLETTE!!" hype gets a gif instantly, no AI call
- Adds an **emoji reaction** to genuinely shocking, dark, hilarious or wholesome messages -
  rarely, never on routine chat (needs the **Add Reactions** permission)
- Knows Genshin Impact (characters, banners, resin, artifacts, 50/50 pain) and talks like a player
- **Tags people by nickname**: teach her once ("Mayank ka username mayank_123") and anyone can
  then say "Mayank ko tag karo" - she resolves the name to the right member, and asks instead of
  guessing when a name is unclear
- **Calls people to VC from any channel**: "Kristy #gaming-chat pe Mayank aur Naiyo ko VC join
  karne bolo turant" posts the summon in that channel with their real @mentions


## Setup (one time)

1. **Discord developer portal** (https://discord.com/developers/applications):
   - Your application -> **Bot** -> copy the **token** (Reset Token if needed)
   - Same page, scroll down -> switch ON **Message Content Intent** (required!)
   - **OAuth2** -> scope `bot`, permissions `Send Messages` + `Read Message History`
     + `Add Reactions` -> open the generated URL to invite her to your server
     (already invited her without `Add Reactions`? No re-invite needed - that permission
     is on for everyone by default. Only if your server disabled it for @everyone,
     re-invite with the new URL or allow it for her role. Without it she just skips
     reacting and keeps working.)
2. **Gemini** (main brain): make a free API key at https://aistudio.google.com -> Get API key.
   (Optional fallback: a Groq key from https://console.groq.com - used if `GEMINI_API_KEY`
   is not set, or if the Gemini call itself fails, so a rate limit never leaves her speechless.)

## Run on Android with Termux (free, your spare phone)

Runs great on a spare Android phone plugged into the charger. Free forever, no card.

1. Install **Termux from F-Droid** (https://f-droid.org/en/packages/com.termux/).
   Do NOT use the Play Store version - it is outdated and broken.
2. In Termux, run:

```bash
pkg update && pkg install python git clang nano -y
git clone https://github.com/fazaldeveloper007/shouko-bot.git
cd shouko-bot
export AIOHTTP_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 FROZENLIST_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1
pip install -r requirements.txt
export DISCORD_TOKEN="your-bot-token"
export GEMINI_API_KEY="your-gemini-key"
termux-wake-lock
python bot.py
```

You should see `Kristy is online` in a few seconds. Keep the Termux
window open (you can switch apps, just don't swipe Termux away).

Notes:
- PyPI has no Android wheels for these packages, so the `NO_EXTENSIONS` exports
  above are required for the pip install.
- **clang** is needed to build `audioop-lts` on Python 3.14.
- `termux-wake-lock` is built into Termux; it does **not** need Termux:API.
- Android Settings -> Apps -> Termux -> Battery -> **Unrestricted** helps keep
  Android from killing the bot in the background.
- For autostart after a reboot, install **Termux:Boot** from F-Droid, open it
  once, then create `~/.termux/boot/start-shouko.sh` with:

```bash
#!/data/data/com.termux/files/usr/bin/sh
export AIOHTTP_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 FROZENLIST_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1
export DISCORD_TOKEN="your-bot-token"
export GEMINI_API_KEY="your-gemini-key"
cd ~/shouko-bot && python bot.py >> ~/shouko.log 2>&1
```

Then make the script executable:

```bash
chmod +x ~/.termux/boot/start-shouko.sh
```

Tip: instead of `export` lines you can also drop a `.env` file next to
`bot.py` (copy `.env.example` and fill in your two keys) - she reads it
automatically.

## Run locally (optional)

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your two keys
python bot.py
```

## Deploy with Docker (any Docker host)

The included `Dockerfile` works on any Docker host or container platform
(Railway, Fly.io, a VPS, etc.): set `DISCORD_TOKEN` and `GEMINI_API_KEY` as
environment variables and deploy.

## Nicknames and VC summons

Per-server nickname registry (`memory/aliases.json`, gitignored), separate from the diaries:

- Teach: "Kristy, yaad rakhna Mayank ka username mayank_123" (or "Mayank's username is mayank_123").
  The username must match exactly one current member, and a nickname can never point at someone
  else silently - retargeting needs "Mayank ko bhool ja" first. A nickname also can't steal
  another member's existing name.
- Teach your own: "call me Fazallette" / "mujhe Junaid bhai bulao".
- Forget: "Kristy, Mayank ko bhool ja".
- Tag: "Kristy, Mayank ko tag karo" works with taught nicknames, display names and usernames;
  ambiguous or unknown names get a question, never a guessed ping.
- Summon: mention ONE #channel natively and name the people:
  "Kristy #gaming-chat pe Mayank aur Naiyo aur Junaid ko VC join karne bolo turant".
  She posts "@Mayank @Naiyo @Junaid abhi ke abhi VC join kariye turant!" there (max 5 people,
  45s cooldown per member, never @everyone/@here/roles). If any name is unclear she asks first
  and sends nothing. She refuses channels she can't post in or you can't see, and never relays
  to another server.

## Memory

Kristy keeps a small private "diary" for every member: their preferred name, Genshin mains,
likes, running jokes and things they asked her to remember. She uses it naturally in replies.

- Each member's diary is one file, `memory/<discord_user_id>.json`, created automatically.
  The `memory/` folder is gitignored, so `git pull` never conflicts and diaries are never committed.
- She learns in the background after replying (never delays a reply). To save the free-tier quota
  she runs one extra small AI call per member roughly every 6 messages to her, or sooner when a
  message sounds personal ("my main is...", "remember..."), with cooldowns between calls.
- Tell her to remember something directly ("yaad rakhna...", "remember this", "likh lo") and she
  saves it right away, skipping the cooldown: the fact is extracted and written to the diary file,
  retried within a separate three-call budget for temporary failures, and she confirms in the channel
  ("likh liya 📔") only once it is actually saved. If every attempt fails she stays silent instead
  of lying, and retries the save on your next message.
- Each diary is capped at 40 short facts; the oldest are dropped when it is full.
- Say "forget me" (or "delete my diary") to her and that diary file is deleted.
- If a diary is missing or corrupt, she just chats normally. Set `MEMORY_DIR` to store diaries elsewhere.

## Web search (live answers)

When a message clearly asks about something current - next/upcoming banner, redeem codes,
maintenance, release dates, "search kar...", "pata kar...", weather, etc. - she searches the
web and answers from fresh results instead of guessing. Casual chatting never triggers a search.

- Searches go through **Tavily** (`SEARCH_PROVIDER=tavily`, the default): free, no credit card,
  plain HTTPS. It works **out of the box with no key** (Tavily's keyless mode). For higher,
  more reliable limits, make a free key at https://tavily.com (1,000 searches/month free,
  no card) and set `SEARCH_API_KEY`.
- The search results are summarized by her normal chat model in her normal voice - a search
  costs one Tavily call plus one ordinary chat-model call. No special grounded model needed
  (Google retired free-tier Gemini grounding, and gemini-2.5-flash is no longer served to
  new projects).
- A client-side cap (`SEARCH_MAX_PER_DAY`, default 25) limits search calls per day, resetting at
  UTC midnight. Past the cap she just answers normally.
- Fully fail-open: if the search call errors, is over quota, or returns nothing, she replies
  with her normal brain as if search never happened.

## Make her yours

- **Personality**: edit `persona.py` (it's plain English, no code)
- **GIFs**: add/remove URLs in `gifs.py`, or add new keyword triggers in `bot.py` (`GIF_TRIGGERS`)
- **Model**: set `GEMINI_MODEL` (default `gemini-3.5-flash-lite`; the request config adapts if a model
  rejects an option, so switching models can't silently break her). Only on the Groq fallback:
  `GROQ_MODEL` (default `openai/gpt-oss-120b`). Web search uses Tavily (see above)
- **Name**: set `BOT_NAME` and she responds to the new name instead

## Files

| File | What it's for |
|---|---|
| `bot.py` | all the logic: when to reply, memory, GIF triggers |
| `persona.py` | her personality (edit this to change it) |
| `diary.py` | per-member memory (diary files, merge, forget) |
| `gifs.py` | the GIF library (1037 URLs, every one verified working) |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | for Docker hosts (optional; Termux doesn't need it) |
| `.env.example` | which environment variables she needs |

## Proactive announcements

Kristy can post on her own schedule (all targets and wording approved by the server owner):

- **Good morning** - "Good morning @everyone" + a random GIF, daily at 09:00 (Asia/Kolkata) in the chat channel. If the bot is offline at 09:00 the day's post is skipped - no late catch-up pings.
- **New redeem codes** - checks game8's Genshin codes page hourly. A code posts only when it's freshly added per the source ("Date Added" within 2 days), was never seen before, and isn't in the known-expired history. Special Program drops post as one batched message. Community-sourced posts carry a source note. Old/uncertain codes are held, never pinged.
- **Limited-time events** - official HoYoLAB events feed (verified official accounts only), filtered to real events (no discussion rooms / giveaways / top-up promos), with the event's own official image when available.
- **Welcome message** - pings the actual new member (never @everyone) with a cute GIF when someone joins.

Setup notes: the bot role needs **Mention @everyone** permission in the announcement channels (Discord strips the ping silently otherwise), and welcomes need **Server Members Intent** enabled in the Discord developer portal. Config via env vars - see `.env.example`. State lives in `memory/announcements_state.json` (gitignored, append-only; a corrupted state disables posting rather than re-pinging old codes).

## Music candidate (off by default)

Local, not-yet-deployed music implementation is documented in [music/README.md](music/README.md).
It uses the existing Python gateway plus a token-free Node voice worker. Commands,
buttons, output and music-state awareness stay in the connected VC's built-in chat.
`MUSIC_ENABLED=0` and `MUSIC_SYNC_COMMANDS=0` keep playback and slash registration off.
Do not enable/install/register from this review build without the separate deployment decision.

## Temporary API failures

Each generation or search operation gets at most three HTTP attempts, including
the first. Provider fallback and Gemini config fixes share that same generation
budget. Successful distinct steps (initial reply, search, researched answer) get
separate budgets and are not counted as failed retries. The whole AI/search reply
phase has a 60-second deadline; each call is capped at 20 seconds or remaining time.
Temporary connection errors, timeouts and retryable server errors get a short
internal retry. Invalid keys, permanent requests and quota failures fail fast. A
provider Retry-After is never shortened: waits up to eight seconds are respected,
longer waits stop the retry. Only one final reply/fallback is sent. Message sends,
VC summons and other actions are not replayed. Background member-diary extraction
has its own independent three-call budget, no second outer retry loop; a failed
explicit save stays pending for the member's next message.

## Kristy's own review-first lesson diary

This is separate from member diaries. Each member, including the owner, still
has a maximum of 40 facts. Kristy's lesson file has at most **100 entries total**
across servers, pending and active combined. Entries are server-scoped: one
server's suggestions, reviews and style hints do not appear in another server.
At capacity new suggestions are refused, not silently evicted. Owner deletion
or a server reset frees space.

Any human member can suggest one of these fixed style IDs:

- `concise-answer`
- `explain-step-by-step`
- `avoid-repeating-joke`
- `admit-uncertainty`

Examples (say these to Kristy in a server):

```text
Kristy lesson suggest concise-answer
Kristy lesson list
Kristy lesson approve 1
Kristy lesson delete 1
Kristy lesson reset confirm
```

Only `suggest` is open to all members. List, approve, delete and reset require
the exact Discord user ID configured in `OWNER_ID` in `.env`. **No name fallback**
is accepted for these commands. If `OWNER_ID` is missing, suggestions can still
be saved, but nobody can activate or manage lessons until it is set. Use Discord
Developer Mode's Copy User ID on your own profile; no token or API key is needed
for this setting. This feature does not edit `.env` or infer the owner's ID.

Simple direct feedback also creates a pending suggestion, for example:
`Kristy jawab bahut lamba hai`, `Kristy steps mein samjhao`,
`Kristy wahi joke dobara mat sunao`, `Kristy pakka nahi ho toh guess mat karo`.
Other corrections are not automatically classified. Quotes, code, forwarded
snapshots, webhooks and bots cannot create lesson commands. No extra AI call is
needed. Even the owner's own suggestions need a separate approval command.

Each entry records the suggester's user ID, source channel/message IDs and time;
approval records the owner's ID and time. `lesson list` shows this server's
entries and attribution without pinging anyone. It posts in the current channel,
so use an owner-only channel when you want the review private. It does not
copy message text or private member facts into the self-diary. Duplicate lesson
IDs keep the original source instead of overwriting its attribution.

Pending suggestions never affect answers. After approval, only fixed,
code-owned style hints enter this server's subsequent AI replies. Arbitrary
free-text instructions cannot be taught. Lessons cannot change the persona,
permissions, tools, ping destinations or privacy rules. These are style hints,
not a guarantee that every generated reply will follow them perfectly.

Storage defaults to `memory/kristy-lessons.json` (inside `MEMORY_DIR` if set),
with an optional `SELF_DIARY_FILE` override. It is local and gitignored by the
normal memory directory rule. Writes use atomic replacement; read/write failure
does not claim success. `reset confirm` deletes only the current server's
self-diary entries, never member diaries, aliases or another server's lessons.

## Current VC members from text chat

Ask `Kristy VC mein kaun hai?` in a server text channel. Kristy reads your
current voice/stage channel's gateway snapshot and replies directly, without
asking the AI. If you are not in a VC, she asks which one. To select a different
VC, pick its native Discord #channel mention, for example
`Kristy #Gaming mein kaun hai?`. Exactly one voice/stage channel, from the same
server, is accepted. Unknown, text, inaccessible or multiple channels are not
guessed. The requester and Kristy must both be able to view the VC.

The reply displays current member names, labels bots, and disables all mentions.
It does not join a call, ping anyone or expose mute/deafen/streaming details.
An empty channel is reported as empty. Before startup is ready, while the guild
is unavailable, or after disconnect until ready/resume, it refuses to report an
old list. Unresolved voice-state IDs cause a retry-later message rather than a
partial or falsely empty list. Discord has no separate REST roster refresh here:
this is the gateway snapshot at handling time, not a guarantee nobody joins or
leaves just after it. Webhooks, bots, code, quotes and forwarded snapshots do not
trigger roster lookup. Music, summons and diary controls are unchanged.

If Kristy asks which VC, you can answer with only its native #mention in the
same text channel within two minutes, or reply directly to that clarification
with the mention. This pending question belongs only to that requester/server/
text channel. A reply to some other message does not select a VC for it; quoted,
forwarded or prose-only answers are not interpreted as roster selections. The
selection is consumed on lookup and the normal current access/readiness checks
run again. After expiry, ask the full question with the VC mention again.

Generation retries also validate HTTP 200 responses: missing/malformed/empty
Gemini or Groq text is retryable within the same three-call operation cap and
60-second response deadline. A reaction-only/unusable reply is not treated as
a successful answer; a valid model-search request still is a valid intermediate
step. Explicit provider safety/policy blocks stop without retry or alternate-
provider bypass. Permanent HTTP failures and quota/long Retry-After limits still
fail fast. Local history/setup errors are not blindly replayed.

Termux logs now show safe provider labels, actual call `attempt=N/3`, HTTP status
and fixed reason codes such as `empty_output`, `malformed_output` or `blocked`,
without provider response bodies, request headers or keys. These logs distinguish
silent retries from an immediate non-retryable stop. Only one final Discord
answer/fallback is sent; screenshot timestamps alone cannot count attempts.

## Preview-confirmed reminders and timed messages

One-shot only, for every human member. Examples (the `|` separators are required):

```text
Kristy schedule 10 minute baad | here | Mayank, Naiyo | Match shuru karte hain
Kristy schedule kal 8 PM | #gaming-chat | @Mayank, @Naiyo | VC join kar lo
Kristy remind 10 minute baad | here | Paani pi lena
Kristy summon kal 8 PM | #gaming-chat | Mayank, Naiyo
```

Pick native Discord channel/member mentions where shown. `here` explicitly means
this text channel. The destination may be another ordinary text channel in this
server, never another server. Targets can be known aliases/exact usernames or
native member mentions, separated by commas or `aur`/`and`. Input order is kept;
duplicates are removed. Maximum five human targets. `none` permits a no-ping
message. `remind` pings only its creator. `summon` uses this fixed previewed text:
`VC join kar lo, creator ne is time par bulaya hai.` It does not join or move
anyone, infer a future VC, repeat pings or escalate them.

The outgoing body is the user's exact text, not an AI rewrite. Put message text
after the last separator. Payloads must be 1-1000 characters and cannot contain
Discord mention markup, @everyone/@here, or bot tag/search control markers. Tags
come only from the target field; role/everyone/here/reply pings are disabled.

Nothing is armed immediately. Kristy saves a **preview** showing the exact
outgoing text, stable member IDs in mention form (without pinging), destination
name/ID and full weekday/date/time in IST (Asia/Kolkata). Within two minutes,
the same creator confirms from the source text channel:

```text
Kristy schedule confirm PREVIEW_ID
Kristy schedule list
Kristy schedule cancel JOB_ID
```

Confirm/list/cancel use the real returned 10-character ID, not the words above.
Only the creator can confirm. Members list/cancel their own same-server jobs;
the configured exact `OWNER_ID` can list/cancel others in that server, never
confirm as them. No display-name fallback. Lists show own same-server job status/time/reason even after access or target
loss, without destination IDs, target IDs or private bodies.

Supported time formats:

- `10 minute baad`, `2 hour baad` (relative to the original message timestamp)
- `aaj 9:30 PM`, `today 9 PM`, `kal 8 AM`, `tomorrow 8 AM`
- `2026-11-03 20:30` (explicit date and 24-hour clock)

Bare `8 baje`, passed times and unclear dates are not guessed. Between midnight
and 4 AM, use an explicit date instead of `kal`/`tomorrow`. The preview is the
final authority for the resolved date/time. Jobs must be 1 minute to 30 days
in the future. Limits include previews: 10 active per member/server, 50 per
server and 1000 total; last 200 terminal jobs are retained. This is a bounded
command grammar, not unrestricted natural-language scheduling.

Both creation/confirmation and delivery recheck the same-server destination,
creator membership and destination view/send access for creator and bot. Every
target must still be a verified human member. Alias changes cannot retarget an
armed job. Missing permissions or departed targets fail the whole job without
partial pings. Quotes, forwarded snapshots, webhooks and bots cannot arm jobs.

The phone, Termux and bot **must be running**. A local worker checks about every
15 seconds and drains up to 10 due jobs per tick, with no model call per check. Jobs survive restart in gitignored
`memory/scheduled-messages.json` (beside the self-diary storage). Use one bot
process per state file, not multiple competing instances. Corrupt/unwritable
state stops delivery rather than resetting/replaying jobs. Scheduled work never
changes the independent proactive announcements feature.

Delivery is attempted once only. The worker saves `dispatching` before sending,
then records the returned Discord message ID on success. A crash/timeout during
send can leave delivery uncertain: it may have reached Discord, so it is never
automatically retried. Restart turns old `dispatching` jobs into `uncertain`.
This avoids duplicate pings; it is not a promise of exactly-once delivery over
network failure. Check the channel before creating a replacement for an uncertain
job. A failure saving final state halts the worker, not replaying the send.

**Missed-time policy:** delivery can occur within two minutes of the due time
(e.g. a short reconnect); anything later is marked `missed` and skipped, never
caught up hours later. The normal 15-second check cadence is not exact-second
delivery. The preview and confirmation state this before arming.


### Reminder routing and safe status

Explicit reminder/schedule/timer or timed-send intent no longer falls through
normal AI chat when the fields are incomplete. Kristy responds with a code-owned
"not saved/active" clarification and the required format instead of pretending
an "okay" has scheduled a delivery. A short same-user/server/channel follow-up
like "haan isi channel mein dena" is linked to that clarification for two minutes
(or must reply to that exact clarification); it still **cannot arm a job**.
Only the creator's exact `schedule confirm ID` arms the unchanged preview.

A bounded short form also works:
`Kristy reminder 10 min baad: Paani pi lena`
This creates a current-channel self-reminder **preview**, not an armed job.
Unclear clock dates/times, missing message text, targets or destination still
need the explicit format. This is not unrestricted natural-language extraction.

Use `Kristy schedule status JOB_ID` or `Kristy schedule list` to see your saved
jobs and worker health, without message bodies or destination/member identifiers.
Statuses include preview, scheduled, sent, missed, failed and uncertain, with
fixed reason codes. Empty list means no saved job found here, not a promise that
an earlier conversational "okay" was scheduled. `failed` can reflect membership/
permission checks; `uncertain` must never be blindly resent. Diagnostics show
worker running/not started/stopped/halted/waiting for connection. Corrupt storage
stays fail-closed. No job history is replayed by this repair.
