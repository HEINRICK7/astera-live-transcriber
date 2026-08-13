# Clinical quality pilot

This pilot intentionally stops after three samples:

- `sample_002`
- `sample_015`
- `sample_019`

For each referenced sample directory, listen to `audio.wav` and
create `reference.txt` literally from the audio. Fill
`clinical_annotations.json` from the audio only and change `_status`
to `validated` after review.

Do not use provider or projected text as ground truth. Do not annotate
the remaining 17 samples until this pilot benchmark is reviewed.
