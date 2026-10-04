# TEST-only fixtures

Place sanitized provider payloads in per-provider subfolders and case packets in
per-case subfolders. Record source/provenance, units, timestamps and expected
missing fields. Artificial cases must say TEST. Never include secrets or raw
authorization headers, and never import this directory from production code.

Live runtime always reads provider observations. A quiet live demo must not
silently substitute one of these payloads.
