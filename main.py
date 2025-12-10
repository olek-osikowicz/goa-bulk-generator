import json
import logging as log
from pathlib import Path

from tqdm.contrib.concurrent import process_map

from goamapper.fetcher import Fetcher
from goamapper.generator import Generator
from goamapper.models import Poster

CONFIG_DIR = Path("config")


def generate_from_file(path: Path):
    try:
        log.debug(f"Opening file {path}")
        with open(path, encoding="utf8") as file:
            data = json.load(file)

        p = Poster(**data)
        g = Generator(p, overwrite=False)
        g.generate_svg()
        g.save_png()

    except Exception as e:
        log.error(f"Error generating map from {path}: {e}")


if __name__ == "__main__":
    log.basicConfig(format="%(levelname)s:%(asctime)s: %(message)s", level=log.DEBUG)
    Fetcher.ensure_water_polygons()
    paths = list(CONFIG_DIR.glob("**/a3.json"))
    process_map(generate_from_file, paths)
