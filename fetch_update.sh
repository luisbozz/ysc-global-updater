#!/usr/bin/env bash
# fetch_update.sh — download the decompiled scripts + decrypted dumps for a GTA
# build into the versioned store, so the update tools can compare by version.
#
#   ./fetch_update.sh <build-label> [git-ref]
#   ./fetch_update.sh 1.73-3889              # git-ref defaults to 'senpai' (latest)
#   ./fetch_update.sh 1.72-3788 0b47ae4      # pin an older build by commit
#
# Writes:
#   scripts/<build>/*.c                    (8 creator/launcher scripts, for offsets)
#   scrpatches/disasm/<build>/*.ysc.full   (6 patched scripts, for scrpatches)
set -euo pipefail

build="${1:?usage: ./fetch_update.sh <build-label> [git-ref]   e.g. 1.73-3889 senpai}"
ref="${2:-senpai}"
root="$(cd "$(dirname "$0")" && pwd)"
scripts_dir="$root/scripts/$build"
dumps_dir="$root/scrpatches/disasm/$build"
base="https://raw.githubusercontent.com/calamity-inc/GTA-V-Decompiled-Scripts/$ref"

# .c for the offset updater (8), .ysc.full for scrpatches (the 6 patched scripts)
c_scripts="fm_capture_creator fm_deathmatch_creator fm_lts_creator fm_race_creator \
           fm_survival_creator fmmc_launcher public_mission_creator tuneables_processing"
full_scripts="fm_capture_creator fm_deathmatch_creator fm_lts_creator fm_race_creator \
              fm_survival_creator fmmc_launcher"

mkdir -p "$scripts_dir" "$dumps_dir"
echo "$ref" > "$dumps_dir/.ref"

echo "== decompiled .c  -> scripts/$build/"
for f in $c_scripts; do
  echo "   .c    $f"
  curl -fSL "$base/decompiled_scripts/$f.c" -o "$scripts_dir/$f.c"
done

echo "== decrypted .ysc.full -> scrpatches/disasm/$build/"
for f in $full_scripts; do
  echo "   .full $f"
  curl -fSL "$base/scripts/${f}_ysc/$f.ysc.full" -o "$dumps_dir/$f.ysc.full"
done

echo "done: build $build  (ref $ref)  ->  scripts/$build/ + scrpatches/disasm/$build/"
