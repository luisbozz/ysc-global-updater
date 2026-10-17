#!/usr/bin/env python3
"""Downloads the blip icons Xenvious shows for script and vehicle pickups (Images/pickups).

The pickup props themselves are meshes with a white texture; the 2D icons a player sees are the
blip sprites. Which sprite belongs to which pickup type is in the mission controller
(func_10132 for rput, func_10133 for vput, 1.73); the images are the ones the FiveM docs render
from the game (docs.fivem.net/docs/game-references/blips), like Images/blips.
"""
from __future__ import annotations

import pathlib
import re
import urllib.request

PAGE = "https://docs.fivem.net/docs/game-references/blips/"
# Blip ids the controller uses for pickups, plus the cash and hacking icons for rput 13 / 14.
IDS = [403, 463, 468, 467, 469, 466, 464, 472, 465, 489, 586, 272, 875, 834,
       368, 354, 484, 487, 483, 485, 486, 515, 522, 544, 549, 545, 530, 531, 621, 622, 623, 624, 625]


def download(folder: pathlib.Path) -> None:
    html = urllib.request.urlopen(PAGE).read().decode("utf-8", "replace")
    urls = {}
    for url, i in re.findall(r'<img[^>]*src="([^"]*radar_[a-z0-9_]+\.png)"[^>]*>(?:(?!<img).){0,400}?\b(\d{1,3})\b', html, re.S):
        urls.setdefault(int(i), url)
    folder.mkdir(parents=True, exist_ok=True)
    for i in IDS:
        url = urls[i]
        urllib.request.urlretrieve(url, folder / url.rsplit("/", 1)[1])
    print(f"{len(IDS)} pickup icons in {folder}")


if __name__ == "__main__":
    import sys
    download(pathlib.Path(sys.argv[1]))
