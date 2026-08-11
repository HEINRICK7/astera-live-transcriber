from astera_live_transcriber.domain.audio import AudioChunk


class AudioNormalizer:
    """Validates the canonical PCM16/mono/16kHz boundary.

    Decoders and resamplers are deliberately deferred until an external input
    format is selected. Engines only receive chunks that satisfy this contract.
    """

    target_sample_rate = 16_000
    target_channels = 1

    def normalize(self, chunk: AudioChunk) -> AudioChunk:
        if chunk.sample_rate != self.target_sample_rate:
            raise ValueError("audio must be normalized to 16kHz before entering the pipeline")
        if chunk.channels != self.target_channels:
            raise ValueError("audio must be mono before entering the pipeline")
        if len(chunk.data) % 2:
            raise ValueError("PCM16 audio must contain complete samples")
        return chunk
