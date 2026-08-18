# Sanitized agent-session history

`session-2026-08-11-sanitized.jsonl` is the Codex agent session (started 2026-08-11) that
designed and ran the data pipeline: collection, normalization, DeepSeek memo generation,
Qwen structuring, retries and merges. 21,386 JSONL events.

Sanitization, applied by `../scripts/sanitize_codex_session.py`:

1. `encrypted_content` fields (opaque ciphertext of model reasoning) replaced with
   `[STRIPPED: encrypted reasoning blob]` — 3,430 blobs. These blobs were also the source
   of every credential-shaped false positive in the pre-release secret scan.
2. 1Password secret *references* (`op://vault/item/...`) replaced with
   `op://[REDACTED-1PASSWORD-REF]` — 148 occurrences. References are not secrets, but they
   leak vault naming.
3. A personal email address redacted — 4 occurrences.

The sanitizer then re-scans its own output for known credential formats (OpenAI/DeepSeek
`sk-`, HuggingFace `hf_`, GitHub `gh?_`, AWS `AKIA`, private-key blocks, unredacted
`op://`) and deletes the output if anything is found. This release passed that check.
