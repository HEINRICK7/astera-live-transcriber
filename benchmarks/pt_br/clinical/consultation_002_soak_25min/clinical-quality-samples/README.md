# Clinical quality samples

This directory contains reproducible manual-review clips from run 006.

For every `run_*/sample_*/audio.wav`:

1. listen to the complete clip;
2. create `reference.txt` with only what is actually spoken;
3. preserve legitimate repetitions, hesitations, numbers, units, negations and speaker labels;
4. do not copy `xai_raw_committed_snapshot.txt` or any Astera snapshot into the reference;
5. fill `clinical_annotations.json` only from the audio and change `_status`
   to `validated` after review;
6. validate medication, dose/value/unit, negation and speaker annotations independently.

`reference.pending.txt` is not a benchmark input. The manifest reports
`benchmark_ready=false` until a manually reviewed `reference.txt` exists for
each selected sample, annotations are validated, and a segment-local hypothesis
has been validated. `projected_text_clean.pending.txt` is not a hypothesis.

The source run stores `projected_text` as a cumulative transcript snapshot.
Those snapshots are retained for audit, but are not scored as if they were
local hypotheses for one 45-second clip.
