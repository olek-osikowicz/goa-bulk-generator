import os
import sys
import json
from goamapper.generator import Generator
from goamapper.models import Poster
import logging as log
from pathlib import Path
from multiprocessing import Pool

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
        return path, None
    except Exception as e:
        return path, e.__str__()


def main():

    paths = [p for p in CONFIG_DIR.glob('**/*') if p.suffix == '.json']

    # to do make number of processes envariomental variable
    with Pool(processes=4) as pool:
        results = pool.map(generate_from_file, paths)

    fails = [r for r in results if r[1]]

    log.info("Printing fails:")
    for (path, msg) in fails:
        log.error(f"Generating {path} ended with {msg}")


if __name__ == "__main__":

    log.basicConfig(
        format='%(levelname)s:%(asctime)s: %(message)s', level=log.DEBUG)
    main()
