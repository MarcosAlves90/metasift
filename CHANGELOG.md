# Changelog

## 0.4.0 - 2026-08-15

- Add field-level JSON metadata inspection and sanitization for top-level `metadata`, `_metadata`, and `_meta` containers while preserving ordinary application data.
- Add YAML/TOML Markdown front-matter inspection and sanitization for `.md`, `.markdown`, `.mdown`, `.mkd`, and `.mkdn`, preserving the document body.
- Add PDF Info/XMP metadata inspection and sanitization through `pypdf`, with digital-signature inventory and fail-closed behavior for encrypted or signed PDFs.
- Expand Office package selection beyond `.docx`, `.xlsx`, and `.pptx` to macro/template/add-in/slideshow variants while retaining core/custom-property-only sanitization.
- Apply centralized file-size budgets to JSON and Markdown adapters and simplify front-matter parsing to avoid regex backtracking.
- Add regression and end-to-end coverage for the new document formats while retaining the project's 90% coverage gate.

## 0.3.0 - 2026-08-08

- Separate privacy, AI workflow, provenance, technical metadata, and hidden-content evidence instead of treating every signal as equivalent.
- Add `share-safe`, `workflow`, `provenance`, and `metadata-max` policies while retaining `ai` and `full` as compatibility modes.
- Add a `plan` phase so destructive provenance removal and preserve-recommended metadata are visible before mutation.
- Add resource budgets for file size, metadata expansion, PNG compression, ZIP entries/ratios, XML size, chunk counts, and image pixels.
- Replace JPEG block-level EXIF handling with tag-level EXIF inspection/removal and add selective XMP handling.
- Add dedicated WebP parsing and maintain `VP8X` EXIF/XMP/ICC feature-bit consistency after cleaning.
- Harden PNG parsing, add bounded text decompression, tag-level eXIf handling, and trailing-data detection.
- Harden OOXML against path traversal, oversized entries, excessive aggregate expansion, suspicious compression ratios, DTD/entity XML, and archive bombs; add read-only hidden-content inventory.
- Add MP3 frame-level ID3 handling through optional Mutagen with a safe container-level fallback.
- Group contiguous JPEG APP11 C2PA/JUMBF segments so multi-segment manifest stores are removed coherently.
- Add optional official `c2pa-python` provenance verification and structured `DigitalSourceType`/AI-disclosure recognition.
- Add independent verification hooks for ExifTool, Pillow, Mutagen, and C2PA plus a `doctor` command reporting available backends.
- Redesign image rebuilding to preserve the input format by default, preserve alpha/ICC where the output supports them, and default pixel jitter to zero.
- Keep legacy `image-clean` as an explicit fresh-JPEG compatibility path; RGB jitter remains opt-in rather than a detector-evasion guarantee.
- Publish a versioned JSON schema marker (`schema_version: 1.0`) in reports.
- Expand tests to malformed-input, resource-exhaustion, provenance, WebP consistency, field-level policies, independent verification, and fidelity behavior.

## 0.2.0 - 2026-08-08

- Add `image-clean` for explicit decode/jitter/re-encode image reconstruction.
- Add `image-batch` for JPEG/PNG/WebP/AVIF batch reconstruction.
- Add bounded per-channel RGB jitter from 0 to ±2 and JPEG quality control from 85 to 95.
- Materialize EXIF orientation before discarding metadata and flatten transparency onto a configurable background.
- Add SHA-256 before/after reporting and metadata-free JPEG verification tests.
- Add Pillow 12.3 as the raster processing dependency and AVIF input support.
- Document AI Metadata Cleaner behavior and explicitly reject perceptual-hash/detector-evasion guarantees.

## 0.1.0 - 2026-08-08

- Add metadata inspection with human-readable and JSON output.
- Add `ai`, `privacy`, `full`, and `custom` cleaning modes.
- Add native PNG, JPEG, RIFF/WebP/WAV/AVI, GIF, OOXML, and MP3 adapters.
- Add C2PA recognition/removal for PNG, JPEG, RIFF, and GIF containers.
- Add batch cleaning, dry-run, exact keep/remove rules, and verification.
- Preserve originals by default and use atomic writes.
- Fail closed for unsupported cleaning formats.
- Add test suite with a 90% coverage gate.
