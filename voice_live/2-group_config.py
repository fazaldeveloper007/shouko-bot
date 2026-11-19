"""Read private local config. Never publish real IDs or infer member consent."""
import json
from pathlib import Path
from voice_live.group_policy import GroupScope, GroupPolicyError


def load_config(path):
    path = Path(path)
    if path.stat().st_size > 16384:
        raise GroupPolicyError('GROUP_CONFIG_INVALID')
    data = json.loads(path.read_text())
    if (data.get('schema') != 1 or data.get('maximum_concurrent_humans') != 8 or
            data.get('consents') != [] or data.get('notice_approved') is not False or
            data.get('channel_gate', 'restricted') not in ('restricted', 'open')):
        raise GroupPolicyError('GROUP_CONFIG_INVALID')
    members = data.get('members')
    if not isinstance(members, list) or not 1 <= len(members) <= 64:
        raise GroupPolicyError('GROUP_CONFIG_INVALID')
    ids = [data['owner_id']] + [m['user_id'] for m in members]
    if len(ids) != len(set(ids)):
        raise GroupPolicyError('GROUP_CONFIG_INVALID')
    return data, tuple(ids)


def configured_scope(data, ids, bot_id):
    # Actual target channel must be explicitly filled; no old screenshot default.
    return GroupScope(data['guild_id'], data['voice_channel_id'], str(bot_id),
        frozenset(ids), data['notice_revision'],
        data.get('channel_gate', 'restricted'))
