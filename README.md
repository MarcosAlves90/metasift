# MetaSift

MetaSift is a local-first CLI and Python library for inspecting, planning, sanitizing, rebuilding, and verifying embedded file metadata and provenance.

The project deliberately separates five concepts that are often conflated:

- **privacy metadata** — GPS, creator identity, device serials and similar personal data;
- **generation workflow metadata** — prompts, seeds, model/workflow fields and generator identifiers;
- **provenance** — C2PA / Content Credentials and structured digital-source declarations;
- **technical metadata** — orientation, ICC/color information and other data that may affect rendering or interoperability;
- **hidden content** — document comments, revisions, custom XML, embedded objects and similar non-visible package content.

MetaSift does **not** claim that sanitizing metadata makes AI-generated media indistinguishable from human-created media. Signal-level watermarks such as SynthID, steganographic marks, external service records, and content-based classifiers are different mechanisms.

## Design goals

1. Inspect before changing.
2. Preserve originals by default.
3. Fail closed when a format cannot be safely sanitized.
4. Prefer field-level removal over deleting whole metadata blocks.
5. Preserve rendering-critical data unless removal is explicit.
6. Treat provenance removal as a destructive authenticity decision, not ordinary privacy cleanup.
7. Bound resource consumption when parsing untrusted files.
8. Verify the result before committing the output; optionally use independent verification backends.

## Install

Python 3.11+ is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Optional capabilities:

```bash
python -m pip install -e '.[audio]'   # Mutagen: frame-level MP3 metadata
python -m pip install -e '.[c2pa]'    # official c2pa-python validation
python -m pip install -e '.[all]'
```

ExifTool is detected automatically when installed on the system and is used as an independent verification oracle. `ffprobe`/`ffmpeg` are reported by `doctor` but are not yet mutation backends in 0.4.0.

For development:

```bash
python -m pip install -e '.[dev]'
sh scripts/check.sh
```

## Core workflow

Inspect:

```bash
metasift inspect image.jpg
metasift inspect image.jpg --json
```

Plan before mutation:

```bash
metasift plan image.jpg --preset share-safe
metasift plan image.jpg --preset provenance
```

Sanitize metadata without re-encoding the primary media payload where the format permits it:

```bash
metasift sanitize image.jpg --preset share-safe
metasift sanitize image.jpg --preset privacy
metasift sanitize image.jpg --preset workflow
metasift sanitize image.jpg --preset provenance
metasift sanitize image.jpg --preset metadata-max
```

Select individual canonical fields:

```bash
metasift sanitize photo.jpg \
  --preset custom \
  --remove exif.GPS.GPSLatitude \
  --remove exif.GPS.GPSLongitude
```

Document metadata uses the same policy engine:

```bash
metasift inspect report.pdf
metasift sanitize report.pdf --preset share-safe
metasift sanitize notes.md --preset privacy
metasift sanitize payload.json --preset workflow
```

`--keep` always wins over a preset or explicit `--remove` selector.

Verify the output:

```bash
metasift verify image.sanitized.jpg --preset share-safe
metasift verify image.sanitized.jpg --preset provenance --json
```

Inspect provenance separately:

```bash
metasift provenance image.jpg
metasift provenance image.jpg --json
```

Check optional backends:

```bash
metasift doctor
```

Show format capabilities:

```bash
metasift formats
metasift formats --json
```

## Policies

| Policy | Removes privacy | Removes AI workflow | Removes provenance | Notes |
| --- | ---: | ---: | ---: | --- |
| `share-safe` | Yes | Yes | No | Recommended default for ordinary sharing. |
| `privacy` | Yes | No | No | Personal/location/device data only. |
| `workflow` | No | Yes | No | Prompt/generator/workflow evidence only. |
| `provenance` | No | No | Yes | Explicitly destroys recognized provenance/authenticity data. |
| `metadata-max` | Yes | Yes | Yes | Removes every recognized removable item except rendering-required entries. |
| `custom` | Explicit selectors only | Explicit | Explicit | Exact field/container control. |
| `ai` | No | Yes | Yes | Legacy compatibility behavior. |
| `full` | Yes | Yes | Yes | Legacy alias for `metadata-max`. |

`share-safe` intentionally preserves C2PA. C2PA may describe AI generation, but it may also prove legitimate camera/editor/news provenance. Removing it should be an explicit decision.

## Supported native sanitization

