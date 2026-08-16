# Threat model

## Protected assets

- user identity, location and device identifiers;
- document authorship/history intended to be removed;
- generation prompts/workflows intended to be removed;
- provenance/authenticity information the user explicitly chooses to remove;
- visible/media payload integrity when using non-rebuild sanitization;
- local host availability while parsing attacker-controlled files.

## Trust boundaries

- Every input file is untrusted binary data.
- ZIP members, XML, compressed PNG text and metadata lengths are attacker-controlled.
- Pillow, Mutagen, required `pypdf`, and optional c2pa-python are third-party parser boundaries.
- ExifTool is an optional external process invoked without a shell and with a timeout.
- Output paths are caller-controlled local filesystem destinations.
- MetaSift performs no network access in normal operation.

## Security invariants

1. Unsupported sanitization fails closed.
2. Original files are preserved unless in-place mutation is explicit.
3. Malformed structures abort mutation rather than being partially rewritten.
4. Resource-intensive decompression/parsing is bounded.
5. A successful native sanitization must pass the same policy as a post-condition before output commit.
6. “Backend unavailable” and “not checked” are never reported as evidence of absence.
7. Provenance deletion is explicit in the recommended workflow.
8. Rebuild and lossless/container sanitization are distinct operations.

## Threats and mitigations

### Oversized file / memory exhaustion

Mechanism: a hostile file can force large allocations when read or transformed.

Mitigation: `ResourceBudget.max_file_bytes` is checked before the bounded read. Image pixels are separately capped. Residual risk: files under the cap are still read into memory; streaming parsers are not implemented yet.

### PNG compressed-metadata bomb

Mechanism: a small zTXt/iTXt payload expands into very large text.

Mitigation: decompression is incremental/bounded and counts against metadata budgets; malformed streams fail closed.

### OOXML ZIP bomb

Mechanism: many or highly compressed ZIP members expand far beyond input size.

Mitigation: limits on entry count, per-entry uncompressed size, total uncompressed size and compression ratio are checked before reading members.

### ZIP path traversal / encrypted members

Mitigation: unsafe member paths and encryption are rejected. MetaSift does not extract package paths into the filesystem during sanitization.

### XML entity/DTD expansion

Mitigation: metadata XML containing DTD/entity declarations is rejected and XML byte size is bounded.

### Structured-text parser exhaustion

Mechanism: oversized JSON or Markdown input can consume memory during UTF-8 decoding, JSON parsing, or front-matter scanning.

Mitigation: JSON and Markdown adapters enforce the centralized file-size budget before decoding. Markdown front-matter parsing is line-oriented rather than based on backtracking regular expressions.

### Encrypted or digitally signed PDF rewrite

Mechanism: encrypted PDFs cannot be safely inspected without decryption context, while rewriting a digitally signed PDF invalidates the signature and can create a misleading output.

Mitigation: encrypted PDFs fail closed. Recognized PDF signature fields are reported as non-removable provenance evidence, and sanitization refuses to rewrite signed PDFs.

### Parser confusion / truncated containers

Mitigation: structural lengths, signatures, CRCs where applicable, marker boundaries and RIFF/ZIP invariants are validated before mutation.

### WebP internal inconsistency

Mechanism: deleting EXIF/XMP/ICC chunks while leaving VP8X feature flags set creates an inconsistent container.

Mitigation: dedicated WebP adapter recomputes those VP8X flags after mutation.

### Partial C2PA removal in JPEG

Mechanism: a C2PA JUMBF store can span multiple contiguous APP11 segments.

Mitigation: MetaSift groups contiguous APP11 segments and removes a recognized C2PA run coherently rather than deleting an isolated segment.

### False statement of “clean”

Mechanism: a parser may fail to know about a metadata location and then verify its own omission.

Mitigation: native verification is a required post-condition; optional ExifTool/Pillow/Mutagen/c2pa-python provide independent evidence. Residual risk remains for unknown structures unsupported by all available oracles.

### Privacy preset destroys rendering data

Mitigation: evidence can be marked `rendering_required`/`preserve_recommended`. Orientation is not removed by broad maximum presets, and image rebuild preserves ICC by default.

### Broad provenance removal destroys authenticity

Mechanism: C2PA can document camera/editor/publisher provenance unrelated to AI.

Mitigation: `share-safe` preserves provenance. `provenance`, legacy `ai`, `metadata-max`, or explicit selectors are required to remove it, and planning reports warnings.

### Rebuild destroys visible fidelity

Mitigation: same-format output is default, alpha is preserved where possible, ICC is preserved by default, EXIF orientation is materialized, and animation fails closed. Format conversion and ICC deletion are explicit. Lossy codecs necessarily re-encode.

### User expects AI-detector evasion

Mitigation: MetaSift only reports transformations it performs. It does not claim removal of SynthID, steganographic signals, visual/statistical fingerprints, perceptual matching, or classifier detection.

### External verifier command injection

Mitigation: ExifTool is invoked as an argument vector with `shell=False`, a fixed executable discovered on PATH, and a finite timeout. File paths are never interpolated into a shell command.

## Residual risks

- A valid file may carry metadata in an unimplemented namespace or container.
- Native parsers are custom code and should continue receiving malformed corpus/fuzz testing.
- Third-party parser vulnerabilities, including `pypdf` for PDF support and optional backends, remain part of the dependency attack surface.
- C2PA cryptographic validation depends on optional c2pa-python; native structural recognition is not equivalent to signature validation.
- Very large but budget-compliant files can still use substantial memory because the engine is not streaming.
- Hidden OOXML content is inventory-only and is not claimed sanitized.
- PDF sanitization targets Info/XMP metadata only; it is not a content-disarm or active-content reconstruction guarantee.
