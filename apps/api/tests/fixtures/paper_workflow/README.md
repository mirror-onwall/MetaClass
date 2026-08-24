# Paper workflow integration samples

`local_samples.example.json` registers the two local PDFs used during Stage 0 contract
development. The files remain outside Git because research-paper redistribution rights
may vary.

Unit tests do not depend on these paths. Future opt-in integration tests may copy the
PDFs into an isolated job workspace and skip cleanly when a local file is unavailable.
