#!/usr/bin/env python3
"""Convert one PDF with MinerU and publish an exact two-file package."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target><[^>]+>|[^\s)]+)(?P<title>\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\)")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
REMOTE_SCHEMES = ("http://", "https://", "data:", "mailto:")
IMAGE_CHUNK_BYTES = 3 * 1024 * 1024  # multiple of 3 so Base64 output never pads mid-stream

# Repair PDF font artifacts (ligature glyphs emitted as PUA, dropped letters).
# Kept optional: a missing module must not break conversion.
try:
    from normalize_text import repair_text as _repair_text
except ImportError:  # pragma: no cover - defensive
    try:
        # When loaded via importlib without the scripts directory on sys.path.
        _here = str(Path(__file__).resolve().parent)
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from normalize_text import repair_text as _repair_text
    except ImportError:
        _repair_text = None


@dataclass(frozen=True)
class MarkdownStats:
    embedded_images: int
    output_bytes: int
    non_whitespace_characters: int
    text_repairs: int = 0


class ConversionError(RuntimeError):
    pass


def validate_pdf_signature(path: Path) -> None:
    """Reject files that only have a .pdf suffix but no PDF header."""
    with path.open("rb") as stream:
        header = stream.read(1024)
    if b"%PDF-" not in header:
        raise ConversionError(
            f"Input does not contain a PDF signature: {path}. "
            "Renaming another file to .pdf does not convert it."
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def write_embedded_markdown(
    source_markdown: Path,
    destination_markdown: Path,
    artifact_root: Path,
) -> MarkdownStats:
    """Stream MinerU Markdown into the destination, embedding local images as Base64.

    Reads the source line by line and writes straight to the destination, so peak
    memory stays proportional to one line plus one image chunk instead of the whole
    document and all images combined. Code fences, remote URLs, ``data:`` URIs and
    anchors are left untouched.
    """
    embedded_images = 0
    output_bytes = 0
    non_whitespace_characters = 0
    text_repairs = 0
    fence_marker: str | None = None

    def emit(fragment: str) -> None:
        nonlocal output_bytes, non_whitespace_characters
        output_bytes += len(fragment.encode("utf-8"))
        non_whitespace_characters += sum(not character.isspace() for character in fragment)

    def normalize(fragment: str) -> str:
        """Repair font artifacts outside code fences, accumulating the count."""
        nonlocal text_repairs
        if _repair_text is None or not fragment:
            return fragment
        fixed, report = _repair_text(fragment)
        text_repairs += report.total_fixes
        return fixed

    with (
        source_markdown.open("r", encoding="utf-8", errors="strict") as source,
        destination_markdown.open("w", encoding="utf-8", newline="\n") as destination,
    ):
        for line in source:
            fence = FENCE_RE.match(line)
            if fence:
                marker = fence.group(1)
                if fence_marker is None:
                    fence_marker = marker
                elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                    fence_marker = None
                emit(line)
                destination.write(line)
                continue

            if fence_marker is not None:
                emit(line)
                destination.write(line)
                continue

            line = normalize(line)
            position = 0
            for match in IMAGE_RE.finditer(line):
                emit(line[position : match.start()])
                destination.write(line[position : match.start()])

                raw_target = match.group("target")
                target = raw_target[1:-1] if raw_target.startswith("<") else raw_target
                title = match.group("title") or ""
                if target.lower().startswith(REMOTE_SCHEMES) or target.startswith(("#", "//")):
                    fragment = match.group(0)
                    emit(fragment)
                    destination.write(fragment)
                else:
                    image_path = (source_markdown.parent / target).resolve()
                    if not is_within(image_path, artifact_root):
                        raise ConversionError(f"Unsafe image path in Markdown: {target}")
                    if not image_path.is_file():
                        raise ConversionError(f"Referenced image was not generated: {target}")

                    embedded_images += 1
                    mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
                    prefix = f"![{match.group('alt')}](data:{mime};base64,"
                    suffix = f"{title})"
                    emit(prefix)
                    destination.write(prefix)
                    with image_path.open("rb") as image:
                        while True:
                            chunk = image.read(IMAGE_CHUNK_BYTES)
                            if not chunk:
                                break
                            encoded = base64.b64encode(chunk).decode("ascii")
                            emit(encoded)
                            destination.write(encoded)
                    emit(suffix)
                    destination.write(suffix)
                position = match.end()

            tail = line[position:]
            emit(tail)
            destination.write(tail)

    return MarkdownStats(
        embedded_images=embedded_images,
        output_bytes=output_bytes,
        non_whitespace_characters=non_whitespace_characters,
        text_repairs=text_repairs,
    )


def find_markdown(artifact_root: Path) -> Path:
    candidates = [path for path in artifact_root.rglob("*.md") if path.is_file()]
    if len(candidates) != 1:
        raise ConversionError(
            f"Expected exactly one Markdown file from MinerU, found {len(candidates)}."
        )
    return candidates[0]


def ensure_destination_available(output_dir: Path) -> None:
    if output_dir.exists():
        if not output_dir.is_dir():
            raise ConversionError(f"Output path is not a directory: {output_dir}")
        if any(output_dir.iterdir()):
            raise ConversionError(f"Output directory must be empty: {output_dir}")


def validate_diagnostic_log_target(path: Path) -> Path:
    """Validate the explicit diagnostic log target before conversion starts."""
    target = path.expanduser().resolve()
    if target.exists():
        raise ConversionError(
            f"Diagnostic log target already exists; refusing to overwrite: {target}"
        )
    parent = target.parent
    if not parent.is_dir():
        raise ConversionError(
            f"Diagnostic log parent directory does not exist: {parent}"
        )
    probe = parent / f".mineru-write-probe-{uuid.uuid4().hex[:8]}"
    try:
        probe.write_text("", encoding="utf-8")
    except OSError as exc:
        raise ConversionError(f"Diagnostic log directory is not writable: {parent}") from exc
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return target


def copy_diagnostic_log(source_log: Path, target: Path) -> None:
    """Copy the full MinerU log to the user-specified diagnostic file."""
    try:
        shutil.copyfile(source_log, target)
    except OSError as exc:
        raise ConversionError(
            f"Could not save the diagnostic log to {target}. No final package was published."
        ) from exc


def resolve_mineru(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_value = os.environ.get("MINERU_EXE")
    if env_value:
        candidates.append(Path(env_value).expanduser())
    home = Path.home()
    candidates.extend(
        [
            home / "mineru-env" / "Scripts" / "mineru.exe",
            home / ".mineru-env" / "Scripts" / "mineru.exe",
        ]
    )
    command = shutil.which("mineru")
    if command:
        candidates.append(Path(command))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ConversionError("MinerU is not installed. Run Install-MinerU.ps1 first.")


def diagnose_failure(details: str) -> str:
    """Return one sanitized action hint from local keyword classification only."""
    lowered = details.lower()
    if any(
        marker in lowered
        for marker in (
            "huggingface",
            "modelscope",
            "connectionerror",
            "connection error",
            "sslerror",
            "certificate verify failed",
            "name resolution",
        )
    ):
        return (
            "Action: Check internet/proxy/certificate settings. If Hugging Face is "
            "unreachable, retry with -ModelSource modelscope after user approval."
        )
    if any(marker in lowered for marker in ("no space left", "errno 28", "disk full")):
        return "Action: Free disk space on the environment, cache, and output drives, then retry."
    if any(marker in lowered for marker in ("cuda out of memory", "cudnn", "outofmemory")):
        return (
            "Action: Close GPU-heavy applications and retry. The skill already uses "
            "the pipeline backend, which can also run on CPU."
        )
    if any(marker in lowered for marker in ("encrypted", "needs a password", "password protected")):
        return "Action: Remove the PDF password with an authorized tool, then retry the unencrypted copy."
    if any(
        marker in lowered
        for marker in ("address already in use", "only one usage of each socket", "winerror 10048")
    ):
        return (
            "Action: A localhost port needed by MinerU is busy. Wait for another MinerU "
            "task to finish, or close only the stale MinerU task, then retry."
        )
    if any(marker in lowered for marker in ("permissionerror", "access is denied", "winerror 5")):
        return "Action: Choose a writable local folder or fix the file permission, then retry."
    return (
        "Action: Review the input PDF and conversion settings, or rerun with "
        "-DiagnosticLogPath to keep the full log for troubleshooting."
    )


def terminate_process_tree(process: subprocess.Popen[object]) -> None:
    """Terminate only the process tree created for this conversion."""
    if process.poll() is not None:
        return

    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_mineru(
    mineru: Path,
    staged_pdf: Path,
    artifact_root: Path,
    log_path: Path,
    *,
    heartbeat_seconds: float = 15.0,
    timeout_seconds: float | None = None,
    model_source: str = "auto",
    method: str = "auto",
    language: str | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
    formula: bool = True,
    table: bool = True,
) -> None:
    command = [
        str(mineru),
        "-p",
        str(staged_pdf),
        "-o",
        str(artifact_root),
        "-b",
        "pipeline",
        "-m",
        method,
    ]
    if language is not None:
        command.extend(("-l", language))
    if start_page is not None:
        command.extend(("-s", str(start_page)))
    if end_page is not None:
        command.extend(("-e", str(end_page)))
    if not formula:
        command.extend(("-f", "false"))
    if not table:
        command.extend(("-t", "false"))
    process_environment = os.environ.copy()
    process_environment.setdefault("PYTHONUTF8", "1")
    process_environment.setdefault("PYTHONIOENCODING", "utf-8")
    if model_source != "auto":
        process_environment["MINERU_MODEL_SOURCE"] = model_source

    print("Starting MinerU with the pipeline backend.", flush=True)
    started = time.monotonic()
    next_heartbeat = started + heartbeat_seconds
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )

    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=process_environment,
            creationflags=creation_flags,
        )
        try:
            while True:
                return_code = process.poll()
                if return_code is not None:
                    break

                now = time.monotonic()
                elapsed = now - started
                if timeout_seconds is not None and elapsed >= timeout_seconds:
                    terminate_process_tree(process)
                    raise ConversionError(
                        f"MinerU timed out after {elapsed / 60:.1f} minutes. "
                        "No final package was published."
                    )
                if heartbeat_seconds > 0 and now >= next_heartbeat:
                    print(
                        f"MinerU is still running ({elapsed / 60:.1f} minutes elapsed). "
                        "Do not start a duplicate conversion.",
                        flush=True,
                    )
                    next_heartbeat = now + heartbeat_seconds
                time.sleep(min(1.0, max(0.05, heartbeat_seconds)))
        except KeyboardInterrupt as exc:
            terminate_process_tree(process)
            raise ConversionError(
                "Conversion cancelled by the user. No final package was published."
            ) from exc

    if return_code != 0:
        details = log_path.read_text(encoding="utf-8", errors="replace")
        hint = diagnose_failure(details)
        raise ConversionError(
            f"MinerU failed with exit code {return_code}. {hint} "
            "Use -DiagnosticLogPath to keep the full log for troubleshooting."
        )


def verify_package(
    output_dir: Path,
    snapshot_sha: str,
    final_pdf: Path,
    final_md: Path,
) -> None:
    entries = list(output_dir.iterdir())
    files = [path for path in entries if path.is_file()]
    directories = [path for path in entries if path.is_dir()]
    if len(files) != 2 or directories:
        raise ConversionError("Final output is not the required two-file package.")
    if final_pdf not in files or final_md not in files:
        raise ConversionError("Final output filenames do not match the source PDF.")
    if sha256(final_pdf) != snapshot_sha:
        raise ConversionError("Copied PDF hash does not match the conversion snapshot.")
    if final_md.stat().st_size == 0:
        raise ConversionError("Generated Markdown is empty.")


def convert(
    pdf: Path,
    output_dir: Path,
    mineru: Path,
    *,
    heartbeat_seconds: float = 15.0,
    timeout_seconds: float | None = None,
    model_source: str = "auto",
    diagnostic_log_path: Path | None = None,
    method: str = "auto",
    language: str | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
    formula: bool = True,
    table: bool = True,
) -> tuple[Path, Path, MarkdownStats]:
    pdf = pdf.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not pdf.is_file():
        raise ConversionError(f"PDF does not exist: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        raise ConversionError(f"Input must be a PDF file: {pdf}")
    validate_pdf_signature(pdf)
    ensure_destination_available(output_dir)
    diagnostic_target = (
        validate_diagnostic_log_target(diagnostic_log_path)
        if diagnostic_log_path is not None
        else None
    )

    output_parent = output_dir.parent
    output_parent.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="mu-"))
    publish_dir = output_parent / f".{output_dir.name}.building-{uuid.uuid4().hex[:8]}"

    try:
        staged_pdf = work_root / "doc.pdf"
        artifact_root = work_root / "out"
        log_path = work_root / "mineru.log"
        shutil.copy2(pdf, staged_pdf)
        snapshot_sha = sha256(staged_pdf)
        try:
            run_mineru(
                mineru,
                staged_pdf,
                artifact_root,
                log_path,
                heartbeat_seconds=heartbeat_seconds,
                timeout_seconds=timeout_seconds,
                model_source=model_source,
                method=method,
                language=language,
                start_page=start_page,
                end_page=end_page,
                formula=formula,
                table=table,
            )
        except ConversionError:
            if diagnostic_target is not None:
                copy_diagnostic_log(log_path, diagnostic_target)
            raise
        if diagnostic_target is not None:
            copy_diagnostic_log(log_path, diagnostic_target)

        try:
            if not pdf.is_file():
                raise ConversionError(
                    "Source PDF became unavailable during conversion; "
                    "no final package was published. Confirm the input and retry."
                )
            current_sha = sha256(pdf)
        except OSError as exc:
            raise ConversionError(
                "Source PDF became unavailable during conversion; "
                "no final package was published. Confirm the input and retry."
            ) from exc
        if current_sha != snapshot_sha:
            raise ConversionError(
                "Source PDF changed during conversion; no final package was published. "
                "Confirm the input and retry."
            )

        raw_md = find_markdown(artifact_root)
        publish_dir.mkdir()
        final_pdf = publish_dir / pdf.name
        final_md = publish_dir / f"{pdf.stem}.md"
        shutil.copy2(staged_pdf, final_pdf)
        stats = write_embedded_markdown(raw_md, final_md, artifact_root)
        if stats.non_whitespace_characters == 0:
            raise ConversionError("MinerU generated an empty Markdown file.")
        verify_package(publish_dir, snapshot_sha, final_pdf, final_md)

        if output_dir.exists():
            output_dir.rmdir()
        os.replace(publish_dir, output_dir)
        return output_dir / pdf.name, output_dir / f"{pdf.stem}.md", stats
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
        if publish_dir.exists():
            shutil.rmtree(publish_dir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert one PDF with MinerU into an exact PDF + Markdown package."
    )
    parser.add_argument("--pdf", required=True, help="Source PDF path")
    parser.add_argument("--output", required=True, help="New or empty output directory")
    parser.add_argument("--mineru", help="Optional path to mineru.exe")
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=15.0,
        help="How often to print a still-running message (default: 15)",
    )
    parser.add_argument(
        "--timeout-minutes",
        type=float,
        default=0.0,
        help="Stop after this many minutes; 0 disables the timeout (default: 0)",
    )
    parser.add_argument(
        "--model-source",
        choices=("auto", "huggingface", "modelscope", "local"),
        default="auto",
        help="MinerU model source override (default: preserve automatic/configured behavior)",
    )
    parser.add_argument(
        "--diagnostic-log-path",
        help="Optional local file to keep the full MinerU log in; the parent "
        "directory must exist and the file must not already exist",
    )
    parser.add_argument(
        "--method",
        choices=("auto", "txt", "ocr"),
        default="auto",
        help="PDF parsing method (default: auto)",
    )
    parser.add_argument(
        "--language",
        choices=(
            "ch",
            "ch_server",
            "korean",
            "ta",
            "te",
            "ka",
            "th",
            "el",
            "arabic",
            "east_slavic",
            "cyrillic",
            "devanagari",
        ),
        help="Optional OCR language hint supported by the pipeline backend",
    )
    parser.add_argument("--start-page", type=int, help="First PDF page to parse, zero-based")
    parser.add_argument("--end-page", type=int, help="Last PDF page to parse, zero-based")
    parser.add_argument(
        "--disable-formula",
        action="store_true",
        help="Disable formula parsing",
    )
    parser.add_argument(
        "--disable-table",
        action="store_true",
        help="Disable table parsing",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.heartbeat_seconds <= 0:
        print("ERROR: --heartbeat-seconds must be greater than zero.", file=sys.stderr)
        return 2
    if args.timeout_minutes < 0:
        print("ERROR: --timeout-minutes cannot be negative.", file=sys.stderr)
        return 2
    if args.start_page is not None and args.start_page < 0:
        print("ERROR: --start-page cannot be negative.", file=sys.stderr)
        return 2
    if args.end_page is not None and args.end_page < 0:
        print("ERROR: --end-page cannot be negative.", file=sys.stderr)
        return 2
    if (
        args.start_page is not None
        and args.end_page is not None
        and args.end_page < args.start_page
    ):
        print("ERROR: --end-page cannot be before --start-page.", file=sys.stderr)
        return 2
    started = time.monotonic()
    try:
        source_pdf = Path(args.pdf)
        output_dir = Path(args.output)
        mineru = resolve_mineru(args.mineru)
        resolved_source = source_pdf.expanduser().resolve()
        if not resolved_source.is_file():
            raise ConversionError(f"PDF does not exist: {resolved_source}")
        print(f"Source PDF: {resolved_source}")
        print(f"Source size: {resolved_source.stat().st_size / (1024 * 1024):.2f} MB")
        timeout_seconds = args.timeout_minutes * 60 if args.timeout_minutes else None
        diagnostic_log_path = (
            Path(args.diagnostic_log_path) if args.diagnostic_log_path else None
        )
        final_pdf, final_md, stats = convert(
            source_pdf,
            output_dir,
            mineru,
            heartbeat_seconds=args.heartbeat_seconds,
            timeout_seconds=timeout_seconds,
            model_source=args.model_source,
            diagnostic_log_path=diagnostic_log_path,
            method=args.method,
            language=args.language,
            start_page=args.start_page,
            end_page=args.end_page,
            formula=not args.disable_formula,
            table=not args.disable_table,
        )
    except (ConversionError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    print("Conversion completed successfully.")
    print(f"PDF: {final_pdf}")
    print(f"Markdown: {final_md}")
    print(f"Elapsed: {elapsed / 60:.1f} minutes")
    print(f"Markdown size: {stats.output_bytes / (1024 * 1024):.2f} MB")
    print(f"Markdown non-whitespace characters: {stats.non_whitespace_characters}")
    print(f"Embedded images: {stats.embedded_images}")
    if stats.text_repairs:
        print(f"Text repairs (ligature/symbol artifacts): {stats.text_repairs}")
    print(f"PDF SHA-256: {sha256(final_pdf)}")
    print("Output contains exactly one PDF and one Markdown file.")
    print("Quality note: machine extraction must be checked against the source PDF.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
