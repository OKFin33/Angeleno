"""Preserve PCM samples and resampling state across arbitrary HTTP chunks."""
import audioop


class PCMResampler:
    def __init__(self, source_rate, target_rate):
        self.source_rate = source_rate
        self.target_rate = target_rate
        self.pending = b''
        self.state = None

    def push(self, chunk):
        data = self.pending + chunk
        size = len(data) // 2 * 2
        self.pending = data[size:]
        data = data[:size]
        if not data or self.source_rate == self.target_rate:
            return data
        result, self.state = audioop.ratecv(data, 2, 1, self.source_rate, self.target_rate, self.state)
        return result

    def finish(self):
        if self.pending:
            raise ValueError('Truncated 16-bit PCM sample')
