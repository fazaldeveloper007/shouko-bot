"""Keep transient player state out of durable diaries without disabling VC memories."""
import re
# Strip only clauses describing current playback. Keep other personal facts/preferences.
STATE = re.compile(r'(?i)\b(?:(?:is|are|am)\s+(?:playing|paused|listening)|now\s+playing|currently\s+(?:playing|paused|listening)|queue(?:d)?\s+(?:is|has)|(?:gaana|song|track|audio|video|music)\s+(?:abhi|baj\w*|chal\w*|playing|paused)|(?:i|we|kristy|she)\s+(?:am|are|is)?\s*(?:playing|paused|listening)\b|(?:abhi|currently)\s+[^.!?;]{0,30}\b(?:baj\w*|chal\w*))')

def durable_user_text(text):
    parts = re.split(r'(?<=[.!?;])\s+|\n+', text)
    return ' '.join(p for p in parts if not STATE.search(p)).strip()

def durable_exchange(speaker, user_text, bot_name, reply, music_context):
    if not music_context:
        return [(speaker, user_text[:300]), (bot_name, reply[:300])]
    # The reply had transient runtime context injected. Do not feed that reply
    # into the diary extractor, but preserve unrelated user facts in the same chat.
    text = durable_user_text(user_text)
    return [(speaker, text[:300])] if text else []
