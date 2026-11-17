# Research protocol format and fingerprinting

**Status:** foundational format and identity primitives; no protocol family is approved by this document.

## Scope

This change defines JSON Schema contracts for protocol, run, journal-event, and decision records, plus the deterministic serialization used to identify protocol content. It does not create a PPO protocol instance, release a protocol version, register existing reports, or authorize experiment execution.

The first PPO benchmark protocol must be drafted from verified source evidence, reviewed field by field, and explicitly approved before it can be marked `released`. Existing benchmark parameters are not automatically authoritative protocol values.

## Schemas

The JSON Schemas use Draft 2020-12 and are stored under `schemas/`:

- `research-protocol.schema.json`
- `research-run.schema.json`
- `research-event.schema.json`
- `research-decision.schema.json`

Schema validation and governance policy are separate. A schema-valid protocol with `status: draft` is not eligible for an `ACCEPTED` decision. Schema validation does not prove methodological validity, data provenance, or statistical robustness.

## Canonical JSON v1

`src/research_governance/protocol.py` exposes:

- `canonicalize_protocol(mapping) -> bytes`
- `protocol_fingerprint(mapping) -> str`

Canonical bytes are UTF-8 JSON with recursively sorted object keys, compact separators (`,` and `:`), non-ASCII characters emitted as UTF-8, and array order preserved. Non-finite numbers (`NaN`, positive/negative infinity), non-string object keys, and values outside the JSON data model are rejected. No timestamps, defaults, metadata, or fields are injected during canonicalization.

The fingerprint is the lowercase hexadecimal SHA-256 digest of those exact bytes. It identifies the complete supplied protocol content; changing any field changes the digest. Callers must validate the protocol against the applicable schema before fingerprinting and must retain the original protocol bytes separately when byte-for-byte source preservation is required.

Canonicalization is versioned as `stmaa-json-v1` by this documented contract. Implementations in other languages must reproduce these serialization rules exactly and should use cross-language test vectors before being treated as compatible. The function does not currently implement RFC 8785/JCS number normalization; protocol fingerprint production is therefore defined by this Python serialization contract until a separately reviewed cross-language canonicalization standard is adopted.

## Explicit exclusions

- No `research/protocols/ppo-benchmark/1.0.0.json` is added in this change.
- No current PPO training, benchmark, walk-forward, or cost parameter is promoted into a released protocol.
- No report or model artifact is rewritten or regenerated.
- No registry journal, run capture, evidence verification, or decision workflow is implemented here.
- No broker or Alpaca access is introduced.
