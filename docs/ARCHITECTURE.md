# Architecture

## Objective

MetaSift is a local-first metadata/provenance sanitizer. The 0.3 architecture optimizes for security, correctness, explicit policy, payload fidelity, and independently verifiable behavior before format breadth.

## Pipeline

```text
Path
  -> bounded probe/read
  -> adapter selection
  -> inspect/classify evidence
  -> sanitization plan
  -> transform
  -> native post-condition verification
  -> atomic write
  -> optional independent verification
  -> report
```

Image reconstruction is a separate pipeline:

```text
Path -> bounded decode -> materialize orientation -> preserve/drop fidelity data explicitly
     -> optional pixel perturbation -> encode selected format -> atomic write -> fidelity report
```

## Evidence model

`MetadataEntry` is the canonical evidence record. It contains:

- physical/logical source;
- key and canonical selector path;
- namespace;
- category (`privacy`, `workflow`, `provenance`, `technical`, `hidden-content`, `metadata`, `unknown`);
- signal type;
- confidence (`confirmed`, `probable`, `possible`, `unknown`);
- removability;
- removal impact;
- preservation recommendation;
- rendering-required flag.

Legacy boolean `ai_related`, `privacy_related`, and `provenance_related` fields remain for compatibility but policy decisions are increasingly grounded in structured evidence.

## Policy layer

`policy.py` is the single source of truth for presets. Presets are deliberately orthogonal:

- `privacy` — personal/location/device evidence;
- `workflow` — generation workflow evidence;
- `provenance` — authenticity/provenance structures;
- `share-safe` — privacy + workflow, preserving provenance;
- `metadata-max` — all recognized removable metadata except rendering-required entries;
- `custom` — exact selectors;
- `ai` and `full` — compatibility spellings/semantics.

Explicit `--keep` takes precedence over every removal decision.

## Resource boundary

`ResourceBudget` centralizes parser limits instead of allowing adapters to invent independent thresholds. `ResourceTracker` accounts for metadata expansion and chunk counts.

Budgets currently cover:

- file size;
- expanded metadata total and entry size;
- parser chunks;
- XML size;
- ZIP entry count, individual uncompressed size, aggregate uncompressed size and compression ratio;
- image pixels;
- container-depth reservation for future recursive formats.

The current engine still reads a file into memory *after* the file-size guard. This is bounded, not streaming. Streaming/seekable parsers are a future architecture change for large media.

## Adapters

### JPEG

The parser validates markers and segment lengths before mutation. EXIF is parsed to individual tags; XMP is parsed to leaf fields where supported. C2PA/JUMBF APP11 data is treated as a contiguous run: if a run is recognized as C2PA, the run is removed coherently. SOS and entropy-coded scan data are copied unchanged during container sanitization.

### PNG

The parser validates chunk boundaries and CRCs. zTXt/iTXt expansion uses a bounded zlib decompressor. eXIf uses field-level EXIF policy. C2PA `caBX` and recognized text metadata can be removed while IDAT is preserved. Bytes after IEND are exposed as trailing data rather than silently ignored.

### WebP

WebP has a dedicated adapter rather than relying on generic RIFF behavior. EXIF/XMP/ICC/C2PA chunks are classified separately. After removal, VP8X feature bits are recomputed for ICC, EXIF, and XMP consistency. VP8/VP8L/ANMF media chunks are preserved.

### RIFF WAV/AVI

The generic RIFF adapter handles known top-level metadata chunks and excludes WebP, whose invariants require the dedicated adapter.

### GIF

The parser removes recognized comment/XMP/C2PA extensions but preserves unknown application extensions because they may affect animation/rendering.

### JSON documents

The JSON adapter accepts UTF-8 `.json` documents and deliberately treats metadata as opt-in structure rather than guessing from arbitrary application fields. Only top-level `metadata`, `_metadata`, and `_meta` containers are inspected. Dictionary fields become field-level `MetadataEntry` records; non-object metadata containers are exposed as a single removable entry. A mutation preserves BOM/newline behavior and JSON values, although formatting is normalized when a rewrite is required. Non-object JSON documents are left unchanged.