| Format | Inspection | Sanitization | Important guarantees / limits |
| --- | --- | --- | --- |
| JPEG/JPG | Field-level EXIF/XMP + structural APP metadata | Yes | JPEG scan data is preserved; contiguous APP11 C2PA/JUMBF runs are handled together. |
| PNG | Field-level eXIf + chunk/text metadata | Yes | IDAT chunks are preserved; compressed metadata expansion is bounded. |
| WebP | EXIF/XMP/ICC/C2PA RIFF chunks | Yes | VP8X feature flags are updated after removals; media chunks are preserved. |
| WAV/AVI RIFF | Chunk-level | Yes | Known metadata chunks only. |
| GIF | Extension-level | Yes | Unknown application extensions are preserved; C2PA/XMP/comments can be removed. |
| JSON | Fields inside top-level `metadata`, `_metadata`, or `_meta` objects | Yes | UTF-8 only; application data outside explicit metadata containers is preserved. |
| Markdown | YAML (`---`) and TOML (`+++`) front matter | Yes | UTF-8 only; body content is preserved. `metadata-max` can remove the complete recognized front-matter block. |
| PDF | Info dictionary + XMP + digital-signature inventory | Yes | Uses `pypdf`; encrypted PDFs and signed PDFs are not rewritten. Page objects are cloned, but PDF container bytes are rewritten. |
| Office Open XML / OPC | Core/custom properties + hidden-content inventory | Partial | Supports `.docx`, `.docm`, `.dotx`, `.dotm`, `.xlsx`, `.xlsm`, `.xlsb`, `.xltx`, `.xltm`, `.xlam`, `.pptx`, `.pptm`, `.potx`, `.potm`, `.ppsx`, `.ppsm`, and `.ppam`. Hidden content is reported but not destructively removed. |
| MP3 | ID3 frame-level with Mutagen; container fallback | Yes | MPEG audio payload is preserved. |
| Other formats | Warning | No | Sanitization fails closed. |

The capability matrix returned by `metasift formats --json` is the authoritative machine-readable description.

## Image rebuild

Metadata sanitization and image reconstruction are intentionally separate operations.

`image-rebuild` decodes and writes a new image without carrying EXIF/XMP/IPTC/C2PA forward. Unlike 0.2.0, it preserves the **same format by default**, uses **zero RGB jitter by default**, preserves alpha when the output format supports it, and preserves ICC by default because color profiles can affect rendering.

```bash
metasift image-rebuild photo.jpg
metasift image-rebuild art.png
metasift image-rebuild art.webp --jitter 2
metasift image-rebuild source.png --to jpeg --background FFFFFF
metasift image-rebuild source.jpg --drop-icc
```

Supported single-frame inputs/outputs are JPEG, PNG, WebP and AVIF when supported by the installed Pillow build. Animated/multi-frame images fail closed instead of silently losing frames.

`image-clean` remains as a compatibility command for the earlier AI Metadata Cleaner-style workflow: it defaults to a fresh JPEG. Pixel jitter is opt-in:

```bash
metasift image-clean image.png --jitter 2
```

A changed cryptographic hash or ±1–2 RGB perturbation is **not** a guarantee of defeating perceptual hashes, invisible watermarks, or AI classifiers.

## Batch operations

Lossless/container sanitization:

```bash
metasift batch ./input --recursive --preset share-safe --output-dir ./sanitized
```

Image rebuilding:

```bash
metasift image-batch ./images --recursive --output-dir ./rebuilt --to same
```

## Security model

Input files are untrusted. MetaSift 0.4.0 applies centralized resource budgets for:

- total file bytes;
- metadata bytes and individual metadata entries;
- parser chunk counts;
- PNG compressed metadata expansion;
- OOXML ZIP entry count, per-entry expansion, aggregate expansion and compression ratio;
- XML size and entity/DTD rejection;
- JSON/Markdown document size before UTF-8 parsing;
- image pixel count.

PDF parsing is delegated to the required `pypdf` dependency. Encrypted PDFs fail closed, and PDFs with recognized digital-signature fields are never rewritten because sanitization would invalidate their signatures.

Writes use sibling temporary files plus atomic replacement. Originals are not overwritten unless `--in-place` is explicitly requested. Unsupported sanitization never copies a file unchanged and reports success.

The current default file-size budget is deliberately finite, but most native container paths still read the bounded file into memory. Streaming parsers remain future work for very large media.

## Independent verification

`metasift verify` first evaluates the output with MetaSift's native policy engine, then uses available independent observations:

- **ExifTool** — broad external metadata inventory;
- **Pillow** — independent image metadata observation;
- **Mutagen** — independent MP3 ID3 observation;
- **c2pa-python** — official C2PA manifest validation.

Independent backends strengthen evidence but do not mathematically prove absence of every possible hidden channel. If a backend is missing, the report says so instead of treating “not checked” as “absent”.

## JSON contracts

Inspection, plan, sanitization, verification, provenance and image-rebuild reports include:

```json
{
  "schema_version": "1.0"
}
```

The schema marker allows integrations to detect future breaking report changes rather than scraping human-readable output.

## Deliberate non-goals in 0.4.0

- SynthID or other signal-level watermark removal.
- AI-classifier or perceptual-hash evasion guarantees.
- Steganalysis/steganographic rewriting.
- Destructive OOXML comment/revision acceptance/removal.
- PDF content-disarm/reconstruction.
- ISO-BMFF mutation for HEIC/HEIF/MP4/MOV/M4A.
- TIFF/DNG rewriting.
- GUI, cloud service, daemon or database.

These remain roadmap items only where they solve a demonstrated requirement. See `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, and `docs/RESEARCH.md`.

## Validation

```bash
pytest --cov=metasift --cov-report=term-missing
python -m compileall -q src tests
python -m build
```

Coverage is gated at 90%.

## License

MIT.
