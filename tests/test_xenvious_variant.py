"""Static checks on the Xenvious C# side.

The app is .NET Framework 4.8 with WPF, so it only builds on Windows and cannot
be compiled here. These tests do not replace the compiler. They cover the
failure modes that a compiler would not catch anyway, and that are easy to get
wrong when data files move:

  * an embedded resource whose file is missing, or a file that was never
    registered -- both build fine and fail at runtime,
  * a resource name that no longer matches the suffix the loader builds,
  * a build-specific file that exists for one variant and not the other,
  * a process name left hardcoded at a call site.
"""
import json
import os
import pathlib
import re
import unittest

XENVIOUS = pathlib.Path(os.environ.get("XENVIOUS_ROOT", "/opt/Xenvious"))
PROJ = XENVIOUS / "Xenvious"
CSPROJ = PROJ / "Xenvious.csproj"
OFFLINE = PROJ / "OfflineData"

HAVE_REPO = CSPROJ.is_file() and OFFLINE.is_dir()

VARIANTS = ("legacy", "enhanced")
PER_VARIANT = ("offsets.ini", "scrpatches.json", "scrpatchesdev.json")
SHARED = ("props.json", "vehicles.json", "weapons.json", "actors.json")

# Only two Enhanced patterns have been derived so far. The rest ship empty on
# purpose; see GTA.HasPattern.
ENHANCED_KNOWN_AOB = ("globalptr", "localptr")


def csproj_text() -> str:
    return CSPROJ.read_text(encoding="utf-8-sig")


def embedded_resources() -> list[str]:
    return re.findall(r'<EmbeddedResource Include="([^"]+)"', csproj_text())


def ini_section(text: str, name: str) -> dict:
    out, inside = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            inside = s == f"[{name}]"
            continue
        if not inside:
            continue
        m = re.match(r'^([A-Za-z0-9_]+)\s*=\s*(["\'])(.*)\2\s*$', s)
        if m:
            out[m.group(1)] = m.group(3)
    return out


def ini_keys(text: str, name: str) -> set:
    return set(ini_section(text, name))


@unittest.skipUnless(HAVE_REPO, f"no Xenvious checkout at {XENVIOUS}")
class EmbeddedResourceTest(unittest.TestCase):
    def test_every_registered_resource_exists(self):
        for rel in embedded_resources():
            path = PROJ / rel.replace("\\", "/")
            self.assertTrue(path.is_file(), f"registered but missing: {rel}")

    def test_every_offline_data_file_is_registered(self):
        registered = {r.replace("\\", "/") for r in embedded_resources()}
        for path in OFFLINE.rglob("*"):
            if not path.is_file() or path.suffix == ".cs":
                continue
            rel = path.relative_to(PROJ).as_posix()
            self.assertIn(rel, registered,
                          f"{rel} is on disk but not an EmbeddedResource: it would "
                          f"not be compiled into the exe")

    def test_both_variants_are_registered(self):
        registered = {r.replace("\\", "/") for r in embedded_resources()}
        for variant in VARIANTS:
            for name in PER_VARIANT:
                self.assertIn(f"OfflineData/{variant}/{name}", registered)

    def test_shared_content_is_registered_once_at_the_root(self):
        registered = {r.replace("\\", "/") for r in embedded_resources()}
        for name in SHARED:
            self.assertIn(f"OfflineData/{name}", registered)
            for variant in VARIANTS:
                self.assertNotIn(f"OfflineData/{variant}/{name}", registered,
                                 "shared game content must not be duplicated per variant")

    def test_game_variant_is_compiled(self):
        compiled = re.findall(r'<Compile Include="([^"]+)"', csproj_text())
        self.assertIn("GameVariant.cs", [c.replace("\\", "/") for c in compiled])


