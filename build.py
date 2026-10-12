#!/usr/bin/env python3
"""Build the public site; regenerate CV variants only on explicit local request."""
from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-only', action='store_true', help='Reuse the reviewed PDFs (CI mode).')
    parser.add_argument('--public-only', action='store_true', help='Rebuild only the English public CVs (any OS).')
    parser.add_argument('--private-out', type=Path, default=ROOT.parent / '简历', help='Where the Chinese CVs go (macOS STHeiti required).')
    args = parser.parse_args()
    private_out = args.private_out.resolve()
    if private_out == ROOT or ROOT in private_out.parents:
        parser.error('--private-out must be outside the entire public website repository')
    from build_site import build as build_site
    if not args.site_only:
        from build_cv import build as build_cv
        build_cv(ROOT / 'profile.json', ROOT / 'dist' / 'cv', None if args.public_only else private_out, public_only=args.public_only)
    build_site(json.loads((ROOT / 'profile.json').read_text(encoding='utf-8')), ROOT / 'dist', source_dir=ROOT)
    (ROOT / 'dist' / '.nojekyll').touch()
    subprocess.run([sys.executable, str(ROOT / 'check.py')], check=True)


if __name__ == '__main__':
    main()
