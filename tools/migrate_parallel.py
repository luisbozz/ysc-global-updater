#!/usr/bin/env python3
"""Parallele kombinierte Migration (semantischer Pfad + infer-Kontext-Matching).

Der semantische Pfad ist quasi gratis; teuer ist nur das infer-Kontext-Matching
fuer code-accessed Familien (veh/doors/obj) und semantische Recall-Luecken sowie
Locals. Dieses Werkzeug verteilt genau diese infer-Aufrufe ueber mehrere Prozesse.

Ablauf ohne Logik-Duplikat:
  Pass 1  migrate_text mit einem "Sammler"-Fallback -> Liste der infer-Kandidaten
  Pool    infer_candidates je Kandidat parallel (N Worker, eigener Datei-Cache)
  Pass 2  migrate_text erneut, Fallback = Dict-Lookup der Pool-Ergebnisse

Sicher: liest nur, schreibt ein separates Ergebnis; Original-offsets.ini bleibt
unangetastet.
"""
import argparse
import functools
import json
import pathlib
import sys
from multiprocessing import Pool

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.migrate_offsets import (  # noqa: E402
    DEFAULT_INFER_FAMILIES,
    DEFAULT_STRUCT_FAMILIES,
    build_maps,
    migrate_text,
)

_OLD = _NEW = None


def _worker_init(old_dir: str, new_dir: str, file_cache: int) -> None:
    """Pro Worker: Pfade merken und die Datei-Caches von infer_offsets bewusst KLEIN halten.

    Wichtig gegen OOM: bei vollem Korpus (~1145 Dateien, teils ~40 MB) darf der
    Datei-Cache pro Worker nur wenige Dateien halten, sonst multipliziert sich der
    Speicher mit der Worker-Zahl. Kleiner Cache reicht, weil ein einzelner Offset
    ohnehin nur wenige Kandidatendateien wiederholt liest (lokale Lokalitaet).
    """
    global _OLD, _NEW
    _OLD, _NEW = pathlib.Path(old_dir), pathlib.Path(new_dir)
    from tools import infer_offsets as io
    # read_lines/read_text_cached halten DATEIINHALTE -> klein (file_cache).
    # candidate_files_for_value_cached haelt nur Trefferlisten (klein) -> etwas mehr.
    for fn, sz in (("read_lines", file_cache), ("read_text_cached", file_cache),
                   ("candidate_files_for_value_cached", 256)):
        f = getattr(io, fn, None)
        if f is not None and hasattr(f, "__wrapped__"):
            setattr(io, fn, functools.lru_cache(maxsize=sz)(f.__wrapped__))


