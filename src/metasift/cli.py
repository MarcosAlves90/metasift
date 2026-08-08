from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .capabilities import capability_matrix
from .doctor import tool_status
from .engine import clean_file, inspect_file, plan_file
from .image_rebuild import rebuild_image
from .models import CleanMode, InspectionReport, MetadataEntry
from .provenance import inspect_provenance
from .verification import verify_file


def _json_dump(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def _entry_line(entry: MetadataEntry) -> str:
    flags = [entry.category, entry.confidence]
    if entry.preserve_recommended:
        flags.append("preserve-recommended")
    if not entry.removable:
        flags.append("read-only")
    preview = f" = {entry.value_preview}" if entry.value_preview else ""
    return f"- {entry.selector} ({entry.source}, {entry.size} B) [{' '.join(flags)}]{preview}"


def _print_report(report: InspectionReport) -> None:
    print(f"{report.path}: {report.format} via {report.adapter}; {report.size} bytes")
    if report.metadata:
        for entry in report.metadata:
            print(_entry_line(entry))
    else:
        print("- no embedded metadata recognized by this adapter")
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _mode(value: str) -> CleanMode:
    try:
        return CleanMode(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _rgb(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise argparse.ArgumentTypeError("background must be a 6-digit RGB hex value")
    try:
        return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("background must be a 6-digit RGB hex value") from exc


def _iter_files(root: Path, recursive: bool) -> list[Path]:
    if root.is_file():
        return [root]
    pattern = "**/*" if recursive else "*"
    return sorted(path for path in root.glob(pattern) if path.is_file())


def cmd_inspect(args: argparse.Namespace) -> int:
    reports = [inspect_file(path) for path in args.paths]
    if args.json:
        _json_dump([report.to_dict() for report in reports] if len(reports) > 1 else reports[0].to_dict())
    else:
        for index, report in enumerate(reports):
            if index:
                print()
            _print_report(report)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    plan = plan_file(
        args.path,
        mode=args.mode,
        remove_keys=tuple(args.remove or ()),
        keep_keys=tuple(args.keep or ()),
    )
    if args.json:
        _json_dump(plan.to_dict())
    else:
        print(f"plan: {plan.source} ({plan.mode.value})")
        print("remove:")
        if plan.remove:
            for entry in plan.remove:
                print(_entry_line(entry))
        else:
            print("- nothing")
        print("preserve:")
        if plan.preserve:
            for entry in plan.preserve:
                print(_entry_line(entry))
        else:
            print("- nothing")
        for warning in plan.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


def _run_clean(args: argparse.Namespace) -> int:
    if getattr(args, "dry_run", False):
        plan = plan_file(
            args.path,
            mode=args.mode,
            remove_keys=tuple(args.remove or ()),
            keep_keys=tuple(args.keep or ()),
        )
        if args.json:
            _json_dump({"dry_run": True, **plan.to_dict()})
        else:
            print(f"dry-run: {plan.source} ({plan.mode.value})")
            for entry in plan.remove:
                print(_entry_line(entry))
            if not plan.remove:
                print("- nothing would be removed")
        return 0

    result = clean_file(
        args.path,
        mode=args.mode,
        destination=args.output,
        in_place=args.in_place,
        remove_keys=tuple(args.remove or ()),
        keep_keys=tuple(args.keep or ()),
    )
    if args.json:
        _json_dump(result.to_dict())
    else:
        label = "cleaned" if getattr(args, "command", "") == "clean" else "sanitized"
        print(f"{label}: {result.source} -> {result.destination}")
        print(f"removed: {len(result.removed)} entries; {result.bytes_before} -> {result.bytes_after} bytes")
        for entry in result.removed:
            print(_entry_line(entry))
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    root = Path(args.path)
    files = _iter_files(root, args.recursive)
    if not files:
        print("no files found", file=sys.stderr)
        return 1
    if args.in_place and args.output_dir:
        raise ValueError("--in-place and --output-dir are mutually exclusive")

    results = []
    failures = []
    output_root = Path(args.output_dir) if args.output_dir else None
    for source in files:
        try:
            destination = None
            if output_root is not None:
                relative = source.relative_to(root) if root.is_dir() else Path(source.name)
                destination = output_root / relative
            results.append(
                clean_file(
                    source,
                    mode=args.mode,
                    destination=destination,
                    in_place=args.in_place,
                    remove_keys=tuple(args.remove or ()),
                    keep_keys=tuple(args.keep or ()),
                )
            )
        except (OSError, ValueError) as exc:
            failures.append({"path": str(source), "error": str(exc)})
            if args.fail_fast:
                break

    if args.json:
        _json_dump({"results": [item.to_dict() for item in results], "failures": failures})
    else:
        for item in results:
            print(f"{item.source} -> {item.destination}: removed {len(item.removed)}")
        for failure in failures:
            print(f"error: {failure['path']}: {failure['error']}", file=sys.stderr)
    return 1 if failures else 0


def cmd_verify(args: argparse.Namespace) -> int:
    report = verify_file(
        args.path,
        mode=args.mode,
        remove_keys=tuple(args.remove or ()),
        keep_keys=tuple(args.keep or ()),
        independent=not args.no_independent,
    )
    if args.json:
        _json_dump(report.to_dict())
    else:
        if not report.supported:
            print(f"unsupported: {report.path}; sanitization cannot be verified")
        else:
            print(f"{'clean' if report.clean else 'not clean'}: {report.path} for mode={report.mode.value}")
            for entry in report.remaining:
                print(_entry_line(entry))
            for check in report.independent_checks:
                print(f"check: {check['backend']}: {check.get('status', 'unknown')}")
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    if not report.supported:
        return 3
    return 0 if report.clean else 2


def _run_rebuild(args: argparse.Namespace, *, legacy_jpeg: bool = False) -> int:
    requested = "jpeg" if legacy_jpeg and args.to_format is None else (args.to_format or "same")
    result = rebuild_image(
        args.path,
        destination=args.output,
        output_format=requested,
        quality=args.quality,
        jitter=args.jitter,
        background=args.background,
        preserve_icc=not args.drop_icc,
    )
    if args.json:
        _json_dump(result.to_dict())
    else:
        print(f"rebuilt: {result.source} -> {result.destination}")
        quality = f" quality={result.quality}" if result.quality is not None else ""
        print(f"image: {result.input_format} -> {result.output_format}; {result.width}x{result.height};{quality} jitter=±{result.jitter}")
        print(f"fidelity: alpha_preserved={result.alpha_preserved} icc_preserved={result.icc_preserved}")
        print(f"sha256: {result.sha256_before} -> {result.sha256_after}")
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


def cmd_image_rebuild(args: argparse.Namespace) -> int:
    return _run_rebuild(args)


def cmd_image_clean(args: argparse.Namespace) -> int:
    return _run_rebuild(args, legacy_jpeg=True)


def cmd_image_batch(args: argparse.Namespace) -> int:
    root = Path(args.path)
    candidates = _iter_files(root, args.recursive)
    supported = {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".avif"}
    files = [path for path in candidates if path.suffix.casefold() in supported]
    if not files:
        print("no supported images found", file=sys.stderr)
        return 1

    results = []
    failures = []
    output_root = Path(args.output_dir)
    for source in files:
        try:
            relative = source.relative_to(root) if root.is_dir() else Path(source.name)
            requested = args.to_format or "same"
            suffix = source.suffix if requested == "same" else {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "avif": ".avif"}[requested]
            destination = output_root / relative.parent / f"{relative.stem}.rebuilt{suffix}"
            results.append(
                rebuild_image(
                    source,
                    destination=destination,
                    output_format=requested,
                    quality=args.quality,
                    jitter=args.jitter,
                    background=args.background,
                    preserve_icc=not args.drop_icc,
                )
            )
        except (OSError, ValueError) as exc:
            failures.append({"path": str(source), "error": str(exc)})
            if args.fail_fast:
                break
    if args.json:
        _json_dump({"results": [item.to_dict() for item in results], "failures": failures})
    else:
        for item in results:
            print(f"{item.source} -> {item.destination}: {item.output_format}")
        for failure in failures:
            print(f"error: {failure['path']}: {failure['error']}", file=sys.stderr)
    return 1 if failures else 0


def cmd_provenance(args: argparse.Namespace) -> int:
    report = inspect_provenance(args.path)
    if args.json:
        _json_dump(report.to_dict())
    else:
        print(f"provenance: {report.path}")
        for entry in report.structural_signals:
            print(_entry_line(entry))
        if not report.structural_signals:
            print("- no structural provenance signal recognized by MetaSift")
        print(f"c2pa-python: {report.c2pa_status}")
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    status = tool_status()
    if args.json:
        _json_dump(status)
    else:
        for name, info in status.items():
            state = "available" if info["available"] else "missing"
            detail = f" ({info['path']})" if info.get("path") else ""
            print(f"{name:14} {state:10}{detail} - {info['purpose']}")
    return 0


def cmd_formats(args: argparse.Namespace) -> int:
    matrix = capability_matrix()
    if args.json:
        _json_dump(matrix)
    else:
        for name, capabilities in matrix.items():
            print(f"{name.upper():10} inspect={capabilities.get('inspect')} sanitize={capabilities.get('sanitize')}")
    return 0


def _add_policy_arguments(parser: argparse.ArgumentParser, default: CleanMode) -> None:
    parser.add_argument("--mode", "--preset", dest="mode", choices=[mode.value for mode in CleanMode], default=default.value)
    parser.add_argument("--remove", action="append", help="remove an exact key or canonical selector; repeatable")
    parser.add_argument("--keep", action="append", help="preserve an exact key or canonical selector; repeatable")


def _add_rebuild_arguments(parser: argparse.ArgumentParser, *, default_to: str | None = None) -> None:
    parser.add_argument("path", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--to", dest="to_format", choices=["same", "jpeg", "png", "webp", "avif"], default=default_to)
    parser.add_argument("--quality", type=int, default=90, help="quality 85-95 for lossy-capable output formats")
    parser.add_argument("--jitter", type=int, default=0, help="opt-in RGB jitter from 0 to 2; not a detector-evasion guarantee")
    parser.add_argument("--background", type=_rgb, default=(255, 255, 255), help="RGB hex used only when converting transparency to JPEG")
    parser.add_argument("--drop-icc", action="store_true", help="discard ICC color profile even though rendering may change")
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metasift",
        description="Inspect, plan, sanitize, rebuild and verify file metadata and provenance.",
    )
    parser.add_argument("--version", action="version", version="MetaSift 0.3.0")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect", help="show recognized embedded metadata and evidence")
    inspect_parser.add_argument("paths", nargs="+", type=Path)
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(func=cmd_inspect)

    plan_parser = sub.add_parser("plan", help="show exactly what a sanitization policy would remove and preserve")
    plan_parser.add_argument("path", type=Path)
    _add_policy_arguments(plan_parser, CleanMode.SHARE_SAFE)
    plan_parser.add_argument("--json", action="store_true")
    plan_parser.set_defaults(func=cmd_plan)

    sanitize_parser = sub.add_parser("sanitize", help="losslessly sanitize metadata using a policy")
    sanitize_parser.add_argument("path", type=Path)
    _add_policy_arguments(sanitize_parser, CleanMode.SHARE_SAFE)
    sanitize_parser.add_argument("-o", "--output", type=Path)
    sanitize_parser.add_argument("--in-place", action="store_true")
    sanitize_parser.add_argument("--dry-run", action="store_true")
    sanitize_parser.add_argument("--json", action="store_true")
    sanitize_parser.set_defaults(func=_run_clean)

    clean_parser = sub.add_parser("clean", help="legacy alias for lossless sanitization")
    clean_parser.add_argument("path", type=Path)
    _add_policy_arguments(clean_parser, CleanMode.FULL)
    clean_parser.add_argument("-o", "--output", type=Path)
    clean_parser.add_argument("--in-place", action="store_true")
    clean_parser.add_argument("--dry-run", action="store_true")
    clean_parser.add_argument("--json", action="store_true")
    clean_parser.set_defaults(func=_run_clean)

    batch_parser = sub.add_parser("batch", help="sanitize files in a directory")
    batch_parser.add_argument("path", type=Path)
    batch_parser.add_argument("--recursive", action="store_true")
    _add_policy_arguments(batch_parser, CleanMode.SHARE_SAFE)
    batch_parser.add_argument("--output-dir", type=Path)
    batch_parser.add_argument("--in-place", action="store_true")
    batch_parser.add_argument("--fail-fast", action="store_true")
    batch_parser.add_argument("--json", action="store_true")
    batch_parser.set_defaults(func=cmd_batch)

    verify_parser = sub.add_parser("verify", help="verify that policy-targeted metadata is absent and run optional independent checks")
    verify_parser.add_argument("path", type=Path)
    _add_policy_arguments(verify_parser, CleanMode.SHARE_SAFE)
    verify_parser.add_argument("--no-independent", action="store_true")
    verify_parser.add_argument("--json", action="store_true")
    verify_parser.set_defaults(func=cmd_verify)

    provenance_parser = sub.add_parser("provenance", help="inspect structural provenance and optionally validate C2PA")
    provenance_parser.add_argument("path", type=Path)
    provenance_parser.add_argument("--json", action="store_true")
    provenance_parser.set_defaults(func=cmd_provenance)

    rebuild_parser = sub.add_parser("image-rebuild", help="decode and re-encode an image; same-format and zero-jitter by default")
    _add_rebuild_arguments(rebuild_parser, default_to="same")
    rebuild_parser.set_defaults(func=cmd_image_rebuild)

    image_clean_parser = sub.add_parser("image-clean", help="legacy rebuild alias; defaults to JPEG but jitter is now opt-in")
    _add_rebuild_arguments(image_clean_parser, default_to=None)
    image_clean_parser.set_defaults(func=cmd_image_clean)

    image_batch_parser = sub.add_parser("image-batch", help="rebuild supported raster images into an output directory")
    image_batch_parser.add_argument("path", type=Path)
    image_batch_parser.add_argument("--recursive", action="store_true")
    image_batch_parser.add_argument("--output-dir", type=Path, required=True)
    image_batch_parser.add_argument("--to", dest="to_format", choices=["same", "jpeg", "png", "webp", "avif"], default="same")
    image_batch_parser.add_argument("--quality", type=int, default=90)
    image_batch_parser.add_argument("--jitter", type=int, default=0)
    image_batch_parser.add_argument("--background", type=_rgb, default=(255, 255, 255))
    image_batch_parser.add_argument("--drop-icc", action="store_true")
    image_batch_parser.add_argument("--fail-fast", action="store_true")
    image_batch_parser.add_argument("--json", action="store_true")
    image_batch_parser.set_defaults(func=cmd_image_batch)

    formats_parser = sub.add_parser("formats", help="show the capability matrix")
    formats_parser.add_argument("--json", action="store_true")
    formats_parser.set_defaults(func=cmd_formats)

    doctor_parser = sub.add_parser("doctor", help="show optional verification and media backends")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2
