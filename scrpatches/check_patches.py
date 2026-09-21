#!/usr/bin/env python3
"""Patch-Health-Check fuer Xenvious scrpatches.

Prueft jeden AOB-Patch aus ``scrpatches.json`` gegen den ALTEN und den NEUEN
Script-Bytecode (calamity-inc ``*.ysc.full`` = entschluesselter Memory-Dump) und
meldet, welche Patches den Spiel-Update ueberlebt haben und welche gebrochen sind.

Kategorien pro Patch (Haupt-Pattern, analog fuer jedes ``values``-Subpattern):
- OK          : 1 Treffer alt, 1 Treffer neu  -> Pattern lebt (Wildcards fangen
                die Operand-Shifts; nichts zu tun).
- BROKEN      : Treffer alt, 0 Treffer neu     -> Instruktions-Sequenz geaendert,
                Pattern muss neu abgeleitet werden.
- AMBIG_NEW   : 1 alt, >1 neu                   -> Pattern jetzt mehrdeutig, praezisieren.
- AMBIG_OLD   : >1 alt                          -> war schon nicht eindeutig (fragil).
- NOT_IN_OLD  : 0 alt                           -> Pattern matcht nicht mal den alten
                Bytecode (falsche Alt-Version, Script-Variante oder totes Pattern).
- DISABLED    : ``"enabled": false``        -> Patch ist bewusst abgeschaltet und
                                                   wird von Xenvious gar nicht erst
                                                   angewendet. Wird nicht geprueft.
- REDERIVED   : 0 alt, 1 neu, ``derived_for``   -> Pattern wurde bewusst fuer genau
                == geprueftem neuen Build          diesen Build neu abgeleitet, weil
                                                   sich die Instruktionsform geaendert
                                                   hat. Alt und neu sind dann
                                                   inkompatibel; kein Pattern kann
                                                   beide treffen. Kein Handlungsbedarf.

``derived_for`` setzt man von Hand auf den Build, fuer den man das Pattern neu
abgeleitet hat (z. B. ``"derived_for": "1.73-3889"``), im Patch selbst oder in
einem einzelnen ``values``-Eintrag. Der Marker gilt nur fuer genau diesen Build:
beim naechsten Update ist er der ALTE Build, das Pattern trifft ihn wieder, und
die normalen Regeln greifen von selbst. Ein veralteter Marker schuetzt also nicht
versehentlich einen echten Bruch.

Die ``*.ysc.full`` liegen versioniert unter ``scrpatches/disasm/<build>/`` (z.B.
``disasm/1.73-3889/fm_lts_creator.ysc.full``); geladen werden sie mit
``./fetch_update.sh <build>``.
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import versions  # noqa: E402  (repo-root helper: disasm/<build> resolution)

DISASM = ROOT / "scrpatches" / "disasm"
PATCHES = ROOT / "scrpatches" / "data" / "scrpatches.json"


def aob_to_regex(pattern: str) -> "re.Pattern":
    """AOB-String (``2D 00 ? 5D ? ?``) -> Byte-Regex; ``?`` = beliebiges Byte."""
    out = b""
    for tok in pattern.split():
        out += b"." if tok == "?" else re.escape(bytes([int(tok, 16)]))
    return re.compile(out, re.DOTALL)


def _load(script: str, build: str, _cache: dict = {}) -> bytes:
    """``<script>.ysc.full`` aus ``disasm/<build>/`` laden (gecacht)."""
    key = (script, build)
    if key in _cache:
        return _cache[key]
    path = DISASM / build / f"{script}.ysc.full"
    if not path.is_file():
        raise SystemExit(f"missing {path}\n  -> fetch it:  ./fetch_update.sh {build} <git-ref>")
    data = path.read_bytes()
    _cache[key] = data
    return data


def _count(data: bytes, pattern: str) -> list:
    return [m.start() for m in aob_to_regex(pattern).finditer(data)]


def classify(n_old: int, n_new: int,
             derived_for: str | None = None, new_build: str | None = None) -> str:
    """Classify one pattern from its hit counts in the old and the new build.

    ``derived_for`` marks a pattern that was deliberately re-derived for a
    specific build. That happens when the compiler changed the *shape* of the
    instructions, not just their operands - the old and the new form are then
    incompatible and no single pattern can match both builds. Such a pattern
    legitimately has zero hits in the old build, so ``NOT_IN_OLD`` would be a
    false alarm. It is reported as ``REDERIVED`` instead.

    The marker only silences the alarm for the build it names. On the next
    update that build becomes the *old* one, the pattern matches it again, and
    the normal rules take over by themselves."""
    if derived_for and new_build and derived_for == new_build:
        # Judge on the new build alone. Whatever this pattern does or does not
        # hit in the old build says nothing: it was written against a different
        # instruction shape. Counting old hits here is worse than useless - a
        # re-derived pattern can coincidentally match one unrelated old site and
        # would then be reported OK, which looks like a verified result and is
        # not one.
        if n_new == 1:
            return "REDERIVED"
        return "BROKEN" if n_new == 0 else "AMBIG_NEW"
    if n_old == 0:
        return "NOT_IN_OLD"
    if n_old > 1:
        return "AMBIG_OLD"
    if n_new == 1:
        return "OK"
    if n_new == 0:
        return "BROKEN"
    return "AMBIG_NEW"


def is_healthy(status: str) -> bool:
    """A status that needs no action."""
    return status in ("OK", "REDERIVED", "DISABLED")


def stale_markers(patches: list, old_build: str, new_build: str) -> list:
    """``derived_for`` values naming a build that is neither end of this check.

    Such a marker does nothing - the pattern falls through to the normal rules,
    which compare it against an old build it was never written for. Usually a
    typo or a marker left over from a skipped update."""
    out = []
    for p in patches:
        for owner, entry in [(None, p)] + [(v.get("id"), v) for v in (p.get("values") or [])]:
            df = entry.get("derived_for")
            if df and df not in (old_build, new_build):
                label = p.get("patch_name", "?")
                if owner is not None:
                    label += f" values#{owner}"
                out.append((label, p.get("script_name", "?"), df))
    return out


def check(patches: list, old_build: str, new_build: str) -> list:
    """Pro Patch (und Subpattern) alt/neu-Treffer zaehlen + klassifizieren."""
    results = []
    for p in patches:
        script = p.get("script_name")
        pat = p.get("pattern")
        if not script or not pat:
            continue
        old = _load(script, old_build)
        new = _load(script, new_build)
        ho, hn = _count(old, pat), _count(new, pat)
        # enabled=false parks a patch: Xenvious skips it entirely, so a broken
        # pattern on it is not a problem to fix. Report it, do not classify it.
        status = ("DISABLED" if p.get("enabled") is False
                  else classify(len(ho), len(hn), p.get("derived_for"), new_build))
        entry = {
            "patch": p.get("patch_name", "?"), "script": script,
            "old_hits": len(ho), "new_hits": len(hn),
            "status": status,
            "values": [],
        }
        for v in p.get("values") or []:
            vp = v.get("pattern", "")
            vo, vn = _count(old, vp), _count(new, vp)
            entry["values"].append({
                "id": v.get("id"), "old_hits": len(vo), "new_hits": len(vn),
                "status": classify(len(vo), len(vn), v.get("derived_for"), new_build),
            })
        # Ein Patch mit gebrochenem value-Subpattern ist effektiv auch kaputt.
        if (entry["status"] != "DISABLED" and is_healthy(entry["status"])
                and any(not is_healthy(v["status"]) for v in entry["values"])):
            entry["status"] = "BROKEN"
        results.append(entry)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patches", default=str(PATCHES))
    ap.add_argument("--new", help="New build (e.g. 1.73-3889, 1.73, or 'latest'). "
                                  "Default: newest folder in disasm/.")
    ap.add_argument("--old", help="Old build the patterns match. Default: the build "
                                  "right before --new.")
    ap.add_argument("--report-json", help="Optionaler JSON-Report.")
    ap.add_argument("--only-issues", action="store_true",
                    help="Nur Patches zeigen, die NICHT OK sind.")
    args = ap.parse_args()

    new_build = versions.resolve(DISASM, args.new)
    old_build = (versions.resolve(DISASM, args.old) if args.old
                 else versions.previous(DISASM, new_build))
    if not old_build:
        raise SystemExit("no earlier build in disasm/ -- pass --old <build>")

    patches = json.loads(pathlib.Path(args.patches).read_text(encoding="utf-8"))
    results = check(patches, old_build, new_build)

    for label, script, df in stale_markers(patches, old_build, new_build):
        print(f"[WARN] derived_for={df!r} passt weder zu --old {old_build} noch zu "
              f"--new {new_build}: {label} [{script}]\n"
              f"       Marker wirkt nicht; Pattern wird gegen einen Build geprueft, "
              f"fuer den es nie geschrieben wurde.", file=sys.stderr)

    order = ["BROKEN", "AMBIG_NEW", "AMBIG_OLD", "NOT_IN_OLD",
             "REDERIVED", "DISABLED", "OK"]
    counts = {k: sum(1 for r in results if r["status"] == k) for k in order}

    bar = "=" * 78
    print("\n" + bar)
    print(f"  SCRPATCHES HEALTH CHECK   (old={old_build}  new={new_build})")
    print(bar)
    for k in order:
        print(f"  {k:11s}: {counts[k]:3d}")
    print(f"  {'TOTAL':11s}: {len(results):3d}")
    print("-" * 78)

    for k in order:
        if is_healthy(k) and args.only_issues:
            continue
        group = [r for r in results if r["status"] == k]
        if not group:
            continue
        print(f"\n[{k}]  ({len(group)})")
        for r in sorted(group, key=lambda r: (r["script"], r["patch"])):
            vinfo = ""
            if r["values"]:
                bad = [f"val#{v['id']}={v['status']}" for v in r["values"]
                       if not is_healthy(v["status"])]
                vinfo = ("  values: " + ", ".join(bad)) if bad else "  (values ok)"
            print(f"  {r['patch'][:44]:44s} {r['script']:22s} "
                  f"alt={r['old_hits']} neu={r['new_hits']}{vinfo}")

    if args.report_json:
        rp = pathlib.Path(args.report_json)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps({"counts": counts, "patches": results}, indent=2) + "\n",
                      encoding="utf-8")
        print(f"\nreport: {rp}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
