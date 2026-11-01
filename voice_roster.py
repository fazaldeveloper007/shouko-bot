"""Current gateway voice snapshot, never model/history-derived or ping-bearing."""
import re
import discord

QUERY = re.compile(
    r'(?:\b(?:vc|voice(?:\s+channel)?)\b.*\b(?:kaun|kon|who|members?)\b|'
    r'\b(?:kaun|kon|who|members?)\b.*\b(?:vc|voice(?:\s+channel)?)\b|'
    r'<#\d+>.*\b(?:kaun|kon|who|members?)\b|'
    r'\b(?:kaun|kon|who|members?)\b.*<#\d+>)', re.I)


def is_query(text):
    return bool(QUERY.search(text))


def snapshot(message, client, connected):
    guild = message.guild
    if (not connected or not client.is_ready() or not client.intents.voice_states
            or getattr(guild, 'unavailable', True)):
        return 'Abhi live VC state ready nahi hai. Reconnect hone ke baad poochna; purani list nahi bataungi.'
    raw_ids = re.findall(r'<#(\d+)>', message.content)
    if len(set(raw_ids)) > 1:
        return 'Ek hi VC ko # mention karo, kiski list chahiye?'
    if raw_ids:
        channel = guild.get_channel(int(raw_ids[0]))
        if channel is None:
            return 'Ye channel is server mein verify nahi hua. VC ko # mention karke select karo.'
    else:
        channel = getattr(getattr(message.author, 'voice', None), 'channel', None)
        if channel is None:
            return 'Tum abhi VC mein nahi ho. Kaunsa VC? Usko # mention karke poochho: Kristy is VC mein kaun hai?'
        # Resolve again, do not trust a detached channel object.
        channel = guild.get_channel(channel.id)
    if (channel is None or getattr(getattr(channel, 'guild', None), 'id', None) != guild.id
            or channel.type not in (discord.ChannelType.voice, discord.ChannelType.stage_voice)):
        return 'Is server ka ek voice/stage channel # mention karo, text channel nahi.'
    requester = guild.get_member(message.author.id)
    me = guild.me
    if requester is None or me is None:
        return 'Server membership/permissions abhi verify nahi hui; list nahi bataungi.'
    if not channel.permissions_for(requester).view_channel or not channel.permissions_for(me).view_channel:
        return 'Us VC ki list dikhane ki access verify nahi hui.'
    # Public voice_states includes IDs even when the member cache is incomplete.
    # Never turn unresolved IDs into a confidently empty/partial roster.
    states = dict(channel.voice_states)
    members = []
    for mid in states:
        m = guild.get_member(mid)
        if m is None:
            return 'VC ke kuch members abhi resolve nahi hue. Adhoori list dene ke bajaye thodi der baad poochna.'
        members.append(m)
    name = safe(channel.name)
    if not members:
        return f'{name} VC abhi khaali hai. (Abhi ke gateway snapshot ke hisaab se.)'
    names = [f"- {safe(m.display_name)}{' [bot]' if m.bot else ''}" for m in sorted(members, key=lambda m: (m.display_name.casefold(), m.id))]
    return f'{name} VC mein abhi {len(members)} members:\n' + '\n'.join(names) + '\n(Abhi ka gateway snapshot; log join/leave kar sakte hain.)'


def safe(text):
    return discord.utils.escape_markdown(str(text).replace('\n', ' ').replace('\r', ' '))[:100]
