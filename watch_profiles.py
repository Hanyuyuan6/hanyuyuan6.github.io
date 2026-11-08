#!/usr/bin/env python3
"""Weekly watch: compare public profiles with profile.json and open one issue on drift.

profile.json is the single source of truth. This script reads what public APIs expose,
ORCID (works, biography, keywords, links, public emails, other names, affiliations,
peer reviews) and GitHub (profile fields, the profile README and each listed repository's
About line), and reports every
field that no longer matches. The issue holds only those public values; for platforms
without a public API, sync_platforms.py lists the values locally. It never edits a
platform. Without GITHUB_TOKEN it only prints.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ORCID = '0009-0005-7643-6331'
GITHUB_USER = 'Hanyuyuan6'
TITLE = 'Public profiles out of sync with profile.json'


def get(url: str, headers: dict[str, str] | None = None, *, raw: bool = False):
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'hanyuyuan6-site-watch', **(headers or {})})
    for attempt in range(3):  # public APIs occasionally drop a connection mid-body
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read().decode('utf-8')
            return data if raw else json.loads(data)
        except (http.client.IncompleteRead, urllib.error.URLError, json.JSONDecodeError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def norm(text: str | None) -> str:
    return re.sub(r'\s+', ' ', (text or '').replace('’', "'")).strip()


def check_orcid(profile: dict, drift: list[str]) -> None:
    copy, person = profile['platform_copy'], profile['person']
    record = get(f'https://pub.orcid.org/v3.0/{ORCID}/record')
    info = record['person']
    bio = (info.get('biography') or {}).get('content')
    if norm(bio) != norm(copy['orcid_bio']):
        drift.append('ORCID biography differs from `platform_copy.orcid_bio`.')
    keywords = [k['content'] for k in info['keywords']['keyword']]
    if keywords != copy['keywords']:
        drift.append(f'ORCID keywords are {keywords}; expected {copy["keywords"]}.')
    urls = {u['url']['value'].rstrip('/') for u in info['researcher-urls']['researcher-url']}
    wanted = {person['homepage'].rstrip('/')} | {p['url'].split('&')[0].rstrip('/') for p in profile['profiles'] if p['name'] in ('Google Scholar', 'GitHub', 'LinkedIn')}
    missing_urls = sorted(u for u in wanted if not any(v.startswith(u) for v in urls))
    if missing_urls:
        drift.append(f'ORCID links missing: {missing_urls}.')
    emails = {e['email'] for e in info['emails']['email']}
    for address in (person.get('email'), person.get('institutional_email')):
        if address and address not in emails:
            drift.append(f'ORCID does not show {address} publicly.')
    others = {n['content'] for n in info['other-names']['other-name']}
    if person.get('name_zh') and person['name_zh'] not in others:
        drift.append(f'ORCID other names lack {person["name_zh"]}.')
    works = {ext['external-id-value'].lower() for group in record['activities-summary']['works']['group']
             for ext in group.get('external-ids', {}).get('external-id', []) if ext['external-id-type'] == 'doi'}
    known = {p['doi'].lower() for p in profile['publications']}
    if works - known:
        drift.append(f'ORCID lists DOIs the site lacks: {sorted(works - known)}.')
    if known - works:
        drift.append(f'The site lists DOIs ORCID lacks: {sorted(known - works)}.')
    # Pages name journals and years, not a count: another review needs no edit, a new
    # journal or a new year does. Journals are matched by the ISSN ORCID records.
    reviewed, years = set(), set()
    for group in record['activities-summary']['peer-reviews'].get('group', []):
        for review in (s for pg in group.get('peer-review-group', []) for s in pg.get('peer-review-summary', [])):
            reviewed.add(review.get('review-group-id', '').removeprefix('issn:'))
            year = ((review.get('completion-date') or {}).get('year') or {}).get('value')
            years |= {int(year)} if year else set()
    listed = {issn for row in profile.get('service', []) for issn in row.get('issns', [])}
    if reviewed - listed:
        drift.append(f'ORCID shows reviews for journals the site does not list (ISSN {sorted(reviewed - listed)}): add them to `service`.')
    if listed - reviewed:
        drift.append(f'The site lists journals with no verified ORCID review (ISSN {sorted(listed - reviewed)}).')
    span = f'{min(years)}–{max(years)}' if len(years) > 1 else str(min(years)) if years else ''
    stated = [row.get('years') for row in profile.get('service', [])]
    if span and span not in stated:
        drift.append(f'ORCID reviews span {span}; `service.years` says {stated}.')
    summaries = record['activities-summary']
    def organisations(kind: str) -> set[str]:
        return {s['organization']['name'] for group in summaries[kind].get('affiliation-group', [])
                for item in group['summaries'] for s in item.values()}
    # The visit is part of the PhD, so it is listed under Education.
    educations, invited = organisations('educations'), organisations('invited-positions')
    for name in [row['institution'] for row in profile['education']] + [row['institution'] for row in profile['appointments']]:
        if name not in educations:
            drift.append(f'ORCID Education lacks {name}.')
    for name in sorted(invited & {row['institution'] for row in profile['appointments']}):
        drift.append(f'ORCID lists {name} as an invited position; it belongs under Education.')


def check_github(profile: dict, drift: list[str]) -> None:
    copy, person = profile['platform_copy'], profile['person']
    user = get(f'https://api.github.com/users/{GITHUB_USER}')
    for field, expected in (('bio', copy['github_bio']), ('blog', person['homepage']), ('company', copy['github_company']), ('location', person['location'])):
        if norm(user.get(field)) != norm(expected):
            drift.append(f'GitHub `{field}` is "{user.get(field)}"; expected "{expected}".')
    readme = get(f'https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_USER}/HEAD/README.md', raw=True)
    for phrase in (profile['narrative']['short_bio'], person['homepage']):
        if norm(phrase) not in norm(readme):
            drift.append(f'GitHub profile README lacks: "{phrase[:80]}".')
    # Each listed repository's About line is the website's code description, unless the
    # entry declares its own `github_about` (a tool that keeps its product line).
    for row in profile['software']:
        match = re.fullmatch(r'https://github\.com/([^/]+)/([^/]+?)/?', row['url'])
        if not match:
            continue
        about = get(f'https://api.github.com/repos/{match[1]}/{match[2]}').get('description')
        expected = row.get('github_about', row['description'])
        if norm(about) != norm(expected):
            drift.append(f'GitHub {match[2]} About is "{about}"; expected "{expected}".')


def main() -> int:
    profile = json.loads((ROOT / 'profile.json').read_text(encoding='utf-8'))
    drift: list[str] = []
    for check in (check_orcid, check_github):
        try:
            check(profile, drift)
        except Exception as error:  # a platform outage must not hide the other checks
            drift.append(f'{check.__name__} could not run: {error}')
    print(json.dumps({'drift': drift}, ensure_ascii=False, indent=2))
    token, repo = os.environ.get('GITHUB_TOKEN'), os.environ.get('GITHUB_REPOSITORY')
    if not drift or not token or not repo:
        return 0
    body = ('These public fields no longer match `profile.json`:\n\n' + '\n'.join(f'- {item}' for item in drift)
            + '\n\nFor platforms without a public API, run `python sync_platforms.py` locally.'
            + '\n\nFix the platform (or update `profile.json` if the platform is right), then close this issue.')
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