@unittest.skipUnless(HAVE_REPO, f"no Xenvious checkout at {XENVIOUS}")
class ResourceNameTest(unittest.TestCase):
    """The loader builds a suffix from the folder name; the layout must match."""

    def test_the_loader_suffix_matches_the_folder_layout(self):
        loader = (OFFLINE / "OfflineData.cs").read_text(encoding="utf-8-sig")
        self.assertIn('"OfflineData." + GameVariant.DataFolder(edition) + "." + fileName',
                      loader)
        variant_cs = (PROJ / "GameVariant.cs").read_text(encoding="utf-8-sig")
        for variant in VARIANTS:
            self.assertIn(f'"{variant}"', variant_cs,
                          f"DataFolder never yields {variant!r}")
            self.assertTrue((OFFLINE / variant).is_dir())

    def test_no_variant_folder_can_satisfy_a_shared_request(self):
        # LoadShared matches "OfflineData.<file>"; a resource in a variant
        # folder is named "OfflineData.<variant>.<file>" and cannot collide.
        for variant in VARIANTS:
            for name in PER_VARIANT:
                self.assertFalse(f"OfflineData.{variant}.{name}".endswith(
                    f"OfflineData.{name}"))


@unittest.skipUnless(HAVE_REPO, f"no Xenvious checkout at {XENVIOUS}")
class VariantDataTest(unittest.TestCase):
    def test_both_variants_carry_the_same_file_set(self):
        for variant in VARIANTS:
            found = {p.name for p in (OFFLINE / variant).iterdir() if p.is_file()}
            self.assertEqual(found, set(PER_VARIANT), f"{variant} file set differs")

    def test_enhanced_offsets_keep_every_legacy_key(self):
        legacy = (OFFLINE / "legacy" / "offsets.ini").read_text(
            encoding="utf-8", errors="replace")
        enhanced = (OFFLINE / "enhanced" / "offsets.ini").read_text(
            encoding="utf-8", errors="replace")
        for section in ("AOB", "OFFSETS", "SESSION"):
            self.assertEqual(ini_keys(legacy, section), ini_keys(enhanced, section),
                             f"[{section}] keys differ between the variants")

    def test_enhanced_offsets_actually_differ_from_legacy(self):
        legacy = (OFFLINE / "legacy" / "offsets.ini").read_text(
            encoding="utf-8", errors="replace")
        enhanced = (OFFLINE / "enhanced" / "offsets.ini").read_text(
            encoding="utf-8", errors="replace")
        lo, eo = ini_section(legacy, "OFFSETS"), ini_section(enhanced, "OFFSETS")
        changed = sum(1 for k in lo if lo[k] != eo.get(k))
        self.assertGreater(changed, 100,
                           "Enhanced offsets look like a copy of Legacy")

    def test_only_derived_enhanced_aob_patterns_are_populated(self):
        aob = ini_section((OFFLINE / "enhanced" / "offsets.ini").read_text(
            encoding="utf-8", errors="replace"), "AOB")
        for key, value in aob.items():
            if key in ENHANCED_KNOWN_AOB:
                self.assertTrue(value.strip(), f"{key} must carry a pattern")
            else:
                self.assertEqual(value.strip(), "",
                                 f"{key} still holds a Legacy pattern; it would scan "
                                 f"Enhanced and produce a pointer from address 0")

    def test_legacy_aob_patterns_are_all_populated(self):
        aob = ini_section((OFFLINE / "legacy" / "offsets.ini").read_text(
            encoding="utf-8", errors="replace"), "AOB")
        self.assertTrue(aob)
        for key, value in aob.items():
            self.assertTrue(value.strip(), f"legacy {key} is empty")

    def test_enhanced_patches_ship_parked(self):
        patches = json.loads((OFFLINE / "enhanced" / "scrpatches.json").read_text(
            encoding="utf-8"))
        self.assertTrue(patches)
        for p in patches:
            self.assertIs(p.get("enabled"), False,
                          f"{p.get('patch_name')!r} is enabled but was never verified "
                          f"against Enhanced bytecode")

    def test_legacy_patches_are_not_all_parked(self):
        patches = json.loads((OFFLINE / "legacy" / "scrpatches.json").read_text(
            encoding="utf-8"))
        self.assertTrue(any(p.get("enabled") is not False for p in patches))


