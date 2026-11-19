"""Content-free local monotonic stage timings, not a network/model attribution."""
import statistics
import time


class Timings:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.records = {}

    def mark(self, epoch, stage):
        if type(epoch) is not int or not 0 < epoch <= 120 or stage not in ('input_end', 'first_audio', 'playing'):
            return
        row = self.records.setdefault(epoch, {})
        row.setdefault(stage, self.clock())

    def summary(self):
        result = {}
        for name, start, end in (
                ('inputEndToFirstAudioMs', 'input_end', 'first_audio'),
                ('firstAudioToPlayingMs', 'first_audio', 'playing'),
                ('inputEndToPlayingMs', 'input_end', 'playing')):
            values = [(r[end] - r[start])*1000 for r in self.records.values()
                      if start in r and end in r and r[end] >= r[start]]
            result[name] = {'count': len(values),
                'median': round(statistics.median(values), 1) if values else None,
                'max': round(max(values), 1) if values else None}
        return result
