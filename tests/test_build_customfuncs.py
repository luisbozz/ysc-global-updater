"""The customfuncs sources must stay in sync with the payloads they build.

``build_customfuncs.py`` assembles ``scrasm/customfuncs/src/*.ysa`` back into
``scrpatches.json``. If the two ever drift apart, the readable source stops
describing what the game actually gets -- which is exactly how the scratch
globals ended up pointing outside their block. These tests pin the loop shut.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRP = ROOT / "scrpatches"
BUILD = SCRP / "build_customfuncs.py"
SRC = SCRP / "scrasm" / "customfuncs" / "src"
DATA = SCRP / "data" / "scrpatches.json"
DISASM = SCRP / "disasm"


def _run(*args, script=BUILD):
    return subprocess.run([sys.executable, str(script), *args],
                          cwd=script.parent, capture_output=True, text=True)


def _sandbox(tmp: Path) -> Path:
    """A throwaway copy of scrpatches/ that shares the (large) disasm corpus."""
    root = tmp / "repo"
    work = root / "scrpatches"
    (work / "scrasm").mkdir(parents=True)
    shutil.copy(ROOT / "versions.py", root / "versions.py")
    shutil.copy(BUILD, work / BUILD.name)
    shutil.copytree(SCRP / "data", work / "data")
    for item in (SCRP / "scrasm").iterdir():
        if item.name == "__pycache__":
            continue
        dst = work / "scrasm" / item.name
        shutil.copytree(item, dst) if item.is_dir() else shutil.copy(item, dst)
    (work / "disasm").symlink_to(DISASM)
    return work


def _have_corpus() -> bool:
    return DISASM.is_dir() and any(DISASM.iterdir()) and SRC.is_dir() \
        and any(SRC.glob("*.ysa"))


@unittest.skipUnless(_have_corpus(), "no disasm corpus or customfuncs sources")
class BuildCustomfuncsTest(unittest.TestCase):

    def test_sources_match_the_shipped_payloads(self):
        """Every .ysa assembles to exactly the bytes in scrpatches.json."""
        r = _run("--check")
        self.assertEqual(r.returncode, 0,
                         f"sources drifted from scrpatches.json:\n{r.stdout}{r.stderr}")
        self.assertIn("unchanged", r.stdout)
        self.assertNotIn("DIFF", r.stdout)

    def test_a_changed_source_is_reported_as_drift(self):
        """--check fails when a source no longer matches its payload."""
        victim = SRC / "fm_lts_creator.ysa"
        if not victim.exists():
            self.skipTest("fm_lts_creator.ysa not present")
        original = victim.read_text()
        self.assertIn("GLOBAL_U24_LOAD", original)
        try:
            victim.write_text(original.replace("PUSH_CONST_U8 0",
                                               "PUSH_CONST_U8 7", 1))
            r = _run("--check")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("DIFF fm_lts_creator", r.stdout)
        finally:
            victim.write_text(original)
        self.assertEqual(_run("--check").returncode, 0,
                         "failed to restore the source file")

    def test_report_mode_never_touches_the_data_file(self):
        """Without --write the payload file is left alone."""
        before = DATA.read_bytes()
        _run()
        _run("--check")
        self.assertEqual(DATA.read_bytes(), before)

    def test_write_applies_the_assembled_bytes(self):
        """--write puts the source's bytes into the matching patch entry."""
        with tempfile.TemporaryDirectory() as tmp:
            work = _sandbox(Path(tmp))
            victim = work / "scrasm" / "customfuncs" / "src" / "fm_lts_creator.ysa"
            if not victim.exists():
                self.skipTest("fm_lts_creator.ysa not present")
            victim.write_text(victim.read_text()
                              .replace("PUSH_CONST_U8 0", "PUSH_CONST_U8 7", 1))

            r = _run("--write", script=work / BUILD.name)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("DIFF fm_lts_creator", r.stdout)

            patched = json.loads((work / "data" / "scrpatches.json").read_text())
            entry = next(p for p in patched
                         if p.get("category") == "customfuncs"
                         and p["script_name"] == "fm_lts_creator"
                         and p["bytes_to_patch"].replace(" ", "").upper().startswith("2D"))
            # 25 00 (PUSH_CONST_U8 0) became 25 07 in the first bit test
            self.assertIn("25 07", entry["bytes_to_patch"])

            # and the result is now self-consistent
            self.assertEqual(_run("--check", script=work / BUILD.name).returncode, 0)

    def test_write_preserves_the_file_formatting(self):
        """--write must not reformat the whole payload file."""
        with tempfile.TemporaryDirectory() as tmp:
            work = _sandbox(Path(tmp))
            data = work / "data" / "scrpatches.json"
            before = data.read_text()
            victim = work / "scrasm" / "customfuncs" / "src" / "fm_lts_creator.ysa"
            victim.write_text(victim.read_text()
                              .replace("PUSH_CONST_U8 0", "PUSH_CONST_U8 7", 1))
            self.assertEqual(_run("--write", script=work / BUILD.name).returncode, 0)

            after = data.read_text()
            changed = [i for i, (a, b) in
                       enumerate(zip(before.splitlines(), after.splitlines())) if a != b]
            self.assertEqual(len(before.splitlines()), len(after.splitlines()))
            self.assertEqual(len(changed), 1,
                             f"expected 1 changed line, got {len(changed)}")

    def test_target_checks_a_deployed_copy_independently_of_data(self):
        """--target lets a deployed scrpatches.json be checked on its own.

        This is the gap that let a payload ship with corrupted internal CALL
        targets: data/scrpatches.json was correct, so a --check with no
        --target passed, while the deployed copy -- patched directly, out of
        band -- had already drifted. --target closes that gap by checking any
        file, not just data/.
        """
        with tempfile.TemporaryDirectory() as tmp:
            work = _sandbox(Path(tmp))
            deployed = work / "deployed_scrpatches.json"
            shutil.copy(work / "data" / "scrpatches.json", deployed)

            # a deployed copy still matching its source checks out ...
            r = _run("--check", "--target", str(deployed), script=work / BUILD.name)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            # ... and checking it must not be the same as checking data/: corrupt
            # only the deployed copy's injected bytes, data/ stays untouched.
            patches = json.loads(deployed.read_text())
            entry = next(p for p in patches
                         if p.get("category") == "customfuncs"
                         and p["script_name"] == "fm_lts_creator"
                         and p["bytes_to_patch"].replace(" ", "").upper().startswith("2D"))
            corrupted = bytearray(bytes.fromhex(entry["bytes_to_patch"].replace(" ", "")))
            corrupted[17] = 0xFF ^ corrupted[17]   # flip a byte inside an internal CALL
            entry["bytes_to_patch"] = corrupted.hex(" ").upper() + " "
            deployed.write_text(json.dumps(patches, indent=4) + "\n")

            r_deployed = _run("--check", "--target", str(deployed), script=work / BUILD.name)
            self.assertEqual(r_deployed.returncode, 1, r_deployed.stdout + r_deployed.stderr)
            self.assertIn("DIFF fm_lts_creator", r_deployed.stdout)

            r_data = _run("--check", script=work / BUILD.name)
            self.assertEqual(r_data.returncode, 0,
                             "corrupting the deployed copy must not affect data/'s own check")

    def test_every_injected_payload_has_a_source(self):
        """No injected customfuncs payload may exist without readable source."""
        patches = json.loads(DATA.read_text())
        injected = {p["script_name"] for p in patches
                    if p.get("category") == "customfuncs"
                    and "{" not in p.get("bytes_to_patch", "")
                    and p["bytes_to_patch"].replace(" ", "").upper().startswith("2D")}
        have = {p.stem for p in SRC.glob("*.ysa")}
        self.assertEqual(injected - have, set(),
                         "injected payloads without a .ysa source")


if __name__ == "__main__":
    unittest.main()
