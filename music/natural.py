"""Deterministic 'Kristy <song> bajao' parser. No AI, no other player actions."""
import re

_VERB = (r'(?:bajao|bajado|baja\s*do|bajana|bajaiye|baja\s*dena|play(?:\s*(?:kar|karo|kardo|kar\s*do|krdo|krde))?|'
         r'chalao|chala\s*do|chaladena|chala\s*dena|laga\s*do|lagado|lagao|lagana|laga\s*dena)')
_TAIL = r'(?:\s+(?:na|please|plz|pls|bhai|yaar|ok|okay|zara|ab|jaldi|ji))*'
_AFTER = re.compile(r'^(?:(?:is|iss|isi|iske|is\s+wale)\s+(?:song|gaane|gaana|track|video)?\s*(?:ke\s+)?(?:baad|bad)|iske\s+(?:baad|bad)|next|phir|fir|uske\s+baad)[\s,:-]+', re.I)
_LEAD = re.compile(r'^(?:(?:ok+|okay|hey|please|plz|pls|bhai|toh|ab|abhi|kristy)[\s,]+)+', re.I)
_PLAY = re.compile(rf'^(?P<q>.+?)\s+{_VERB}{_TAIL}[\s.!]*$|^{_VERB}\s+(?P<q2>.+?)[\s.!]*$', re.I | re.S)
_REPORTED = re.compile(r"\bne\s+(?:bola|kaha|bol\s+raha|keh)\b|\b(?:bola|kaha|said|says|kehta|kehti)\s+(?:ki|that|tha)\b", re.I)
_CONDITIONAL = re.compile(r'(?:agar|if)\b.*\b(?:toh|then)\b', re.I)
_NEGATED = re.compile(r"^(?:mat|nahi|nhi|na|don't|dont|do\s+not|kya|kaun|kaise|kyun|kyu|kab|kahan)$|\b(?:mat|nahi|nhi|don't|dont)$", re.I)
_FILLER = {'ye', 'yeh', 'wo', 'woh', 'ek', 'koi', 'kuch', 'gaana', 'gana', 'gaane', 'song', 'songs', 'music', 'track', 'video', 'ko', 'ka', 'ki', 'ke', 'se', 'wala', 'wale', 'ye', 'kar', 'karo', 'ya', 'aur', 'ab', 'yaar', 'bhai', 'na', 'the', 'a'}
MAX_QUERY = 150


def parse(text):
    """Return a search query, '' when a play request has no title, or None
    when the text is not a play request at all."""
    text = (text or '').strip()
    if not text or '\n' in text or len(text) > 300:
        return None
    # Quotes/code/blockquote, reported speech and conditionals are not a direct
    # request. Words like agar/not/nahi/if inside a TITLE are fine.
    if re.search(r'[`"\u201c\u201d]|^\s*>', text) or _REPORTED.search(text) or _CONDITIONAL.match(text):
        return None
    text = _LEAD.sub('', text)
    text = _AFTER.sub('', text).strip()
    m = _PLAY.match(text)
    if not m:
        return None
    query = ' '.join((m.group('q') or m.group('q2') or '').split()).strip(' ,.:-')
    query = re.sub(r'^(?:ye|yeh|wo|woh|woh wala|ye wala)\s+(?:gaana|gana|song|track|video)\s+', '', query, flags=re.I)
    query = re.sub(r'\s+(?:gaana|gana|song)$', '', query, flags=re.I) if len(query.split()) > 2 else query
    if _NEGATED.search(query) or re.search(r'\bkristy\b', query, re.I):
        return None
    if re.search(r'[<>@`"“”]|@everyone|<@', query):
        return None
    if not [w for w in re.findall(r"[\w']+", query.casefold()) if w not in _FILLER]:
        return ''
    if len(query) > MAX_QUERY:
        return None
    return query
