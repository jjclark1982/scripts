#!/usr/bin/env python

from pathlib import Path
from collections import defaultdict
import glob


class PrefixGrouper:
    """
    Utility to group files by prefix
    """

    @classmethod
    def main_cli(cls):
        import argparse

        parser = argparse.ArgumentParser(
            # prog=cls.__name__,
            description=cls.__doc__
        )
        parser.add_argument('paths', nargs='+', type=Path, help='paths to process')
        parser.add_argument('--delimiter', default='_')
        parser.add_argument('--verbose', action='store_true', default=True)
        parser.add_argument('--quiet', dest='verbose', action='store_false')
        args = parser.parse_args()

        for path in args.paths:
            grouper = PrefixGrouper(path, **args.__dict__)
            grouper.run(**args.__dict__)


    def __init__(self, path, verbose=False, **kwargs):
        self.path = path

    def run(self, verbose=False, delimiter='_', **kwargs):
        files = map(Path, glob.iglob(glob.escape(str(self.path)) + "/*", recursive=True))
        prefix_groups = defaultdict(list)
        for file in files:
            if not file.is_file():
                continue
            parts = file.name.split(delimiter)
            if len(parts) > 1:
                prefix_groups[parts[0]].append(file)

        for [prefix, files] in prefix_groups.items():
            if len(prefix) < 1:
                continue
            if len(files) > 1:
                print(prefix, len(files))
                new_dir = self.path / prefix
                new_dir.mkdir(exist_ok=True)
                for file in files:
                    print(f"{file.name} -> {prefix}/{file.name}")
                    new_file = new_dir / file.name
                    if not new_file.exists():
                        file.move_into(new_dir)


if __name__ == "__main__":
    PrefixGrouper.main_cli()