def _worker(job):
    """job = (name, val) -> (name, val, migrierter Wert oder None)."""
    name, val = job
    from tools.infer_offsets import infer_candidates, present_value_for_ini
    try:
        _, cands = infer_candidates(_OLD, _NEW, val, name)
        nv = present_value_for_ini(val, cands[0].value, name) if cands else None
    except Exception:
        nv = None
    return name, val, nv


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ini", default="offsets.ini", help="Quell-offsets.ini (wird NICHT veraendert).")
    p.add_argument("--old-dir", required=True)
    p.add_argument("--new-dir", required=True)
    p.add_argument("--keep-list", default="reports/sources.keep.txt")
    p.add_argument("--out", default="reports/offsets.migrated.ini")
    p.add_argument("--report-json")
    p.add_argument("--jobs", type=int, default=0, help="Anzahl paralleler infer-Prozesse. 0 = automatisch nach freiem RAM.")
    p.add_argument("--file-cache", type=int, default=8, help="Max. gecachte Dateien pro Worker (klein halten gegen OOM).")
    p.add_argument("--mem-per-job-gb", type=float, default=2.5, help="Angenommener RAM-Bedarf pro Worker (GB) fuer die Auto-Job-Berechnung.")
    p.add_argument("--infer-families", default=",".join(DEFAULT_INFER_FAMILIES),
                   help="OFFSET-Praefixe (ohne 'OFFSET_'), die immer per infer migriert werden. Leer = aus.")
    p.add_argument("--structural", action="store_true",
                   help="Strukturellen Resolver (tools/structural.py) fuer code-accessed Familien vorschalten (schnell, in-process).")
    p.add_argument("--struct-families", default=",".join(DEFAULT_STRUCT_FAMILIES),
                   help="OFFSET-Praefixe fuer den strukturellen Resolver. Leer = aus.")
    args = p.parse_args()

    root = pathlib.Path(__file__).resolve().parents[1]

    def rel(x: str) -> pathlib.Path:
        return pathlib.Path(x) if pathlib.Path(x).is_absolute() else root / x

    ini_path, old_dir, new_dir = rel(args.ini), rel(args.old_dir), rel(args.new_dir)
    keep = [ln.strip() for ln in rel(args.keep_list).read_text(encoding="utf-8").splitlines() if ln.strip()]
    ini_text = ini_path.read_text(encoding="utf-8")
    infer_families = tuple(x for x in args.infer_families.split(",") if x)
    struct_families = tuple(x for x in args.struct_families.split(",") if x) if args.structural else ()

    structural = None
    if args.structural:
        from tools.structural import migrate_value as _struct_migrate

        def structural(name, val, scalar_only=False):
            try:
                return _struct_migrate(val, old_dir, new_dir, scalar_only=scalar_only)
            except Exception:
                return None

    print("Baue semantische Karten (alt/neu) ...", flush=True)
    _, old_rev = build_maps(old_dir, keep)
    new_fwd, _ = build_maps(new_dir, keep)

    # Pass 1: infer-Kandidaten einsammeln (Fallback liefert absichtlich None).
    needed: list = []

    def collector(name, val):
        needed.append((name, val))
        return None

    migrate_text(ini_text, old_rev, new_fwd, fallback=collector, infer_families=infer_families,
                 structural=structural, struct_families=struct_families)

    # Speicher-bewusste Worker-Zahl: nie mehr als (verfuegbarer RAM / mem-per-job).
    jobs = args.jobs
    if jobs <= 0:
        import os
        avail_gb = 8.0
        try:
            # verfuegbaren RAM aus /proc/meminfo (MemAvailable) lesen.
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        avail_gb = int(line.split()[1]) / 1024 / 1024
                        break
        except Exception:
            pass
        # 70 % des verfuegbaren RAM als Budget, mind. 1, max. CPU-Kerne.
        budget = max(1, int(avail_gb * 0.70 / max(args.mem_per_job_gb, 0.5)))
        jobs = max(1, min(budget, os.cpu_count() or 4))
        print(f"Auto-Jobs: {jobs} (verf. RAM ~{avail_gb:.0f}GB, ~{args.mem_per_job_gb}GB/Job)", flush=True)
    print(f"infer-Kandidaten: {len(needed)} | parallele Prozesse: {jobs} | Datei-Cache/Worker: {args.file_cache}", flush=True)

    # Pool: infer je Kandidat parallel. maxtasksperchild recycelt Worker -> Cache-
    # Aufbau wird periodisch freigegeben (zusaetzlicher OOM-Schutz).
    resolved: dict = {}
    if needed:
        with Pool(jobs, initializer=_worker_init,
                  initargs=(str(old_dir), str(new_dir), args.file_cache),
                  maxtasksperchild=200) as pool:
            done = 0
            for name, val, nv in pool.imap_unordered(_worker, needed, chunksize=4):
                resolved[(name, val)] = nv
                done += 1
                if done % 50 == 0:
                    print(f"  ...{done}/{len(needed)} infer fertig", flush=True)

    # Pass 2: Ergebnisse anwenden (Fallback = Dict-Lookup, keine Rechenlast mehr).
    def applier(name, val):
        return resolved.get((name, val))

    migrated_text, stats, changes = migrate_text(
        ini_text, old_rev, new_fwd, fallback=applier, infer_families=infer_families,
        structural=structural, struct_families=struct_families)

    out_path = rel(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(migrated_text, encoding="utf-8")

    total_global = sum(v for k, v in stats.items() if k != "skipped_non_global")
    print(f"geschrieben: {out_path}")
    print(f"Global-Offsets: {total_global} | " + " | ".join(f"{k}={v}" for k, v in sorted(stats.items())))

    if args.report_json:
        rp = rel(args.report_json)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps({
            "stats": stats,
            "infer_candidates": len(needed),
            "changes": [{"offset": n, "old": o, "new": nv} for n, o, nv in changes],
        }, indent=2) + "\n", encoding="utf-8")
        print(f"report: {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
