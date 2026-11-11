"""The single-file executables: their names, their version, their build and their release workflow.

The executables themselves are never started here - ``build-exe.py`` does that after every
build.  These tests need nothing but the standard library, so they also run on a machine without
OpenCV or ZBar.

Run: python3 tests/test_executable.py
"""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

#: How the Loresoft site reads a release asset: <app>-<os>-<arch>-<version>[-build<build>][.exe]
ASSET_PATTERN = re.compile(
    r"^display-bridge-(sender|receiver)-(linux|windows)-[a-z0-9_]+-\d+\.\d+\.\d+(-build\d+)?(\.exe)?$")


def _build_script():
    """Import ``build-exe.py`` (its name is not a module name)."""
    spec = importlib.util.spec_from_file_location("build_exe", str(ROOT / "build-exe.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExecutableName(unittest.TestCase):
    def setUp(self):
        self.build = _build_script()

    def test_the_name_carries_program_platform_and_version(self):
        cases = [
            (False, "x86_64", "sender", "display-bridge-sender-linux-x86_64-1.0.2-build30"),
            (False, "aarch64", "receiver", "display-bridge-receiver-linux-aarch64-1.0.2-build30"),
            (True, "AMD64", "receiver", "display-bridge-receiver-windows-x86_64-1.0.2-build30.exe"),
        ]
        for windows, machine, program, expected in cases:
            with self.subTest(program=program, machine=machine, windows=windows), \
                    mock.patch.object(self.build, "IS_WINDOWS", windows), \
                    mock.patch.object(self.build.platform, "machine", return_value=machine):
                name = self.build.executable_name(program, "1.0.2", 30)
                self.assertEqual(name, expected)
                self.assertRegex(name, ASSET_PATTERN)


class Version(unittest.TestCase):
    def setUp(self):
        self.build = _build_script()

    def test_the_environment_replaces_the_history(self):
        with mock.patch.dict(os.environ, {self.build.VERSION_VARIABLE: "2.3.4 56"}):
            self.assertEqual(self.build.build_version(), ("2.3.4", "56"))

    def test_a_malformed_override_is_refused(self):
        with mock.patch.dict(os.environ, {self.build.VERSION_VARIABLE: "banana"}):
            with self.assertRaises(SystemExit):
                self.build.build_version()

    def test_the_history_gives_patch_and_build(self):
        def git(*args):
            return {"rev-list --count HEAD": "30",
                    "rev-list --count HEAD -- python": "2",
                    "rev-parse --is-shallow-repository": "false"}[" ".join(args)]

        with mock.patch.dict(os.environ, clear=False), mock.patch.object(self.build, "_git", side_effect=git):
            os.environ.pop(self.build.VERSION_VARIABLE, None)
            self.assertEqual(self.build.build_version(),
                             ("{0}.{1}.2".format(self.build.MAJOR, self.build.MINOR), "30"))

    def test_a_shallow_clone_is_refused(self):
        def git(*args):
            return {"rev-list --count HEAD": "1", "rev-list --count HEAD -- python": "1",
                    "rev-parse --is-shallow-repository": "true"}[" ".join(args)]

        with mock.patch.dict(os.environ, clear=False), mock.patch.object(self.build, "_git", side_effect=git):
            os.environ.pop(self.build.VERSION_VARIABLE, None)
            with self.assertRaises(SystemExit):
                self.build.build_version()

    def test_no_history_is_refused(self):
        with mock.patch.dict(os.environ, clear=False), mock.patch.object(self.build, "_git", return_value=""):
            os.environ.pop(self.build.VERSION_VARIABLE, None)
            with self.assertRaises(SystemExit):
                self.build.build_version()


class Build(unittest.TestCase):
    def setUp(self):
        self.build = _build_script()

    def test_every_program_script_exists(self):
        for settings in self.build.PROGRAMS.values():
            self.assertTrue((ROOT / settings["script"]).is_file(), settings["script"])

    def test_the_spec_uses_the_script_and_the_hook_of_its_program(self):
        for program, settings in self.build.PROGRAMS.items():
            text = self.build.spec_text(program, "1.0.2", "30")
            self.assertIn(settings["script"].name, text)
            self.assertIn("hook-{0}.py".format(program), text)
            self.assertIn("console=False", text)
            for package in self.build.COLLECT_ALL:
                self.assertIn(package, text)

    def test_the_spec_names_windows_files_without_the_extension(self):
        with mock.patch.object(self.build, "IS_WINDOWS", True):
            text = self.build.spec_text("sender", "1.0.2", "30")
        self.assertIn("name='display-bridge-sender-windows-", text)
        self.assertNotIn(".exe'", text)

    def test_the_hooks_compile(self):
        for program in self.build.PROGRAMS:
            compile(self.build.hook_text(program, "1.0.2", "30"), "hook.py", "exec")

    def test_the_hook_answers_version_and_exits_before_the_program_runs(self):
        hook = self.build.hook_text("sender", "1.0.2", "30")
        hook = hook.replace("REQUIRED = " + repr(self.build.PROGRAMS["sender"]["required"]), "REQUIRED = []")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hook.py"
            path.write_text(hook + "\nprint('program ran')\n", encoding="utf-8")
            done = subprocess.run([sys.executable, str(path), "--version"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, check=False)
            self.assertEqual(done.returncode, 0)
            self.assertEqual(done.stdout.decode().strip(), "DisplayBridge Sender 1.0.2 (30)")

    def test_a_missing_library_stops_the_hook_with_a_message(self):
        hook = self.build.hook_text("receiver", "1.0.2", "30")
        hook = hook.replace("REQUIRED = " + repr(self.build.PROGRAMS["receiver"]["required"]),
                            "REQUIRED = ['no_such_library_xyz']")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hook.py"
            path.write_text(hook + "\nprint('program ran')\n", encoding="utf-8")
            done = subprocess.run([sys.executable, str(path), "--version"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, check=False)
        self.assertEqual(done.returncode, 1)
        self.assertIn("no_such_library_xyz", done.stderr.decode())
        self.assertNotIn("program ran", done.stdout.decode())

    def test_the_sender_starts_its_pool_workers_the_frozen_way(self):
        text = (ROOT / "python" / "displayBridgeSender.py").read_text(encoding="utf-8")
        guard = text.split('if __name__ == "__main__":')[1]
        self.assertIn("freeze_support()", guard)

    def test_build_output_is_ignored_by_git(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/build/", lines)
        self.assertIn("/dist/", lines)


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github" / "workflows" / "release-exe.yml").read_text(encoding="utf-8")

    def test_it_builds_on_both_systems(self):
        self.assertIn("ubuntu-22.04", self.text)
        self.assertIn("windows-latest", self.text)

    def test_it_needs_the_whole_history_for_the_version(self):
        self.assertGreaterEqual(self.text.count("fetch-depth: 0"), 2)

    def test_the_release_is_tagged_with_version_and_build(self):
        self.assertIn('TAG="v${VERSION}-build${BUILD}"', self.text)
        self.assertIn("build-exe.py --print-version", self.text)

    def test_it_uploads_what_the_build_writes(self):
        self.assertIn("dist/display-bridge-*", self.text)


if __name__ == "__main__":
    unittest.main()
