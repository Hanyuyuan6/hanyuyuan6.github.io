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


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        require('trained on clean data' in home_text and 'tested reconstruct-then-segment' in home_text, 'Lift homepage preserves the comparison and training condition')
        require('one-dimensional optical phased arrays' in home_text, 'LDGI homepage identifies its OPA illumination setting')

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
            matched = [(a, parent) for a, parent in home.images if a.get('src') == relative]
            require(len(matched) == 1 and matched[0][0].get('alt', '').strip() == record.get('alt', '').strip() and bool(record.get('alt', '').strip()), f'Image identification and alt text: {label}')
            require(len(matched) == 1 and matched[0][0].get('width') == str(record.get('width')) and matched[0][0].get('height') == str(record.get('height')), f'HTML preserves intrinsic image dimensions: {label}')
            require(len(matched) == 1 and matched[0][1] is not None and matched[0][1].get('href') == relative and bool(matched[0][1].get('aria-label', '').strip()), f'Image has a labelled full-size link: {label}')
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
            require(content.find('EDUCATION & RESEARCH APPOINTMENTS') >= 0 and content.find('EDUCATION & RESEARCH APPOINTMENTS') < content.find('SELECTED RESEARCH'), f'Education precedes selected research in actual PDF: {path.name}')
            require('PhD (in progress)' not in content and 'RESEARCH SOFTWARE' not in content, f'Current professional terminology in actual PDF: {path.name}')
            compact_text = re.sub(r'\s+', ' ', content)
            require('trained on clean data' in compact_text and 'measurement noise' in compact_text, f'Lift conclusion retains its noise and training scope: {path.name}')
            require('one-dimensional optical phased arrays' in compact_text, f'LDGI OPA setting in actual PDF: {path.name}')

            require(not any(word in content for word in ['CET-6', 'Programming and modeling', 'TECHNICAL SKILLS']), f'No generic tools/language section: {path.name}')
            links = [str(a.get_object().get('/A', {}).get('/URI', '')) for page in reader.pages for a in page.get('/Annots', [])]
            require(any('mailto:' in link for link in links), f'PDF email link annotation: {path.name}')
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
