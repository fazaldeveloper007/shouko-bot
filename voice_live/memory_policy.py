"""Candidate gate, not a claim of perfect semantic privacy classification.

The extractor selects category/meaning. Code binds exact source evidence and
rejects detectable sensitive/third-party/quoted/instruction content. Ambiguous
candidates are skipped, never silently approved by a model's category alone.
Only verbatim self-evidence is stored, not model-generated paraphrases.
"""
import re

DENY = re.compile(
    r'https?://|www\.|@|<@|[0-9]{4,}|[0-9a-f]{24,}|'
    r'\b(?:password|passcode|pin|token|api.?key|secret|otp|cvv|cvc|account|'
    r'address|location|live|lives|home|school|college|hospital|health|medical|'
    r'diagnos\w*|depress\w*|suicid\w*|medicine|disease|bank|salary|income|'
    r'money|finance|debt|card|phone|mobile|email|birthday|birth|age|years? old|'
    r'friend|friends|brother|sister|mother|father|family|boyfriend|girlfriend|'
    r'wife|husband|they|their|she|her|he|his|someone|somebody|person|'
    r'dost|bhai|behen|papa|mummy|uska|uski|unka|unki|woh|wo|'
    r'ignore|instruction|system|developer|prompt|upload|forward|send|delete|'
    r'execute|command|tool|permission|grant|approve|remember|save|yaad)\b', re.I)
SELF = re.compile(r'^(?:i\b|my\b|mera\b|meri\b|mere\b|mujhe\b|main\b|mujhko\b)', re.I)


def eligible_quote(quote):
    if not isinstance(quote, str) or not 1 <= len(quote) <= 129:
        return False
    if quote != quote.strip() or not SELF.search(quote) or DENY.search(quote):
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in quote):
        return False
    # No represented speech, multi-speaker labels, formatting or encoded blobs.
    if any(c in quote for c in ('"', '`', '<', '>', ':', '=', '{', '}', '[', ']')):
        return False
    if any(len(word) > 30 for word in quote.split()):
        return False
    return True


def safe_candidates(candidates, turns):
    """Return (verbatim self-notes, skipped count). No data saved here.

    Category is useful but untrusted. Exact source_quote must occur in its
    code-bound requester source fragment. No fabricated paraphrase is stored.
    No ordering or conflict resolution inferred from asynchronous segment IDs.
    Initial private read review is still needed for existing legacy notes.
    """
    sources = dict(turns)
    approved = []
    skipped = 0
    seen = set()
    for text, category, source_id, quote in candidates:
        source = sources.get(source_id)
        if (category not in ('preferred_name', 'game_preference', 'interest')
                or not isinstance(source, str) or quote not in source
                or not eligible_quote(quote) or DENY.search(source)
                or any(c in source for c in ('"', '`', '>', ':'))):
            skipped += 1
            continue
        note = 'Self-note: ' + quote
        if note not in seen:
            approved.append(note)
            seen.add(note)
    return tuple(approved), skipped
