from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpeechProviderCapabilities:
    streaming: bool = True
    partial_results: bool = True
    timestamps: bool = True
    word_timestamps: bool = True
    diarization: bool = False
    keyterms: bool = False
    multichannel: bool = False
    manual_finalize: bool = True