@unittest.skipUnless(HAVE_REPO, f"no Xenvious checkout at {XENVIOUS}")
class ProcessNameTest(unittest.TestCase):
    """Only GameVariant may name a game process."""

    HARDCODED = re.compile(r'"(?:gta5|GTA5|GTA5_Enhanced)"')

    def test_no_call_site_hardcodes_a_process_name(self):
        offenders = []
        for path in PROJ.rglob("*.cs"):
            if path.name in ("GameVariant.cs", "Translation.cs"):
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if self.HARDCODED.search(line):
                    offenders.append(f"{path.relative_to(PROJ)}:{i}: {line.strip()[:80]}")
        self.assertEqual(offenders, [], "hardcoded process name(s):\n" + "\n".join(offenders))

    def test_game_variant_declares_both_process_names(self):
        text = (PROJ / "GameVariant.cs").read_text(encoding="utf-8-sig")
        self.assertIn('LegacyProcess = "GTA5"', text)
        self.assertIn('EnhancedProcess = "GTA5_Enhanced"', text)


@unittest.skipUnless(HAVE_REPO, f"no Xenvious checkout at {XENVIOUS}")
class EditedSourceTest(unittest.TestCase):
    """A crude structural check on the files this work edited.

    Not a parser: it only catches gross damage from a scripted edit, such as an
    unbalanced brace, which would otherwise only surface on a Windows build."""

    EDITED = ("GameVariant.cs", "GTA.cs", "MainWindow.xaml.cs",
              "OfflineData/OfflineData.cs", "Functions.cs", "OnlineEnable.cs",
              "Creator Classes/ScrPatchesRunner.cs")

    @staticmethod
    def _strip(text: str) -> str:
        text = re.sub(r'@"(?:[^"]|"")*"', '""', text)          # verbatim strings
        text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)        # normal strings
        text = re.sub(r"'(?:\\.|[^'\\])'", "' '", text)        # char literals
        text = re.sub(r"//[^\n]*", "", text)                   # line comments
        return re.sub(r"/\*.*?\*/", "", text, flags=re.S)      # block comments

    def test_braces_balance(self):
        for rel in self.EDITED:
            path = PROJ / rel
            self.assertTrue(path.is_file(), f"missing {rel}")
            code = self._strip(path.read_text(encoding="utf-8-sig", errors="replace"))
            self.assertEqual(code.count("{"), code.count("}"), f"{rel}: unbalanced braces")
            self.assertEqual(code.count("("), code.count(")"), f"{rel}: unbalanced parens")

    def test_the_pointer_guard_covers_every_aob_scan(self):
        text = (PROJ / "GTA.cs").read_text(encoding="utf-8-sig")
        scanned = set(re.findall(
            r"AOBScanModule2\(GTA\.Offsets\.Editor\.(AOB_\w+)", text))
        guarded = set(re.findall(
            r"HasPattern\(GTA\.Offsets\.Editor\.(AOB_\w+)", text))
        self.assertEqual(scanned - guarded, set(),
                         "these pointer scans run without checking that the build "
                         "has a pattern at all")

    def test_the_edition_is_detected_before_data_is_loaded(self):
        # Scoped to Window_Loaded: getOffsets() is also called from the timer,
        # which runs later and does its own detection, so a file-wide position
        # comparison would measure the wrong pair.
        text = (PROJ / "MainWindow.xaml.cs").read_text(encoding="utf-8-sig")
        start = text.index("private async void Window_Loaded(")
        end = text.index("#endregion", start)
        body = text[start:end]
        self.assertIn("GameVariant.Detect();", body)
        self.assertIn("await getOffsets();", body)
        self.assertLess(body.index("GameVariant.Detect();"),
                        body.index("await getOffsets();"),
                        "getOffsets() reads build-specific data, so the build must "
                        "be known first")

    def test_a_changed_edition_reloads_the_data(self):
        text = (PROJ / "MainWindow.xaml.cs").read_text(encoding="utf-8-sig")
        self.assertIn("GameVariant.DetectChanged()", text)

    def test_disabled_patches_are_skipped_by_the_runner(self):
        text = (PROJ / "Creator Classes" / "ScrPatchesRunner.cs").read_text(
            encoding="utf-8-sig")
        self.assertIn("if (!patch.enabled)", text)


if __name__ == "__main__":
    unittest.main()
