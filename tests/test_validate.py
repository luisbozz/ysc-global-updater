import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

OLD_C = """
uParam0->f_1 = DATAFILE::DATADICT_CREATE_DICT(uParam0->f_0, "mission");
DATAFILE::DATADICT_SET_INT(uParam0->f_1, "trntype", Global_100.f_5);
DATAFILE::DATADICT_SET_INT(uParam0->f_1, "spd", Global_100.f_8);
"""
# Zielversion: trntype f_5 -> f_9, spd unveraendert.
NEW_C = OLD_C.replace("Global_100.f_5", "Global_100.f_9")


class ValidateHarnessTest(unittest.TestCase):
    def _run(self, extra, workdir):
        cmd = [sys.executable, str(ROOT / "tools" / "validate.py"),
               "--old-dir", str(workdir / "old"), "--new-dir", str(workdir / "new"),
               "--keep-list", str(workdir / "keep.txt")] + extra
        return subprocess.run(cmd, capture_output=True, text=True)

    def _setup(self, tmp):
        w = pathlib.Path(tmp)
        (w / "old").mkdir()
        (w / "new").mkdir()
        (w / "old" / "c.c").write_text(OLD_C, encoding="utf-8")
        (w / "new" / "c.c").write_text(NEW_C, encoding="utf-8")
        (w / "keep.txt").write_text("c.c\n", encoding="utf-8")
        (w / "src.ini").write_text('OFFSET_trntype = "Global_100.f_5"\n', encoding="utf-8")
        return w

    def test_internal_semantic_migration_reaches_full_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = self._setup(tmp)
            res = self._run(["--offsets", str(w / "src.ini"), "--min-coverage", "1.0"], w)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("100% coverage", res.stdout)

    def test_migrated_file_is_evaluated_against_expect(self):
        # Eine vorgefertigte (kombinierte) migrierte Datei wird direkt bewertet,
        # ohne intern neu zu migrieren. Sie stimmt mit der Referenz ueberein.
        with tempfile.TemporaryDirectory() as tmp:
            w = self._setup(tmp)
            (w / "migrated.ini").write_text('OFFSET_trntype = "Global_100.f_9"\n', encoding="utf-8")
            (w / "expect.ini").write_text('OFFSET_trntype = "Global_100.f_9"\n', encoding="utf-8")
            res = self._run([
                "--offsets", str(w / "src.ini"),
                "--migrated", str(w / "migrated.ini"),
                "--expect", str(w / "expect.ini"),
                "--min-agreement", "1.0",
            ], w)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("100% agreement", res.stdout)

    def test_migrated_disagreement_is_reported_and_fails_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = self._setup(tmp)
            # migrierte Datei weicht von der Referenz ab.
            (w / "migrated.ini").write_text('OFFSET_trntype = "Global_100.f_7"\n', encoding="utf-8")
            (w / "expect.ini").write_text('OFFSET_trntype = "Global_100.f_9"\n', encoding="utf-8")
            res = self._run([
                "--offsets", str(w / "src.ini"),
                "--migrated", str(w / "migrated.ini"),
                "--expect", str(w / "expect.ini"),
                "--min-agreement", "1.0",
            ], w)
            self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
            self.assertIn("echte Abweichungen       : 1", res.stdout)


if __name__ == "__main__":
    unittest.main()
