from __future__ import annotations

import base64
import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "convert_pdf.py"
POWERSHELL_CONVERTER = SCRIPT.with_name("Convert-PdfToMarkdown.ps1")
POWERSHELL_INSTALLER = SCRIPT.with_name("Install-MinerU.ps1")
SKILL_MD = SCRIPT.parents[1] / "SKILL.md"
OPENAI_YAML = SCRIPT.parents[1] / "agents" / "openai.yaml"
README = ROOT / "README.md"
SPEC = importlib.util.spec_from_file_location("convert_pdf", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["convert_pdf"] = MODULE  # dataclasses resolve the module by name
SPEC.loader.exec_module(MODULE)


def extract_powershell_function(text: str, name: str) -> str:
    """Return a single PowerShell function definition from a script by brace matching."""
    start = text.index(f"function {name}")
    open_brace = text.index("{", start)
    depth = 0
    for index in range(open_brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"function {name} not found in installer script")


class ConverterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="mineru-skill-test-"))
        self.source = self.temp / ("很长的中文文献名 含空格和逗号," * 4 + ".pdf")
        self.source.write_bytes(b"%PDF-1.4\nmock\n%%EOF")
        self.fake = self.temp / "fake-mineru.exe"
        self.fake.write_bytes(b"fake")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    @staticmethod
    def fake_run(mode: str = "success", after_artifact=None):
        def run(
            _mineru: Path,
            _pdf: Path,
            artifact_root: Path,
            log_path: Path,
            **_options,
        ) -> None:
            log_path.write_text("simulated MinerU log", encoding="utf-8")
            if mode == "fail":
                raise MODULE.ConversionError("MinerU failed with exit code 7.\nsimulated failure")
            if mode == "none":
                artifact_root.mkdir(parents=True)
                return

            result = artifact_root / "doc" / "auto"
            images = result / "images"
            images.mkdir(parents=True)
            (images / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"test-image")
            markdown = (
                "# Converted\n\n"
                "![chart](images/plot.png \"Figure 1\")\n\n"
                "![remote](https://example.com/a.png)\n\n"
                "```markdown\n![example](images/not-real.png)\n```\n"
            )
            (result / "doc.md").write_text(markdown, encoding="utf-8")
            (result / "middle.json").write_text("{}", encoding="utf-8")
            if mode == "multiple":
                (result / "extra.md").write_text("extra", encoding="utf-8")
            if after_artifact is not None:
                after_artifact()

        return run

    def test_success_embeds_images_and_publishes_only_two_files(self) -> None:
        output = self.temp / "out"
        with patch.object(MODULE, "run_mineru", self.fake_run()):
            final_pdf, final_md, stats = MODULE.convert(self.source, output, self.fake)

        self.assertEqual(stats.embedded_images, 1)
        self.assertEqual(sorted(path.suffix for path in output.iterdir()), [".md", ".pdf"])
        self.assertFalse(any(path.is_dir() for path in output.iterdir()))
        self.assertEqual(MODULE.sha256(self.source), MODULE.sha256(final_pdf))
        text = final_md.read_text(encoding="utf-8")
        self.assertIn("data:image/png;base64,", text)
        self.assertIn("https://example.com/a.png", text)
        self.assertIn("![example](images/not-real.png)", text)
        self.assertNotIn("images/plot.png", text)

    def test_nonempty_output_is_refused_without_changes(self) -> None:
        output = self.temp / "out"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(MODULE.ConversionError, "must be empty"):
            MODULE.convert(self.source, output, self.fake)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_mineru_failure_does_not_publish_output(self) -> None:
        output = self.temp / "out"
        with patch.object(MODULE, "run_mineru", self.fake_run("fail")):
            with self.assertRaisesRegex(MODULE.ConversionError, "exit code 7"):
                MODULE.convert(self.source, output, self.fake)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.temp.glob(".out.building-*")))

    def test_multiple_markdown_files_are_refused(self) -> None:
        output = self.temp / "out"
        with patch.object(MODULE, "run_mineru", self.fake_run("multiple")):
            with self.assertRaisesRegex(MODULE.ConversionError, "found 2"):
                MODULE.convert(self.source, output, self.fake)
        self.assertFalse(output.exists())

    def test_missing_markdown_is_refused(self) -> None:
        output = self.temp / "out"
        with patch.object(MODULE, "run_mineru", self.fake_run("none")):
            with self.assertRaisesRegex(MODULE.ConversionError, "found 0"):
                MODULE.convert(self.source, output, self.fake)
        self.assertFalse(output.exists())

    def test_non_pdf_is_rejected(self) -> None:
        source = self.temp / "document.txt"
        source.write_text("not a pdf", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ConversionError, "must be a PDF"):
            MODULE.convert(source, self.temp / "out", self.fake)

    def test_renamed_non_pdf_is_rejected_before_mineru_runs(self) -> None:
        source = self.temp / "renamed.pdf"
        source.write_text("This is not actually a PDF.", encoding="utf-8")

        with patch.object(MODULE, "run_mineru") as run:
            with self.assertRaisesRegex(MODULE.ConversionError, "PDF signature"):
                MODULE.convert(source, self.temp / "out", self.fake)

        run.assert_not_called()

    def test_source_replaced_during_conversion_fails_without_publish(self) -> None:
        output = self.temp / "out"
        replacement = b"%PDF-1.4\nchanged during conversion\n%%EOF"

        def replace_source() -> None:
            self.source.write_bytes(replacement)

        with patch.object(MODULE, "run_mineru", self.fake_run(after_artifact=replace_source)):
            with self.assertRaisesRegex(MODULE.ConversionError, "changed during conversion"):
                MODULE.convert(self.source, output, self.fake)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.temp.glob(".out.building-*")))

    def test_source_same_bytes_rewrite_publishes(self) -> None:
        output = self.temp / "out"
        original = self.source.read_bytes()

        def rewrite_source() -> None:
            self.source.write_bytes(original)

        with patch.object(MODULE, "run_mineru", self.fake_run(after_artifact=rewrite_source)):
            final_pdf, _final_md, _stats = MODULE.convert(self.source, output, self.fake)
        self.assertEqual(MODULE.sha256(self.source), MODULE.sha256(final_pdf))

    def test_source_deleted_during_conversion_fails_without_publish(self) -> None:
        output = self.temp / "out"

        def delete_source() -> None:
            self.source.unlink()

        with patch.object(MODULE, "run_mineru", self.fake_run(after_artifact=delete_source)):
            with self.assertRaisesRegex(MODULE.ConversionError, "unavailable during conversion"):
                MODULE.convert(self.source, output, self.fake)
        self.assertFalse(output.exists())

    def test_final_pdf_is_copied_from_staged_copy(self) -> None:
        output = self.temp / "out"
        calls: list[tuple[str, str]] = []
        original_copy2 = MODULE.shutil.copy2

        def recording_copy2(src, dst, *args, **kwargs):
            calls.append((str(src), str(dst)))
            return original_copy2(src, dst, *args, **kwargs)

        with (
            patch.object(MODULE, "run_mineru", self.fake_run()),
            patch.object(MODULE.shutil, "copy2", side_effect=recording_copy2),
        ):
            MODULE.convert(self.source, output, self.fake)

        final_copies = [src for src, dst in calls if dst.endswith(self.source.name)]
        self.assertEqual(len(final_copies), 1)
        self.assertEqual(Path(final_copies[0]).name, "doc.pdf")

    def test_write_embedded_markdown_stats_match_written_file(self) -> None:
        root = self.temp / "artifacts"
        result = root / "doc" / "auto"
        images = result / "images"
        images.mkdir(parents=True)
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"test-image"
        (images / "plot.png").write_bytes(image_bytes)
        source = result / "doc.md"
        source.write_text(
            "# Converted\n\n![chart](images/plot.png \"Figure 1\")\n\n"
            "![remote](https://example.com/a.png)\n",
            encoding="utf-8",
        )
        destination = self.temp / "out.md"

        stats = MODULE.write_embedded_markdown(source, destination, root)

        written = destination.read_bytes()
        self.assertEqual(stats.output_bytes, len(written))
        text = written.decode("utf-8")
        self.assertEqual(
            stats.non_whitespace_characters,
            sum(not character.isspace() for character in text),
        )
        self.assertEqual(stats.embedded_images, 1)
        self.assertEqual(text.count("data:image/png;base64,"), 1)
        match = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", text)
        self.assertIsNotNone(match)
        self.assertEqual(base64.b64decode(match.group(1)), image_bytes)

    def test_multichunk_image_base64_is_continuous_without_newlines(self) -> None:
        root = self.temp / "artifacts"
        result = root / "doc" / "auto"
        images = result / "images"
        images.mkdir(parents=True)
        image_bytes = bytes(range(256)) * 17  # 4352 bytes, larger than the patched 9-byte chunk
        (images / "plot.png").write_bytes(image_bytes)
        source = result / "doc.md"
        source.write_text("![x](images/plot.png)\n", encoding="utf-8")
        destination = self.temp / "out.md"

        with patch.object(MODULE, "IMAGE_CHUNK_BYTES", 9):
            stats = MODULE.write_embedded_markdown(source, destination, root)

        text = destination.read_text(encoding="utf-8")
        self.assertEqual(stats.embedded_images, 1)
        match = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)\)", text)
        self.assertIsNotNone(match)
        self.assertNotIn("\n", match.group(1))
        self.assertEqual(base64.b64decode(match.group(1)), image_bytes)

    def test_code_fences_and_remote_urls_survive_streaming_embed(self) -> None:
        root = self.temp / "artifacts"
        result = root / "doc" / "auto"
        images = result / "images"
        images.mkdir(parents=True)
        (images / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"test-image")
        source = result / "doc.md"
        source.write_text(
            "![chart](images/plot.png \"Figure 1\")\n\n"
            "![remote](https://example.com/a.png)\n\n"
            "```markdown\n![example](images/not-real.png)\n```\n",
            encoding="utf-8",
        )
        destination = self.temp / "out.md"

        stats = MODULE.write_embedded_markdown(source, destination, root)

        text = destination.read_text(encoding="utf-8")
        self.assertEqual(stats.embedded_images, 1)
        self.assertIn("https://example.com/a.png", text)
        self.assertIn("![example](images/not-real.png)", text)
        self.assertNotIn("images/plot.png", text)
        self.assertIn("data:image/png;base64,", text)

    def test_unsafe_image_path_is_rejected(self) -> None:
        root = self.temp / "artifacts"
        root.mkdir()
        markdown_dir = root / "result"
        markdown_dir.mkdir()
        outside = self.temp / "outside.png"
        outside.write_bytes(b"outside")
        source = markdown_dir / "doc.md"
        source.write_text("![](../../outside.png)\n", encoding="utf-8")

        with self.assertRaisesRegex(MODULE.ConversionError, "Unsafe image path"):
            MODULE.write_embedded_markdown(source, markdown_dir / "out.md", root)

    def test_diagnostic_log_is_saved_when_requested(self) -> None:
        output = self.temp / "out"
        diag = self.temp / "diag" / "mineru.log"
        diag.parent.mkdir()
        with patch.object(MODULE, "run_mineru", self.fake_run()):
            MODULE.convert(self.source, output, self.fake, diagnostic_log_path=diag)
        self.assertTrue(diag.is_file())
        self.assertIn("simulated MinerU log", diag.read_text(encoding="utf-8"))

    def test_diagnostic_log_target_already_exists_fails_before_mineru(self) -> None:
        output = self.temp / "out"
        diag = self.temp / "existing.log"
        diag.write_text("keep", encoding="utf-8")

        with patch.object(MODULE, "run_mineru") as run:
            with self.assertRaisesRegex(MODULE.ConversionError, "already exists"):
                MODULE.convert(self.source, output, self.fake, diagnostic_log_path=diag)
        run.assert_not_called()
        self.assertFalse(output.exists())
        self.assertEqual(diag.read_text(encoding="utf-8"), "keep")

    def test_diagnostic_log_parent_missing_fails_fast(self) -> None:
        output = self.temp / "out"
        diag = self.temp / "no-such-parent" / "mineru.log"

        with patch.object(MODULE, "run_mineru") as run:
            with self.assertRaisesRegex(MODULE.ConversionError, "parent"):
                MODULE.convert(self.source, output, self.fake, diagnostic_log_path=diag)
        run.assert_not_called()

    def test_diagnostic_log_is_saved_even_on_mineru_failure(self) -> None:
        output = self.temp / "out"
        diag = self.temp / "diag.log"

        with patch.object(MODULE, "run_mineru", self.fake_run("fail")):
            with self.assertRaisesRegex(MODULE.ConversionError, "exit code 7"):
                MODULE.convert(self.source, output, self.fake, diagnostic_log_path=diag)
        self.assertTrue(diag.is_file())
        self.assertIn("simulated MinerU log", diag.read_text(encoding="utf-8"))
        self.assertFalse(output.exists())

    def test_failure_message_never_contains_raw_log_content(self) -> None:
        class FakeProcess:
            pid = 5555

            @staticmethod
            def poll():
                return 1

        def make_fake_process(*args, **kwargs):
            kwargs["stdout"].write("TRACE C:\\Users\\secret\\paper.pdf body-token-abc123\n")
            kwargs["stdout"].flush()
            return FakeProcess()

        with (
            patch.object(MODULE.subprocess, "Popen", side_effect=make_fake_process),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(MODULE.ConversionError) as raised:
                MODULE.run_mineru(
                    self.fake,
                    self.source,
                    self.temp / "artifacts",
                    self.temp / "mineru.log",
                )

        message = str(raised.exception)
        self.assertNotIn("secret", message)
        self.assertNotIn("token-abc123", message)
        self.assertNotIn("paper.pdf", message)
        self.assertNotIn("TRACE", message)
        self.assertIn("MinerU failed with exit code 1", message)

    def test_failure_diagnostics_offer_actionable_hints(self) -> None:
        cases = {
            "ConnectionError while downloading from huggingface.co": "ModelSource",
            "OSError: [Errno 28] No space left on device": "disk space",
            "CUDA out of memory": "GPU",
            "document is encrypted and needs a password": "password",
            "Only one usage of each socket address is normally permitted": "localhost",
            "PermissionError: Access is denied": "permission",
        }
        for details, expected in cases.items():
            with self.subTest(details=details):
                self.assertIn(expected, MODULE.diagnose_failure(details))

    def test_main_reports_a_missing_pdf_without_a_raw_os_error(self) -> None:
        missing = self.temp / "missing.pdf"
        stderr = io.StringIO()
        stdout = io.StringIO()
        with (
            patch.object(
                MODULE.sys,
                "argv",
                [
                    "convert_pdf.py",
                    "--pdf",
                    str(missing),
                    "--output",
                    str(self.temp / "out"),
                    "--mineru",
                    str(self.fake),
                ],
            ),
            patch("sys.stderr", stderr),
            redirect_stdout(stdout),
        ):
            exit_code = MODULE.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("PDF does not exist", stderr.getvalue())
        self.assertNotIn("WinError", stderr.getvalue())


class ProcessRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="mineru-runner-test-"))
        self.mineru = self.temp / "mineru.exe"
        self.pdf = self.temp / "doc.pdf"
        self.out = self.temp / "out"
        self.log = self.temp / "mineru.log"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_runner_logs_to_file_and_prints_heartbeat(self) -> None:
        class FakeProcess:
            pid = 4321

            def __init__(self) -> None:
                self.polls = iter((None, 0))

            def poll(self):
                return next(self.polls)

        fake_process = FakeProcess()
        output = io.StringIO()
        with (
            patch.object(MODULE.subprocess, "Popen", return_value=fake_process) as popen,
            patch.object(MODULE.time, "monotonic", side_effect=(10.0, 26.0)),
            patch.object(MODULE.time, "sleep"),
            redirect_stdout(output),
        ):
            MODULE.run_mineru(
                self.mineru,
                self.pdf,
                self.out,
                self.log,
                heartbeat_seconds=15,
            )

        kwargs = popen.call_args.kwargs
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.STDOUT)
        self.assertIsNot(kwargs["stdout"], subprocess.PIPE)
        self.assertIn("still running", output.getvalue())

    def test_runner_terminates_its_process_tree_after_timeout(self) -> None:
        class FakeProcess:
            pid = 9876

            @staticmethod
            def poll():
                return None

        fake_process = FakeProcess()
        with (
            patch.object(MODULE.subprocess, "Popen", return_value=fake_process),
            patch.object(MODULE.time, "monotonic", side_effect=(0.0, 61.0)),
            patch.object(MODULE, "terminate_process_tree") as terminate,
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(MODULE.ConversionError, "timed out"):
                MODULE.run_mineru(
                    self.mineru,
                    self.pdf,
                    self.out,
                    self.log,
                    heartbeat_seconds=15,
                    timeout_seconds=60,
                )

        terminate.assert_called_once_with(fake_process)


@unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell is required")
class PowerShellContractTests(unittest.TestCase):
    @staticmethod
    def script_parameters(path: Path) -> set[str]:
        escaped = str(path).replace("'", "''")
        command = (
            f"$ErrorActionPreference='Stop'; "
            f"(Get-Command -Name '{escaped}').Parameters.Keys -join \"`n\""
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return set(result.stdout.splitlines())

    def test_converter_exposes_environment_progress_and_model_parameters(self) -> None:
        parameters = self.script_parameters(POWERSHELL_CONVERTER)
        self.assertTrue(
            {"EnvironmentPath", "HeartbeatSeconds", "TimeoutMinutes", "ModelSource",
             "DiagnosticLogPath"}
            <= parameters
        )

    def test_installer_exposes_explicit_model_download_controls(self) -> None:
        parameters = self.script_parameters(POWERSHELL_INSTALLER)
        self.assertTrue({"DownloadModels", "ModelSource"} <= parameters)

    def test_installer_avoids_known_unsafe_and_incompatible_patterns(self) -> None:
        text = POWERSHELL_INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn('"-3.13"', text)
        self.assertNotIn("Invoke-Expression", text)
        self.assertNotRegex(
            text,
            r"Remove-Item\s+-LiteralPath\s+\$EnvironmentPath[^\r\n]*-Recurse",
        )
        self.assertNotIn("mineru[pipeline]>=3.4.4,<3.5", text)

    def test_installer_uses_hashed_uv_assets_and_locked_install(self) -> None:
        text = POWERSHELL_INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("install.ps1", text)
        self.assertNotIn("Invoke-Expression", text)
        self.assertRegex(text, r'"[0-9A-Fa-f]{64}"')
        self.assertIn("x86_64-pc-windows-msvc", text)
        self.assertIn("aarch64-pc-windows-msvc", text)
        self.assertIn("--require-hashes", text)
        self.assertIn("requirements.lock", text)

    def test_lock_files_exist_with_hashes_and_exact_pin(self) -> None:
        skill_root = POWERSHELL_INSTALLER.parents[1]
        lock = skill_root / "requirements.lock"
        pin = skill_root / "requirements.in"
        self.assertTrue(pin.is_file())
        self.assertTrue(lock.is_file())
        text = lock.read_text(encoding="utf-8")
        self.assertIn("--hash=sha256:", text)
        self.assertRegex(text, r"(?m)^mineru==3\.4\.4")

    def test_mineru_exact_version_boundaries(self) -> None:
        text = POWERSHELL_INSTALLER.read_text(encoding="utf-8")
        function = extract_powershell_function(text, "Test-ExactMinerUVersion")
        script = (
            "$PinnedMinerUVersion = '3.4.4'\n"
            + function
            + "\n@('3.4.3','3.4.4','3.5.0') | ForEach-Object { "
            + "if (Test-ExactMinerUVersion $_) { 'yes' } else { 'no' } }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.stdout.splitlines(), ["no", "yes", "no"])

    def test_installer_detects_windows_without_relying_on_os_environment_variable(self) -> None:
        environment = os.environ.copy()
        environment.pop("OS", None)
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(POWERSHELL_INSTALLER),
                "-EnvironmentPath",
                str(Path.home()),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        diagnostic = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing unsafe EnvironmentPath", diagnostic)
        self.assertNotIn("supports Windows only", diagnostic)

    def test_converter_preserves_chinese_paths_in_utf8_console_output(self) -> None:
        environment_path = Path.home() / "mineru-env"
        if not (environment_path / "Scripts" / "mineru.exe").is_file():
            self.skipTest("A default MinerU environment is required for the encoding check")

        missing_pdf = Path(tempfile.gettempdir()) / "不存在的中文文件.pdf"
        output = Path(tempfile.gettempdir()) / "不会创建的输出目录"
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(POWERSHELL_CONVERTER),
                "-PdfPath",
                str(missing_pdf),
                "-OutputDirectory",
                str(output),
                "-EnvironmentPath",
                str(environment_path),
            ],
            check=False,
            capture_output=True,
        )
        diagnostic = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不存在的中文文件.pdf", diagnostic)
        self.assertNotIn("�", diagnostic)


class SkillDocumentationTests(unittest.TestCase):
    def test_description_is_trigger_only_and_starts_with_use_when(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^description: Use when ")

    def test_skill_is_self_sufficient_for_approval_and_quality_boundaries(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        for required in ("-DownloadModels", "20 GB", "machine extraction", "Heartbeat"):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_skill_documents_snapshot_lock_sanitization_and_streaming(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        for required in ("SHA-256", "DiagnosticLogPath", "requirements.lock"):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_readme_has_utf8_validation_and_model_download_command(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertIn("PYTHONUTF8", text)
        self.assertIn("-DownloadModels", text)

    def test_readme_documents_lock_and_diagnostic_log(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertIn("requirements.lock", text)
        self.assertIn("-DiagnosticLogPath", text)

    def test_openai_prompt_names_the_skill(self) -> None:
        text = OPENAI_YAML.read_text(encoding="utf-8")
        self.assertIn("$mineru-pdf-to-markdown", text)


if __name__ == "__main__":
    unittest.main()