### Markdown

The Markdown adapter supports UTF-8 `.md`, `.markdown`, `.mdown`, `.mkd`, and `.mkdn` documents with YAML (`---`) or TOML (`+++`) front matter. Parsing is line-oriented and bounded by the centralized file-size budget. Recognized top-level front-matter fields are sanitized independently when possible; the body is preserved. `metadata-max`/`full` can remove the complete recognized front-matter block, including cases where not every top-level line can be represented safely as an individual field.

### PDF

PDF parsing and rewriting use the required `pypdf` dependency. The adapter exposes the Info dictionary and XMP as field-level metadata and inventories recognized digital-signature fields as non-removable provenance evidence. Encrypted PDFs fail closed. PDFs with recognized signatures are inspectable but are never rewritten because any sanitization rewrite would invalidate those signatures. For unsigned PDFs, pages/document objects are cloned while metadata is rewritten; byte-for-byte PDF container preservation is not promised. Deep content disarm/reconstruction remains a separate non-goal.

### OOXML / OPC office packages

ZIP packages are bounded before decompression. Path traversal, encryption, excessive entries, excessive expansion, suspicious compression ratios, oversized XML and DTD/entity declarations are rejected. Core/custom properties are sanitizable for the supported document, spreadsheet, template, macro, add-in, slideshow, and presentation suffixes. Comments, custom XML, embedded objects, macros, notes and similar hidden package content remain inventory-only because deleting them can alter document semantics.

Supported suffixes are `.docx`, `.docm`, `.dotx`, `.dotm`, `.xlsx`, `.xlsm`, `.xlsb`, `.xltx`, `.xltm`, `.xlam`, `.pptx`, `.pptm`, `.potx`, `.potm`, `.ppsx`, `.ppsm`, and `.ppam`. Adapter selection still requires a ZIP/OPC signature; a matching extension alone is not treated as a valid Office package.

### MP3

When Mutagen is installed, ID3 frames are inspected and mutated at frame level. Without Mutagen, MetaSift retains a smaller container-level fallback. MPEG audio payload bytes are not re-encoded.

## Provenance

`provenance.py` separates structural evidence from cryptographic validation.

Native adapters locate recognized provenance structures. If optional `c2pa-python` is installed, the official SDK reads/validates the Content Credential and the report records active manifest and validation status. Backend absence is reported as `backend-unavailable`, never as “C2PA absent”.

## Verification

Native post-condition verification occurs before the atomic output is committed: if the adapter still recognizes metadata targeted by the selected policy, the operation fails.

`verification.py` can add independent observations from ExifTool, Pillow, Mutagen and c2pa-python. This avoids relying solely on the same parser that performed the mutation. Independent checks remain evidence, not proof of absence of arbitrary unknown channels.

## Image rebuild

`image_rebuild.py` is intentionally separate from ordinary sanitization.

Defaults:

- same output format;
- zero jitter;
- ICC preserved;
- alpha preserved if the output format supports it;
- EXIF orientation materialized into pixels;
- EXIF/XMP/IPTC/C2PA not propagated;
- animation rejected.

Conversion to JPEG, alpha flattening, ICC deletion and pixel jitter require explicit options or the legacy `image-clean` command.

## Public API

The package exports:

- `inspect_file()`
- `plan_file()`
- `clean_file()`
- `verify_file()`
- `inspect_provenance()`
- `rebuild_image()`

Machine-readable results use schema version `1.0`.

## Architectural non-goals for 0.3

No distributed services, database, plugin framework, GUI, vendor cloud dependency, PDF content-disarm/reconstruction pipeline, BMFF mutation, or generic “support every format” abstraction. New backends should be introduced only when they solve an observed format/verification requirement.
