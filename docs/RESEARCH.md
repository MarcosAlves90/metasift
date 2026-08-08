# Research notes: metadata, AI provenance, sanitization and verification

Last reviewed: 2026-08-08.

This document records the mechanisms that materially shaped MetaSift 0.3. It is not a claim that every format or provenance technology below is fully implemented.

## 1. Metadata Cleaner / mat2

GNOME Metadata Cleaner presents file metadata and delegates sanitization to `mat2` (Metadata Anonymisation Toolkit 2). The relevant engineering lesson is format-aware handling, inspection before mutation, cleaned copies, and a conservative stance toward unsupported structures.

Sources:

- https://metadatacleaner.gitlab.io/
- https://gitlab.com/metadatacleaner/metadatacleaner/
- https://github.com/tpet/mat2

MetaSift follows those safety properties but keeps a smaller explicit native adapter set rather than claiming generic coverage.

## 2. Metadata, provenance and signal watermarks are different layers

A file may expose AI-related information through ordinary metadata (`prompt`, workflow JSON, software strings), structured provenance (C2PA/IPTC DigitalSourceType), or a signal embedded in the media itself.

OpenAI documents C2PA and SynthID as separate provenance technologies on supported generated images. C2PA is file-carried provenance that can be stripped; SynthID is embedded in the media signal and is outside a metadata sanitizer.

Sources:

- https://help.openai.com/en/articles/8912793-c2pa-and-synthid-in-openai-generated-images
- https://openai.com/research/verify/
- https://deepmind.google/models/synthid/

Therefore MetaSift never equates “no recognized metadata” with “not detectable as AI”.

## 3. C2PA 2.4

C2PA 2.4 defines format-specific embedding and signed Content Credential semantics. Relevant mechanisms include:

- JPEG — JUMBF/C2PA carried in APP11 and potentially split across multiple contiguous segments;
- PNG — C2PA JUMBF in `caBX`;
- GIF — C2PA application extension;
- RIFF/WebP/WAV/AVI — `C2PA` chunks;
- ISO-BMFF families — C2PA UUID boxes (not yet mutated by MetaSift 0.3).

C2PA 2.4 also defines AI-disclosure/digital-source semantics. MetaSift recognizes structured `DigitalSourceType`/`trainedAlgorithmicMedia` evidence as stronger than a generic keyword such as `prompt`.

Sources:

- https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html
- https://spec.c2pa.org/specifications/specifications/2.4/specs/ContentCredentials.html
- https://iptc.org/news/iptc-publishes-metadata-guidance-for-ai-generated-synthetic-media/

C2PA is not synonymous with AI. It can carry legitimate camera, editor, publisher and news provenance. This is why `share-safe` preserves it.

## 4. Official C2PA validation

The Content Authenticity Initiative publishes `c2pa-python`, Python bindings around the official C2PA SDK implementation. MetaSift treats this optional library as the cryptographic/semantic authority for Content Credential validation rather than reimplementing the full signature/trust model.

Source:

- https://github.com/contentauth/c2pa-python
- https://pypi.org/project/c2pa-python/

MetaSift's native parser still locates/removes supported physical C2PA containers. Structural presence and cryptographic validity are reported separately.

## 5. WebP invariants

WebP uses RIFF but its extended `VP8X` chunk contains feature flags for ICC, alpha, EXIF, XMP and animation. Removing an EXIF/XMP/ICC chunk without updating the corresponding VP8X feature bit can leave an inconsistent file.

Source:

- https://developers.google.com/speed/webp/docs/riff_container

MetaSift 0.3 therefore gives WebP a dedicated adapter and updates relevant VP8X flags after sanitization.

## 6. Selective EXIF vs deleting APP1

A privacy-safe operation should not necessarily delete all EXIF. EXIF can mix GPS/serial/creator data with exposure details, orientation and color-related technical information. MetaSift 0.3 parses supported EXIF IFDs to field-level evidence so policies can remove privacy data without treating the entire APP1 block as one item.

A broad “remove everything” operation can also affect rendering/interoperability. ExifTool's own guidance notes that metadata removal should preserve necessary color/profile information in some workflows.

Source:

- https://exiftool.org/faq.html

## 7. AI Metadata Cleaner image reconstruction

The public description at `aimetadatacleaner.com` describes an image rebuild flow: decode/redraw image pixels, omit carried metadata, optionally perturb RGB values by very small amounts, and write a fresh JPEG.

Sources:

- https://aimetadatacleaner.com/#how-it-works
- https://aimetadatacleaner.com/blog/image-hash-cleaner-change-image-fingerprint-guide

The defensible properties are:

- a newly encoded byte stream changes the cryptographic file hash;
- metadata not passed to the encoder is omitted;
- optional RGB perturbation changes decoded pixel values.

The stronger claim that ±1–2 RGB reliably defeats perceptual hashes/classifiers is not guaranteed; perceptual hashes are designed to tolerate small transformations. MetaSift therefore keeps jitter opt-in and makes no detector-evasion claim.

MetaSift 0.3 improves on the earlier 0.2 implementation by preserving format, alpha, and ICC by default rather than always converting to JPEG.

## 8. Independent verification

A sanitizer that verifies only with the parser that performed the removal has a common-mode failure: an unknown field can be missed both before and after mutation.

MetaSift 0.3 can therefore supplement native policy verification with:

- ExifTool for broad metadata observation;
- Pillow for image metadata observation;
- Mutagen for MP3 ID3 observation;
- c2pa-python for Content Credential validation.

Backend absence is represented as unavailable, not interpreted as evidence that the signal is absent.

## 9. OOXML hidden content

Microsoft Office documents can contain more than core author properties: comments, revisions, custom XML, hidden/invisible content, notes, embedded objects and other package parts can reveal information or alter document semantics.

Source:

- https://support.microsoft.com/en-us/office/remove-hidden-data-and-personal-information-by-inspecting-documents-presentations-or-workbooks

MetaSift 0.3 inventories several hidden-content classes but does not automatically remove them. Deleting comments/revisions or embedded content can change the meaning of a document and requires a separate destructive contract.

## 10. Resource exhaustion

Compressed metadata and archive formats create denial-of-service risk. Pillow documents decompression-bomb protections and limits for compressed PNG text; ZIP-based OOXML also requires explicit expansion budgets.

Sources:

- https://pillow.readthedocs.io/en/stable/handbook/security.html
- https://docs.python.org/3/library/zipfile.html

MetaSift centralizes file, metadata, chunk, ZIP, XML and image-pixel limits in `ResourceBudget`.

## 11. Remaining roadmap

Highest-value work not included in 0.3:

1. seekable/streaming parsing for large files;
2. fuzz/property corpora for all binary adapters;
3. ISO-BMFF base parser for AVIF/HEIC/HEIF/MP4/MOV/M4A C2PA and metadata;
4. richer IPTC/XMP round-trip semantics;
5. FLAC/OGG/audio expansion;
6. PDF metadata scrub and a separately named deep content-disarm/reconstruction mode;
7. OOXML destructive hidden-content transformations with explicit semantic-impact policies;
8. TIFF/DNG read-only inspection before any rewriting support;
9. optional fidelity metrics/oracles for rebuild operations.

These should be added incrementally and only with format-specific invariants and independent validation.
