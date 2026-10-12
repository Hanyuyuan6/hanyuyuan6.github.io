#!/usr/bin/env python3
"""Weekly watch: open one GitHub issue when ORCID lists a DOI the site lacks.

Reads only public data (the ORCID public API) and profile.json. It never edits
the site; a person verifies the new work and adds it to profile.json.
Without GITHUB_TOKEN (local runs) it only prints what it would report.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ORCID = '0009-0005-7643-6331'
TITLE = 'New ORCID works to review for the website'


def get(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'hanyuyuan6-site-watch', **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def orcid_works() -> dict[str, str]:
    works = {}
    for group in get(f'https://pub.orcid.org/v3.0/{ORCID}/works')['group']:
        summary = group['work-summary'][0]
        title = summary['title']['title']['value']
        for ext in group.get('external-ids', {}).get('external-id', []):
            if ext['external-id-type'] == 'doi':
                works[ext['external-id-value'].lower()] = title
    return works


def main() -> int:
    profile = json.loads((ROOT / 'profile.json').read_text(encoding='utf-8'))
    known = {p['doi'].lower() for p in profile['publications']}
    missing = {doi: title for doi, title in orcid_works().items() if doi not in known}
    lines = [f'- [{title}](https://doi.org/{doi}) · `{doi}`' for doi, title in sorted(missing.items())]
    print(json.dumps({'orcid_dois_missing_from_site': sorted(missing)}, indent=2))
    token, repo = os.environ.get('GITHUB_TOKEN'), os.environ.get('GITHUB_REPOSITORY')
    if not missing or not token or not repo:
        return 0
    body = ('ORCID lists works that `profile.json` does not:\n\n' + '\n'.join(lines) +
            '\n\nVerify each record (authors, venue, year, DOI), add it to `profile.json`, rebuild with '
            '`python build.py`, review the regenerated CV pages, then close this issue.')
    api = f'https://api.github.com/repos/{repo}/issues'
    auth = {'Authorization': f'Bearer {token}', 'X-GitHub-Api-Version': '2022-11-28'}
    existing = [i for i in get(api + '?state=open&per_page=100', auth) if i['title'] == TITLE and 'pull_request' not in i]
    payload = json.dumps({'title': TITLE, 'body': body}).encode()
    target = existing[0]['url'] if existing else api
    request = urllib.request.Request(target, data=payload, method='PATCH' if existing else 'POST',
                                     headers={'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json', **auth})
    with urllib.request.urlopen(request, timeout=30) as response:
        print('issue', json.load(response)['html_url'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
