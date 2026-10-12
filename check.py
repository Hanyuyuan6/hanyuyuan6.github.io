#!/usr/bin/env python3
"""Fail on broken local links, stale CVs, invalid metadata, or unlisted public files."""
from pathlib import Path
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
import argparse
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parent
SITE_URL = 'https://hanyuyuan6.github.io/'
# Bytes a desktop retina visitor downloads for images on first view (640w previews
# plus the 320w portrait). The full-size originals load only on click.
IMAGE_BUDGET = 450_000
DASH_RANGE = re.compile(r'\b\d{4} - (?:\d{4}|present)\b')
# Positioning phrases retired on 2026-10-12 ("physics-aware learning" names no learner);
# they must not return in any public file, PDF text or PDF metadata.
RETIRED_TERMS = ('physics-aware',)
TEXT_SUFFIXES = {'.html', '.txt', '.xml', '.json', '.webmanifest', '.bib', '.svg', '.md'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contrast(foreground, background):
    """WCAG 2 contrast ratio of two #rrggbb colours."""
    def luminance(colour):
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def ico_sizes(data):
    if data[:4] != b'\x00\x00\x01\x00':
        raise ValueError('Not an ICO file')
    count = int.from_bytes(data[4:6], 'little')
    return sorted((data[6 + 16 * i] or 256) for i in range(count))


def png_dimensions(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not a PNG file')
    return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')


def webp_dimensions(data):
    """Read WebP canvas/frame dimensions without an imaging dependency."""
    if len(data) < 20 or data[:4] != b'RIFF' or data[8:12] != b'WEBP':
        raise ValueError('Not a WebP RIFF container')
    if int.from_bytes(data[4:8], 'little') + 8 != len(data):
        raise ValueError('WebP container length does not match the file')
    dimensions = []
    has_frame = False
    offset = 12
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError('Truncated WebP chunk header')
        kind = data[offset:offset + 4]
        size = int.from_bytes(data[offset + 4:offset + 8], 'little')
        end = offset + 8 + size
        if end + (size % 2) > len(data):
            raise ValueError('Truncated WebP chunk')
        payload = data[offset + 8:end]
        if kind == b'VP8X':
            if len(payload) != 10 or payload[0] & 2:
                raise ValueError('Expected a static WebP canvas')
            dimensions.append((1 + int.from_bytes(payload[4:7], 'little'), 1 + int.from_bytes(payload[7:10], 'little')))
        elif kind == b'VP8L':
            if len(payload) < 5 or payload[0] != 0x2f:
                raise ValueError('Invalid lossless WebP frame header')
            bits = int.from_bytes(payload[1:5], 'little')
            dimensions.append(((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1))
            has_frame = True
        elif kind == b'VP8 ':
            if len(payload) < 10 or payload[3:6] != b'\x9d\x01\x2a':
                raise ValueError('Invalid lossy WebP frame header')
            dimensions.append((int.from_bytes(payload[6:8], 'little') & 0x3fff, int.from_bytes(payload[8:10], 'little') & 0x3fff))
            has_frame = True
        offset = end + (size % 2)
    if not has_frame or not dimensions or any(w <= 0 or h <= 0 for w, h in dimensions):
        raise ValueError('WebP image dimensions are missing or invalid')
    if any(pair != dimensions[0] for pair in dimensions):
        raise ValueError('WebP canvas and frame dimensions differ')
    return dimensions[0]


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.links = []
        self.tags = []
        self.images = []
        self.errors = []
        self._anchors = []
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        a = dict(attributes)
        self.tags.append((tag, a))
        if 'id' in a:
            self.ids.append(a['id'])
        for name in ('href', 'src'):
            if name in a:
                self.links.append(a[name])
        if tag == 'img':
            self.images.append((a, self._anchors[-1] if self._anchors else None))
            if not a.get('alt', '').strip():
                self.errors.append('Image has no meaningful alt text')
        if tag == 'a' and a.get('target') == '_blank' and 'noopener' not in a.get('rel', ''):
            self.errors.append('New-window link missing noopener')
        if tag == 'a':
            self._anchors.append(a)

    def handle_endtag(self, tag):
        if tag == 'a' and self._anchors:
            self._anchors.pop()


def validate(root):
    failures = []
    checks = []

    def require(condition, message):
        checks.append(message)
        if not condition:
            failures.append(message)

    try:
        profile = json.loads((root / 'profile.json').read_text(encoding='utf-8'))
        manifest = json.loads((root / 'public-files.json').read_text(encoding='utf-8'))
    except (ValueError, OSError) as error:
        return [str(error)], []
    publications = profile['publications']
    ids = [x['id'] for x in publications]
    require(len(ids) == len(set(ids)), 'Unique publication IDs')
    require(len({x['doi'].lower() for x in publications}) == len(publications), 'Unique publication DOIs')
    for p in publications:
        require(p['status'] in ('Published', 'Preprint'), f"Known publication status: {p['id']}")
        require('Yuyuan Han' in p['authors'], f"Author identity present: {p['id']}")
        require(str(p['year']).isdigit() and 2000 <= int(p['year']) <= 2100, f"Valid publication year: {p['id']}")
        require(p['url'].startswith('https://') and p['title'].strip(), f"Publication title and HTTPS link: {p['id']}")
        if 'arxiv.org' in p['url']:
            require(p['status'] == 'Preprint', f"arXiv version explicitly a preprint: {p['id']}")
    for project in profile['projects']:
        require(project['publication_id'] is None or project['publication_id'] in ids, f"Project publication reference: {project['id']}")
    start = profile['education'][0]['start']
    require(start is None or bool(re.fullmatch(r'\d{4}(?:-(?:0[1-9]|1[0-2]))?', start)), 'PhD start is omitted or a confirmed year with an optional valid month')
    require(len(profile['profiles']) == 7 and len({x['url'] for x in profile['profiles']}) == 7, 'Seven distinct academic profile links')
    require(len(profile['platform_copy']['github_bio']) <= 160, 'GitHub bio within 160 characters')
    require(len(profile['platform_copy']['researchgate_bio']) <= 500, 'ResearchGate bio within observed 500-character limit')
    # Platform texts are compositions of the narrative, so a hand edit to one of them is drift.
    copy, narrative, person = profile['platform_copy'], profile['narrative'], profile['person']
    identity = f"I am a {person['position']} at {person['institution']} and a {person['visiting_role'][:1].lower() + person['visiting_role'][1:]} at {person['visiting_institution']}."
    require(narrative['bio'].startswith(narrative['short_bio']), 'Biography opens with the short bio')
    require(copy['researchgate_bio'] == f"{narrative['short_bio']} {identity}", 'ResearchGate bio is the short bio plus the identity sentence')
    require(copy['long_bio'] == copy['orcid_bio'] == f"{identity} {narrative['bio']}", 'ORCID biography is the identity sentence plus the biography')
    require(copy['linkedin_about'] == f"{copy['long_bio']}\n\n{narrative['welcome']}", 'LinkedIn About is the long bio plus the welcome line')
    require(copy['github_bio'].startswith(narrative['hero']) and copy['linkedin_headline'].endswith(narrative['hero'].rstrip('.')), 'GitHub bio and LinkedIn headline carry the hero line')
    visit, visit_zh = profile['appointments'][0], json.loads((root / 'cv-variants.json').read_text(encoding='utf-8'))['zh']['appointment']
    require(visit['department'] in copy['linkedin_experience_polimi'] and visit['host'] in copy['linkedin_experience_polimi'], 'LinkedIn experience names the department and supervisor of record')
    require(visit_zh['department'] in copy['linkedin_experience_polimi_zh'] and visit['host'] in copy['linkedin_experience_polimi_zh'] and visit_zh['institution'] + visit_zh['role'] in copy['linkedin_headline_zh'], 'Chinese LinkedIn copy uses the Chinese appointment of record')

    expected = set(manifest['repository_files'])
    actual = set()
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if any(part in ('.git', '__pycache__', '.qa', '.venv') for part in relative.parts) or path.name == '.DS_Store':
            continue
        if path.is_file() or path.is_symlink():
            actual.add(relative.as_posix())
            require(not path.is_symlink(), f"No public symlink: {relative}")
    require(actual == expected, 'Public repository file list matches reviewed allowlist; missing=' + str(sorted(expected - actual)) + '; unexpected=' + str(sorted(actual - expected)))
    site_files = {x[5:] for x in actual if x.startswith('dist/')}
    require(site_files == set(manifest['site_files']), 'Only reviewed site files will be deployed')
    require(not any('ZH' in x or '简历' in x or 'account-changes' in x for x in actual), 'Chinese application output and account records stay outside public repository')
    all_documents = {}
    for path in sorted((root / 'dist').rglob('*.html')):
        content = path.read_text(encoding='utf-8')
        relative = path.relative_to(root)
        require('\ufffd' not in content and not content.startswith('\ufeff'), f"Clean UTF-8: {relative}")
        require(not re.search(r'/Users/|file://|localhost|127\.0\.0\.1|API[_-]?KEY|sk-ant-', content), f"No local paths or credential strings: {relative}")
        require(not re.search(r'\b(?:TODO|PLACEHOLDER|Lorem ipsum)\b', content, flags=re.I), f"No unfinished template text: {relative}")
        document = Document(content)
        all_documents[path.resolve()] = document
        require(len(document.ids) == len(set(document.ids)), f"Unique HTML anchors: {relative}")
        for error in document.errors:
            require(False, f'{relative}: {error}')
    for path, document in all_documents.items():
        for link in document.links:
            parsed = urlsplit(link)
            if parsed.scheme in ('https', 'http', 'mailto', 'data'):
                continue
            require(not parsed.scheme and bool(link), f'Valid local link scheme in {path.name}: {link}')
            target = (root / 'dist' / unquote(parsed.path).lstrip('/')) if parsed.path.startswith('/') else (path.parent / unquote(parsed.path)) if parsed.path else path
            target = target.resolve()
            if target.is_dir():
                target /= 'index.html'
            require(target.is_relative_to((root / 'dist').resolve()) and target.is_file(), f'Local target exists in public site: {path.name} -> {link}')
            if parsed.fragment and target in all_documents:
                require(unquote(parsed.fragment) in all_documents[target].ids, f'Anchor target exists: {path.name} -> {link}')
    home_path = (root / 'dist' / 'index.html').resolve()
    if home_path in all_documents:
        home = all_documents[home_path]
        require(sum(t == 'h1' for t, _ in home.tags) == 1, 'Homepage has one H1')
        require(any(t == 'html' and a.get('lang') == 'en' for t, a in home.tags), 'Homepage language is English')
        require(any(t == 'meta' and a.get('name') == 'viewport' for t, a in home.tags), 'Responsive viewport metadata')
        home_text = home_path.read_text(encoding='utf-8')
        require(escape(profile['narrative']['bio'], quote=True) in home_text, 'Homepage contains approved research biography')
        description = profile['narrative'].get('meta_description', '')
        require(50 <= len(description) <= 160 and f'name="description" content="{escape(description, quote=True)}"' in home_text, 'Search description is present and fits a result snippet')
        metas = {a.get('property') or a.get('name'): a.get('content', '') for t, a in home.tags if t == 'meta'}
        page_title = f"{profile['person']['name']} · {profile['narrative']['field']}"
        require(f'<title>{escape(page_title, quote=True)}</title>' in home_text and metas.get('og:title') == page_title, 'Page and link-preview titles state the current field')
        require(metas.get('og:image') == SITE_URL + 'social-card.png' and metas.get('twitter:card') == 'summary_large_image', 'Link previews use the social card')
        card = root / 'dist' / 'social-card.png'
        require(card.is_file() and png_dimensions(card.read_bytes()) == (1200, 630), 'Social card is a 1200×630 PNG')
        icons = {a.get('href') for t, a in home.tags if t == 'link' and 'icon' in (a.get('rel') or '')}
        require({'favicon.svg', 'favicon.ico', 'apple-touch-icon.png'} <= {icon.split('?')[0] for icon in icons} and all((root / 'dist' / name).is_file() for name in ('favicon.svg', 'favicon.ico', 'apple-touch-icon.png')), 'Site icons are declared and deployed')
        require(all('?v=' in icon for icon in icons), 'Icon URLs carry a version, so browsers drop a cached old icon')
        # Every public page names the current mark; a page without its own icon falls back to a
        # cached /favicon.ico, which is how an old mark lingers in a tab.
        version = sha(root / 'dist' / 'favicon.svg')[:10] if (root / 'dist' / 'favicon.svg').is_file() else None
        for page in [root / 'dist' / 'index.html', root / 'dist' / '404.html', *sorted((root / 'dist' / 'cv').glob('*.html'))]:
            hrefs = re.findall(r'<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"', page.read_text(encoding='utf-8')) if page.is_file() else []
            require(version is not None and all(any(h.endswith(f'{name}?v={version}') for h in hrefs) for name in ('favicon.svg', 'favicon.ico', 'apple-touch-icon.png')), f'Page declares the current icon set: {page.relative_to(root / "dist")}')
        theme = json.loads((root / 'theme.json').read_text(encoding='utf-8'))
        palette = theme['palettes'][theme['selected']]
        favicon = (root / 'dist' / 'favicon.svg').read_text(encoding='utf-8') if (root / 'dist' / 'favicon.svg').is_file() else ''
        mark = theme['mark']
        require(all(f'stop-color="{mark[k]}"' in favicon for k in ('tile', 'tile_to', 'light', 'light_to', 'signal', 'signal_to')) and f'fill="{mark["pixel"]}"' in favicon and 'url(#m)' in favicon and '<text' not in favicon, 'Favicon is the outlined Y-and-pixel mark in the theme.json mark colours')
        require(all(contrast(mark[k], mark['tile_to']) >= 4 for k in ('light', 'light_to', 'signal', 'signal_to', 'pixel')), 'Every mark colour stands out from the tile at 16 px (≥ 4:1)')
        require((root / 'dist' / 'favicon.ico').is_file() and ico_sizes((root / 'dist' / 'favicon.ico').read_bytes()) == [16, 32, 48], 'favicon.ico carries 16, 32 and 48 px images')
        require((root / 'dist' / 'apple-touch-icon.png').is_file() and png_dimensions((root / 'dist' / 'apple-touch-icon.png').read_bytes()) == (180, 180), 'Apple touch icon is 180×180')
        for name, colours in theme['palettes'].items():
            require(all(contrast(colours[key], colours['bg']) >= 4.5 for key in ('ink', 'muted', 'link', 'hover')) and all(contrast(colours[key], colours['tint']) >= 4.5 for key in ('ink', 'muted', 'link')), f'Palette {name} passes WCAG AA for text on its background and highlight')
        stylesheet = (root / 'dist' / 'style.css').read_text(encoding='utf-8')
        require(all(f'--{key}: {value};' in stylesheet.split('@media', 1)[0] for key, value in palette.items() if key != 'label'), 'Deployed CSS carries the selected theme.json palette')
        require(not theme.get('dark') or all(f'--{key}: {value};' in stylesheet for key, value in theme['palettes'][theme['dark']].items() if key != 'label'), 'Deployed CSS carries the automatic dark palette')
        require(set(re.findall(r'#[0-9a-fA-F]{3,6}\b', (root / 'style.css').read_text(encoding='utf-8'))) <= {'#fff', '#000'}, 'style.css takes its colours from theme.json only')
        require(f'style.css?v={hashlib.sha256(stylesheet.encode("utf-8")).hexdigest()[:10]}' in home_text, 'Stylesheet URL changes whenever the CSS changes')
        robots = (root / 'dist' / 'robots.txt').read_text(encoding='utf-8') if (root / 'dist' / 'robots.txt').is_file() else ''
        sitemap = (root / 'dist' / 'sitemap.xml').read_text(encoding='utf-8') if (root / 'dist' / 'sitemap.xml').is_file() else ''
        require(f'Sitemap: {SITE_URL}sitemap.xml' in robots, 'robots.txt points to the sitemap')
        require(f'<loc>{SITE_URL}</loc>' in sitemap and all(f'<loc>{SITE_URL}{cv["path"]}</loc>' in sitemap for cv in profile['cvs']), 'Sitemap lists the homepage and public CVs')
        require((root / 'dist' / '404.html').is_file(), 'Custom 404 page exists')
        require(not DASH_RANGE.search(home_text), 'Date ranges use en dashes on the homepage')
        news = profile.get('news', [])
        require(1 <= len(news) <= 5 and [n['date'] for n in news] == sorted((n['date'] for n in news), reverse=True), 'News is short and newest first')
        featured = {p['publication_id'] for p in profile['projects'] if p['id'] in profile['selected_project_ids']}
        require(home_text.count('<details class="bibtex">') == len(publications) + len(featured) and all(p['doi'] in home_text for p in publications), 'Every publication offers BibTeX with its DOI')
        require(home_text.count('class="publication publication-entry') == len(publications) and home_text.count('publication-entry is-selected') == len(featured), 'Complete publication list keeps the featured works, highlighted')
        bib = (root / 'dist' / 'publications.bib').read_text(encoding='utf-8') if (root / 'dist' / 'publications.bib').is_file() else ''
        require(len(re.findall(r'^@\w+\{', bib, flags=re.M)) == len(publications) and all(p['doi'] in bib for p in publications) and 'href="publications.bib"' in home_text, 'Complete BibTeX file is linked and lists every paper')
        order = [home_text.find(f'id="{anchor}"') for anchor in ('news', 'research', 'directions', 'publications', 'software', 'background', 'contact')]
        require(-1 not in order and order == sorted(order), 'Sections run News, Research, Directions, Publications, Code, Background, Contact')
        require('Host:' not in home_text and all(f"Supervisor: {escape(row['host'], quote=True)}" in home_text for row in profile['appointments'] if row.get('host')), 'Visiting appointment names its supervisor')
        require('overleaf.com/project/' not in home_text, 'Private Overleaf editor is not a public CV link')
        image_metadata = json.loads((root / 'research-figures.json').read_text(encoding='utf-8'))
        figures = image_metadata['figures']
        portrait = image_metadata.get('portrait')
        selected = set(profile['selected_project_ids'])
        expected_figure_ids = {p['publication_id'] for p in profile['projects'] if p['id'] in selected}
        require(selected == {'lift', 'shsi', 'loop'}, 'All three selected research projects are present')
        require('electro-optic' in home_text.lower(), 'Homepage retains electro-optic materials/device experience')
        require('Research code</h2>' in home_text and 'Research software</h2>' not in home_text, 'Homepage code repositories use the current professional heading')
        require('PhD (in progress)' not in home_text and 'PhD student' in home_text, 'Homepage uses current student identity')
        for project in profile['projects']:
            if project['id'] in selected:
                require(escape(project['summary'], quote=True) in home_text, f"Selected-work conclusion appears in homepage: {project['id']}")
        require(all(term in home_text for term in ('20 dB', 'clean-trained', 'reconstruct-then-segment')), 'Lift homepage states its noise level, training condition and baseline')
        for project in profile['projects']:
            if project['id'] in selected:
                chips = project.get('highlights', [])
                require(1 <= len(chips) <= 4 and all(len(chip) <= 72 and escape(chip, quote=False) in home_text for chip in chips), f"Selected work shows 1-4 short key-result chips: {project['id']}")
                require(len(project['summary'].split()) <= 22, f"Selected-work takeaway is one short sentence (22 words max): {project['id']}")
        require('one-dimensional optical phased arrays' in home_text, 'LDGI homepage identifies its OPA illumination setting')
        require(all(f"{row['count']} verified reviews" in home_text and all(escape(v, quote=False) in home_text for v in row['venues']) for row in profile.get('service', [])), 'Homepage lists verified peer review with its venues and count')
        require('Ten deep' not in home_text and 'Eight deep architectures' in home_text, 'Comparison repository is described as in the paper (eight networks plus a classical inverse)')
        require(escape(profile['narrative']['short_bio'], quote=False) in home_text, 'Homepage carries the canonical short biography verbatim')
        require(not profile['narrative'].get('personal') or f'<p class="personal">{escape(profile["narrative"]["personal"], quote=True)}</p>' in home_text, 'Homepage shows the personal line when profile.json has one')
        require(all(f"Advisor: {escape(row['advisor'], quote=True)}" in home_text for row in profile['education'] if row.get('advisor')), 'Homepage names the recorded advisor')
        first_authored = [p for p in publications if p['id'] in {pr['publication_id'] for pr in profile['projects'] if pr['id'] in selected}]
        require(home_text.count('<strong class="self-author">Yuyuan Han</strong>') >= len(publications) and all(p['authors'][0] == 'Yuyuan Han' for p in first_authored), 'Author lists are visible with the researcher highlighted')

        require(len(figures) == 3 and len({f['id'] for f in figures}) == 3 and {f['id'] for f in figures} == expected_figure_ids, 'Every selected paper has exactly one reviewed figure')
        require(isinstance(portrait, dict), 'Reviewed portrait metadata is present')
        require(not any(t == 'figcaption' for t, _ in home.tags), 'Homepage adds no visible figure captions')
        require(len(home.images) == 4, 'Homepage renders one portrait and three research figures')
        records = [('figure ' + f['id'], f, False) for f in figures]
        if isinstance(portrait, dict):
            records.insert(0, ('portrait', portrait, True))
            require(portrait.get('selected_id') == 'P2', 'Portrait matches the selected P2 image')
            require(portrait.get('alt') == profile['person']['name'], 'Portrait alt identifies the researcher')
        require(len({record.get('path') for _, record, _ in records}) == len(records), 'Portrait and research figures use distinct assets')
        first_view_bytes = 0
        for label, record, is_portrait in records:
            relative = record.get('path', '')
            valid_path = relative == 'assets/portrait.webp' if is_portrait else bool(re.fullmatch(r'assets/research/[^/\\]+\.webp', relative))
            require(valid_path and '..' not in Path(relative).parts, f'Reviewed local image path: {label}')
            if not valid_path or '..' in Path(relative).parts:
                continue
            source = root / relative
            deployed = root / 'dist' / relative
            require(source.is_file() and not source.is_symlink() and sha(source) == record.get('sha256'), f'Reviewed image source hash: {label}')
            require(deployed.is_file() and not deployed.is_symlink() and sha(deployed) == record.get('sha256'), f'Deployed image matches source: {label}')
            valid_dimensions = all(type(record.get(key)) is int and record[key] > 0 for key in ('width', 'height'))
            require(valid_dimensions, f'Positive intrinsic image dimensions: {label}')
            if source.is_file():
                try:
                    dimensions = webp_dimensions(source.read_bytes())
                    require(dimensions == (record.get('width'), record.get('height')), f'WebP dimensions match reviewed metadata: {label}')
                except ValueError as error:
                    require(False, f'Invalid WebP image: {label}: {error}')
            # The page shows a responsive preview; the reviewed original opens on click.
            matched = [(a, parent) for a, parent in home.images if parent is not None and parent.get('href') == relative]
            require(len(matched) == 1 and bool(matched[0][1].get('aria-label', '').strip()), f'Preview opens the labelled full-size original: {label}')
            if len(matched) != 1:
                continue
            image = matched[0][0]
            expected_alt = record.get('alt') if is_portrait else record.get('teaser', {}).get('alt')
            require(bool(expected_alt) and image.get('alt', '').strip() == expected_alt.strip(), f'Preview alt text matches the reviewed record: {label}')
            candidates = [part.strip().rsplit(' ', 1) for part in image.get('srcset', '').split(',') if part.strip()]
            require(len(candidates) >= 2 and image.get('sizes'), f'Preview is responsive (srcset and sizes): {label}')
            crop = record.get('teaser', {}).get('crop') if not is_portrait else [0, 0, record['width'], record['height']]
            for candidate, descriptor in candidates:
                file = root / 'dist' / candidate
                ok = file.is_file() and descriptor.endswith('w')
                if ok:
                    width, height = webp_dimensions(file.read_bytes())
                    ok = width == int(descriptor[:-1]) and abs(width / height - (crop[2] - crop[0]) / (crop[3] - crop[1])) < 0.01 and file.stat().st_size <= 220_000
                require(ok, f'Preview candidate exists, matches its width and aspect, under 220 kB: {candidate}')
            default = root / 'dist' / image.get('src', '')
            if default.is_file():
                width, height = webp_dimensions(default.read_bytes())
                require((image.get('width'), image.get('height')) == (str(width), str(height)), f'HTML declares the preview’s intrinsic size: {label}')
                first_view_bytes += default.stat().st_size
            else:
                require(False, f'Default preview exists: {label}')
        require(first_view_bytes <= IMAGE_BUDGET, f'First-view image payload {first_view_bytes} bytes within {IMAGE_BUDGET}')
        for publication in publications:
            require(publication['doi'] in home_text or publication['url'].replace('&', '&amp;') in home_text, f"Homepage includes publication link: {publication['id']}")
        for cv in profile['cvs']:
            require(cv['path'] in home.links, f"Working CV entry: {cv['label']}")
    else:
        require(False, 'Built homepage exists')

    try:
        cv_manifest = json.loads((root / 'cv-build.json').read_text(encoding='utf-8'))
        fingerprints = {}
        for label, record in cv_manifest['inputs'].items():
            source = root / record['path']
            fingerprints[label] = sha(source)
            require(fingerprints[label] == record['sha256'], f'CV source is current: {label}')
        expected_fingerprint = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
        require(expected_fingerprint == cv_manifest['source_fingerprint'], 'CV source fingerprint matches')
        for output in cv_manifest['outputs']:
            require(output['destination'] == 'public', 'Public CV manifest contains only public outputs')
            for format_name in ('pdf', 'html'):
                record = output[format_name]
                target = root / 'dist' / 'cv' / record['path']
                require(target.is_file() and sha(target) == record['sha256'], f'CV file hash matches reviewed build: {record["path"]}')
        # Visual review is recorded separately; hashes bind it to the exact PDFs.
        review = json.loads((root / 'cv-review.json').read_text(encoding='utf-8'))
        for output in cv_manifest['outputs']:
            record = output['pdf']
            require(review['pdf_sha256'].get(record['path']) == record['sha256'], f'Latest public PDF has a visual review: {record["path"]}')
    except (OSError, ValueError, KeyError) as error:
        require(False, f'CV freshness or visual-review record unavailable: {error}')

    public_text = [path for path in (root / 'dist').rglob('*') if path.is_file() and path.suffix in TEXT_SUFFIXES]
    public_text += [root / 'profile.json', root / 'cv-variants.json']
    stale = sorted(str(path.relative_to(root)) for path in public_text if any(term in path.read_text(encoding='utf-8', errors='ignore').lower() for term in RETIRED_TERMS))
    require(not stale, f'No retired positioning terms in public files: {stale}')

    zh_field = json.loads((root / 'cv-variants.json').read_text(encoding='utf-8'))['zh']['field']
    from pypdf import PdfReader
    for cv in profile['cvs']:
        path = root / 'dist' / cv['path']
        try:
            reader = PdfReader(path)
            content = '\n'.join(page.extract_text() or '' for page in reader.pages)
            require('Yuyuan Han' in content, f'PDF text is searchable: {path.name}')
            require(profile['person']['email'] in content, f'PDF has stable email: {path.name}')
            require('Politecnico di Milano' in content and 'B.Eng.' in content, f'PDF current appointment and correct bachelor degree: {path.name}')
            require('\ufffd' not in content and 'PLACEHOLDER' not in content, f'PDF has no missing-text marker: {path.name}')
            require(len(reader.pages) == (1 if 'Resume' in path.name else 2), f'Expected PDF page count: {path.name}')
            require(all(word in content for word in ['The Lift Spectrum', 'Hadamard', 'LDGI']), f'Three selected works in actual PDF: {path.name}')
            require('electro-optic' in content.lower(), f'Materials/device experience in actual PDF: {path.name}')
            upper = content.upper()
            require(upper.find('EDUCATION & RESEARCH APPOINTMENTS') >= 0 and upper.find('EDUCATION & RESEARCH APPOINTMENTS') < upper.find('SELECTED RESEARCH'), f'Education precedes selected research in actual PDF: {path.name}')
            require('PhD (in progress)' not in content and 'RESEARCH SOFTWARE' not in upper, f'Current professional terminology in actual PDF: {path.name}')
            places = [content.find(name) for name in ("Xi'an Jiaotong University", 'Politecnico di Milano', 'Northwest University')]
            require(-1 not in places and places == sorted(places), f'Visiting appointment follows the PhD it belongs to: {path.name}')
            compact_text = re.sub(r'\s+', ' ', content)
            require('clean-trained' in compact_text and '20 dB' in compact_text, f'Lift claim keeps its noise level and training condition: {path.name}')
            require('one-dimensional optical phased arrays' in compact_text, f'LDGI OPA setting in actual PDF: {path.name}')
            require(not re.search(r'\bPublished\b', content), f'No redundant Published labels: {path.name}')
            require(all(f"Advisor: {row['advisor']}" in compact_text for row in profile['education'] if row.get('advisor')), f'PDF names the recorded advisor: {path.name}')
            require('Host:' not in compact_text and all(f"Supervisor: {row['host']}" in compact_text for row in profile['appointments'] if row.get('host')), f'PDF names the visiting supervisor: {path.name}')
            require(not DASH_RANGE.search(compact_text), f'Date ranges use en dashes: {path.name}')

            require(not any(word in content for word in ['CET-6', 'Programming and modeling', 'TECHNICAL SKILLS']), f'No generic tools/language section: {path.name}')
            links = [str(a.get_object().get('/A', {}).get('/URI', '')) for page in reader.pages for a in page.get('/Annots', [])]
            require(any('mailto:' in link for link in links), f'PDF email link annotation: {path.name}')
            require(SITE_URL in links, f'PDF links back to the homepage: {path.name}')
            info = reader.metadata or {}
            require(info.get('/Title', '').startswith(profile['person']['name']) and info.get('/Author') == profile['person']['name'], f'PDF title and author metadata: {path.name}')
            require(info.get('/Subject') == (zh_field if path.stem.endswith('_ZH') else profile['narrative']['field']) and info.get('/Keywords') == ', '.join(profile['platform_copy']['keywords']), f'PDF subject and keywords come from profile.json: {path.name}')
            require(not any(term in (content + str(info.get('/Subject', ''))).lower() for term in RETIRED_TERMS), f'No retired positioning terms in PDF: {path.name}')
        except Exception as error:
            require(False, f'{path.name}: {error}')
    return failures, checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    failures, checks = validate(args.root.resolve())
    print(json.dumps({'passed': not failures, 'checks': len(checks), 'failures': failures}, ensure_ascii=False, indent=2))
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
