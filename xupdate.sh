#!/bin/sh
# Guided Xenvious update for a new GTA build. See xupdate/README.md.
#   ./xupdate.sh 1.74-4012            (Legacy)
#   ./xupdate.sh enhanced-1.74-1200   (Enhanced)
cd "$(dirname "$0")" && exec python3 -m xupdate "$@"
