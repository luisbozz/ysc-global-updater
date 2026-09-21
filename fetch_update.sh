#!/usr/bin/env bash
# fetch_update.sh — download the decompiled scripts + decrypted dumps for a GTA
# build into the versioned store, so the update tools can compare by version.
#
#   ./fetch_update.sh [--variant legacy|enhanced] <build-label> [git-ref]
#
#   ./fetch_update.sh 1.73-3889                          # Legacy, ref 'senpai'
#   ./fetch_update.sh 1.72-3788 0b47ae4                  # an older Legacy build
#   ./fetch_update.sh --variant enhanced 1.73-1158       # Enhanced, ref 'main'
#
# GTA V ships as two separate games with independent build numbers, so each
# variant has its own upstream and its own folders. Enhanced labels are stored
# with an 'enhanced-' prefix; Legacy labels are stored unchanged, so everything
# fetched before the split keeps its path.
#
# Writes:
#   scripts/<label>/*.c                    (8 creator/launcher scripts, for offsets)
#   scrpatches/disasm/<label>/*.ysc.full   (6 patched scripts, for scrpatches)
#
# Enhanced has no .ysc.full upstream: the Enhanced repository publishes only
# decompiled .c. Offsets therefore work for both variants, scrpatches only for
# Legacy. The script says so rather than leaving an empty folder behind.
set -euo pipefail

variant="legacy"
if [ "${1:-}" = "--variant" ]; then
  variant="${2:?usage: --variant legacy|enhanced}"
  shift 2
fi
case "$variant" in
  legacy|enhanced) ;;
  *) echo "unknown variant '$variant' (legacy|enhanced)" >&2; exit 2 ;;
esac

build="${1:?usage: ./fetch_update.sh [--variant legacy|enhanced] <build-label> [git-ref]}"
root="$(cd "$(dirname "$0")" && pwd)"

if [ "$variant" = "enhanced" ]; then
  ref="${2:-main}"
  repo="acidlabsdev/gtav-enhanced-scripts"
  c_path="scripts"                       # flat: scripts/<name>.c
  case "$build" in enhanced-*) label="$build" ;; *) label="enhanced-$build" ;; esac
else
  ref="${2:-senpai}"
  repo="calamity-inc/GTA-V-Decompiled-Scripts"
  c_path="decompiled_scripts"
  label="$build"
fi
base="https://raw.githubusercontent.com/$repo/$ref"

scripts_dir="$root/scripts/$label"
dumps_dir="$root/scrpatches/disasm/$label"

# .c for the offset updater (8), .ysc.full for scrpatches (the 6 patched scripts)
c_scripts="fm_capture_creator fm_deathmatch_creator fm_lts_creator fm_race_creator \
           fm_survival_creator fmmc_launcher public_mission_creator tuneables_processing"
full_scripts="fm_capture_creator fm_deathmatch_creator fm_lts_creator fm_race_creator \
              fm_survival_creator fmmc_launcher"
# Scripts that hold a handful of offsets but do not belong in the corpus: the
# creator launch sequence flips flags in maintransition.c, which drives the
# transition into and out of a creator. Kept in a context/ subfolder so neither
# select_sources nor the corpus glob picks them up; verified_anchors reads them
# by name.
context_scripts="maintransition"

mkdir -p "$scripts_dir"

echo "== $variant build $label  (repo $repo, ref $ref)"
echo "== decompiled .c  -> scripts/$label/"
for f in $c_scripts; do
  echo "   .c    $f"
  curl -fsSL "$base/$c_path/$f.c" -o "$scripts_dir/$f.c"
done

mkdir -p "$scripts_dir/context"
echo "== context scripts -> scripts/$label/context/"
for f in $context_scripts; do
  echo "   .c    $f"
  curl -fsSL "$base/$c_path/$f.c" -o "$scripts_dir/context/$f.c"
done

if [ "$variant" = "enhanced" ]; then
  echo
  echo "== skipping .ysc.full: $repo publishes no decrypted dumps."
  echo "   scrpatches cannot be checked for Enhanced; offsets are unaffected."
  echo
  echo "done: $label  ->  scripts/$label/"
  exit 0
fi

mkdir -p "$dumps_dir"
echo "$ref" > "$dumps_dir/.ref"
echo "== decrypted .ysc.full -> scrpatches/disasm/$label/"
for f in $full_scripts; do
  echo "   .full $f"
  curl -fsSL "$base/scripts/${f}_ysc/$f.ysc.full" -o "$dumps_dir/$f.ysc.full"
done

echo "done: build $label  (ref $ref)  ->  scripts/$label/ + scrpatches/disasm/$label/"
