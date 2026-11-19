"""Generate the server's zone aliases from an IANA tzdata.zi file."""
import argparse
import json
from pathlib import Path


def generate(source):
    version = None
    links = {}
    for line in source.read_text(encoding='utf-8').splitlines():
        if line.startswith('# version '):
            version = line.split()[2]
        elif line.startswith('L '):
            _, current, old, *_ = line.split()
            if old != current:
                links[old] = current
    if version is None:
        raise ValueError('Missing tzdata.zi version')

    aliases = {}
    for old, current in links.items():
        seen = {old}
        while current in links:
            if current in seen:
                raise ValueError(f'Circular time zone link: {old}')
            seen.add(current)
            current = links[current]
        aliases[old] = current
    return {'version': version, 'aliases': aliases}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tzdata', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(generate(args.tzdata), indent=2, sort_keys=True) + '\n', encoding='utf-8')
