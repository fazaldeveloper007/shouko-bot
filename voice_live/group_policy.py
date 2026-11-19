"""Offline policy only. Not connected to Discord, a microphone, or a provider.

The future adapter must obtain actor/roster from Discord, not command parameters
or a model, and call observe_roster before handling each interaction/audio event.
No consent made by an operator on another member's behalf is supported.
"""
from dataclasses import dataclass
import re


class GroupPolicyError(RuntimeError):
    """Fixed reason codes, never user content."""


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{15,22}', value):
        raise GroupPolicyError('GROUP_ID_INVALID')
    return value


def _ids(values):
    if not isinstance(values, (set, frozenset, tuple, list)):
        raise GroupPolicyError('GROUP_ROSTER_INVALID')
    result = frozenset(_id(x) for x in values)
    if len(result) != len(values):
        raise GroupPolicyError('GROUP_ROSTER_INVALID')
    return result


@dataclass(frozen=True)
class GroupScope:
    guild: str
    channel: str
    bot: str
    allowlist: frozenset
    notice_revision: str

    def __post_init__(self):
        for value in (self.guild, self.channel, self.bot):
            _id(value)
        object.__setattr__(self, 'allowlist', _ids(self.allowlist))
        if not self.allowlist or self.bot in self.allowlist:
            raise GroupPolicyError('GROUP_ALLOWLIST_INVALID')
        if (not isinstance(self.notice_revision, str) or
                not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', self.notice_revision)):
            raise GroupPolicyError('GROUP_NOTICE_INVALID')


class GroupSessionPolicy:
    """Current-session gate; verified persistent grants are supplied by coordinator.

    Ages are member assertions, not age verification. Every audience change is
    terminal after start. A new policy/session is required; bounded standing grants may be revalidated.
    This gate cannot establish the safety of spoken content under free terms.
    """
    def __init__(self, scope):
        if not isinstance(scope, GroupScope):
            raise GroupPolicyError('GROUP_SCOPE_INVALID')
        self.scope = scope
        self._roster = frozenset()
        self._agreed = set()
        self._active = self._closed = False

    def _open(self):
        if self._closed:
            raise GroupPolicyError('GROUP_SESSION_CLOSED')

    def _scope_matches(self, guild, channel):
        return guild == self.scope.guild and channel == self.scope.channel

    def observe_roster(self, *, guild, channel, humans, other_bots=()):
        """All occupants, including non-consenting listeners, matter."""
        self._open()
        try:
            roster = _ids(humans)
            bots = _ids(other_bots)
        except GroupPolicyError:
            self.stop()
            raise
        if (not self._scope_matches(guild, channel) or bots or not roster or
                not roster <= self.scope.allowlist):
            self.stop()
            raise GroupPolicyError('GROUP_AUDIENCE_UNSAFE')
        if roster != self._roster:
            self._agreed.clear()
            if self._active:
                self.stop()
                raise GroupPolicyError('GROUP_AUDIENCE_CHANGED')
            self._roster = roster

    def agree(self, *, actor, guild, channel, audience, notice_revision,
              adult, processing):
        self._open()
        # Actor and audience must be derived by the Discord adapter, not typed
        # IDs. These checks alone are NOT authentication of an interaction.
        if (not self._scope_matches(guild, channel) or actor not in self._roster or
                _ids(audience) != self._roster or
                notice_revision != self.scope.notice_revision or
                adult is not True or processing is not True):
            raise GroupPolicyError('GROUP_AGREEMENT_INVALID')
        self._agreed.add(actor)

    def start(self):
        self._open()
        if self._active:
            raise GroupPolicyError('GROUP_ALREADY_STARTED')
        if not self._roster or self._agreed != set(self._roster):
            raise GroupPolicyError('GROUP_AGREEMENT_MISSING')
        self._active = True

    def require_audio(self, *, speaker):
        self._open()
        if (not self._active or self._agreed != set(self._roster) or
                speaker not in self._roster):
            self.stop()
            raise GroupPolicyError('GROUP_AUDIO_NOT_ALLOWED')

    def require_playback(self):
        self._open()
        if not self._active or self._agreed != set(self._roster):
            self.stop()
            raise GroupPolicyError('GROUP_PLAYBACK_NOT_ALLOWED')

    def decline(self, *, actor):
        if actor not in self._roster:
            raise GroupPolicyError('GROUP_ACTOR_NOT_PRESENT')
        self.stop()

    def stop(self):
        self._active = False
        self._closed = True
        self._agreed.clear()
