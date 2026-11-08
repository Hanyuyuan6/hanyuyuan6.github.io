#!/usr/bin/env python3
"""List what each platform still needs after profile.json changed, with paste-ready text.

The website and CVs are generated from profile.json, but the profiles on LinkedIn, ORCID,
ResearchGate, Google Scholar, OpenReview, GitHub and Web of Science are edited by hand.
This script compares profile.json with platforms-synced.json (the values last read back
on the platforms) and prints, platform by platform, every field that changed: the full
new text, the text it replaces and how to read it back.

    python sync_platforms.py           # what is still to do
    python sync_platforms.py --done    # after reading every platform back: record the values
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RECORD = ROOT / 'platforms-synced.json'
READBACK = {
    'LinkedIn': 'reopen each editor (English and Chinese tabs separately) and compare the text verbatim; read the Featured cards',
    'ORCID': 'python watch_profiles.py (public API; also runs every Monday)',
    'ResearchGate': 'open the profile page and compare About, current activity, skills, affiliations and education',
    'Google Scholar': 'open the profile and compare affiliation, interests and homepage',
    'OpenReview': 'open the profile page and compare expertise, emails and history',
    'GitHub': 'python watch_profiles.py (public API; also runs every Monday)',
    'Web of Science': 'open the author record and compare its works',
}


def fields(profile: dict[str, Any]) -> dict[str, dict[str, str]]:
    """What each platform shows, as text to paste. Keys are the platform's own labels."""
    person, copy = profile['person'], profile['platform_copy']
    places = '\n'.join([f"{r['institution']} · {r['degree']} · {r['field']} · {r['display_dates']}" for r in profile['education']]
                       + [f"{r['institution']} · {r['role']} · {r['department']} · Supervisor: {r['host']} · {r['display_dates']}" for r in profile['appointments']])
    papers = '\n'.join(f"{p['title']} · {p['venue']} ({p['year']}) · doi:{p['doi']}" for p in profile['publications'])
    return {
        'LinkedIn': {
            'Headline': copy['linkedin_headline'],
            'Headline (Chinese profile)': copy['linkedin_headline_zh'],
            'About': copy['linkedin_about'],
            'About (Chinese profile)': copy['linkedin_about_zh'],
            'Featured 1 (title, link, description)': f"{copy['linkedin_featured_site_title']}\n{person['homepage']}\n{copy['linkedin_featured_site']}",
            'Featured 2 (title, link, description)': f"{copy.get('linkedin_featured_lift_title', '')}\n{copy.get('linkedin_featured_lift_url', '')}\n{copy['linkedin_featured_lift']}",
            'Featured 3 (title, link, description)': f"{copy.get('linkedin_featured_resume_title', '')}\n{copy.get('linkedin_featured_resume_url', '')}\n{copy['linkedin_featured_resume']}",
            'Top skills': ', '.join(copy['keywords']),
            'Education (the visit included)': places,
        },
        'ORCID': {'Biography': copy['orcid_bio'], 'Keywords': ', '.join(copy['keywords']), 'Education (the visit included)': places, 'Works': papers},
        'ResearchGate': {'About': copy['researchgate_bio'], 'Current activity': copy['researchgate_activity'], 'Skills': ', '.join(copy['keywords']),
                         'Affiliations and education (the visit included)': places, 'Research items': papers},
        'Google Scholar': {'Affiliation': f"{person['position']}, {person['institution']}", 'Areas of interest (no space after commas)': ','.join(copy['keywords']),
                           'Homepage': person['homepage'], 'Articles': papers},
        'OpenReview': {'Expertise (start year)': '\n'.join(f'{e} ({y})' for e, y in zip(copy['openreview_expertise'], copy['openreview_expertise_start_years'])),
                       'Emails include': person['institutional_email'], 'Career & education': places, 'Publications': papers},
        'GitHub': {'Bio': copy['github_bio'], 'Company': copy['github_company'], 'Location': person['location'], 'Website': person['homepage'],
                   'Profile README lead': profile['narrative']['short_bio'],
                   'Repository About lines': '\n'.join(f"{s['name']}: {s.get('github_about', s['description'])}" for s in profile['software'])},
        'Web of Science': {'Works': papers},
    }


def quote(text: str) -> str:
    return '\n'.join('    ' + line for line in (text or '(empty)').splitlines())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile', type=Path, default=ROOT / 'profile.json')
    parser.add_argument('--done', action='store_true', help='record the current values as read back on every platform')
    args = parser.parse_args()
    now = fields(json.loads(args.profile.read_text(encoding='utf-8')))
    if args.done:
        RECORD.write_text(json.dumps({'read_back_on': date.today().isoformat(), 'platforms': now}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
        print(f'Recorded {sum(map(len, now.values()))} fields on {len(now)} platforms in {RECORD.name}.')
        return 0
    record = json.loads(RECORD.read_text(encoding='utf-8'))
    then = record['platforms']
    todo = {platform: [(label, then.get(platform, {}).get(label, ''), text) for label, text in values.items() if then.get(platform, {}).get(label) != text]
            for platform, values in now.items()}
    todo = {platform: rows for platform, rows in todo.items() if rows}
    if not todo:
        print(f'No tracked field changed since the read-back recorded on {record["read_back_on"]}.')
        return 0
    print(f'{sum(map(len, todo.values()))} field(s) on {len(todo)} platform(s) changed since the read-back of {record["read_back_on"]}.\n')
    for platform, rows in todo.items():
        print(f'## {platform}\nRead back: {READBACK[platform]}\n')
        for label, old, new in rows:
            print(f'### {label}\nNew text:\n{quote(new)}\nReplaces:\n{quote(old)}\n')
    print('After every platform shows the new text, run: python sync_platforms.py --done')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
