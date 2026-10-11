#!/usr/bin/env python3
"""Build four CV variants from one profile, content model and PDF template.

Usage:
  build_cv.py --profile profile.json --out dist/cv --private-out ../简历

No output directory is cleared. HTML and PDF use the same ordered semantic
blocks; the public outputs contain only the two English variants.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
import pdfplumber
from pypdf import PdfReader


HERE = Path(__file__).resolve().parent
RUNTIME = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies'
RUNTIME_FONTS = RUNTIME / 'native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/Resources/fonts/truetype'
PAGE_W, PAGE_H = A4
LEFT = 44.0
RIGHT = 44.0
WIDTH = PAGE_W - LEFT - RIGHT
BOTTOM = 48.0
FONT_SOURCES: dict[str, dict[str, Any]] = {}
LINK_COLOR = '#205b87'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_text(value: Any) -> str:
    # Standard hyphens keep the PDFs friendly to basic parsers and copy/paste.
    return str(value).replace('\u2011', '-').replace('\u2013', '-').replace('\u2014', '-').replace('\u00a0', ' ')


def esc(value: Any) -> str:
    return html.escape(clean_text(value), quote=True)


def link(label: str, url: str) -> str:
    if not url.startswith(('https://', 'mailto:')):
        raise ValueError(f'Unsupported CV URL: {url!r}')
    return f'<a href="{esc(url)}" color="{LINK_COLOR}">{esc(label)}</a>'


def strip_markup(value: str) -> str:
    return html.unescape(re.sub(r'<[^>]+>', '', value))


def chinese_period(value: str) -> str:
    """Translate date words only; all date values come from the profile."""
    if value == 'Current':
        return '当前'
    match = re.fullmatch(r'Graduate research: (\d{4}) - present', value)
    if match:
        return f'研究生科研：{match[1]} 年至今'
    match = re.fullmatch(r'([A-Za-z]{3}) (\d{4}) - present', value)
    if match:
        months = {m: str(i) for i, m in enumerate(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}
        if match[1] not in months:
            raise ValueError(f'Unsupported appointment month: {value}')
        return f'{match[2]} 年 {months[match[1]]} 月至今'
    match = re.fullmatch(r'(\d{4}) - present', value)
    if match:
        return f'{match[1]} 年至今'
    if re.fullmatch(r'\d{4}( - \d{4})?', value):
        return value
    raise ValueError(f'Untranslated profile date: {value}')


def validate_translations(profile: dict[str, Any], config: dict[str, Any]) -> None:
    for project in profile['projects']:
        if project['id'] in config['zh']['projects']:
            source = {k: project[k] for k in ['title', 'status', 'role', 'bullets']}
            actual = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if actual != config['translation_source_hashes'].get(project['id']):
                raise ValueError(f'Chinese translation requires review after English project changed: {project["id"]}')


def load_fonts() -> None:
    fonts = {
        'CVSans': (RUNTIME_FONTS / 'NotoSans-Regular.ttf', 0),
        'CVSansBold': (RUNTIME_FONTS / 'NotoSans-Bold.ttf', 0),
        'CVSansItalic': (RUNTIME_FONTS / 'NotoSans-Italic.ttf', 0),
        'CVSerif': (RUNTIME_FONTS / 'NotoSerif-Regular.ttf', 0),
        'CVSerifBold': (RUNTIME_FONTS / 'NotoSerif-Bold.ttf', 0),
        'CVSerifItalic': (RUNTIME_FONTS / 'NotoSerif-Italic.ttf', 0),
        'CVChinese': (Path('/System/Library/Fonts/STHeiti Light.ttc'), 0),
        'CVChineseBold': (Path('/System/Library/Fonts/STHeiti Medium.ttc'), 0),
    }
    for name, (path, index) in fonts.items():
        if not path.is_file():
            raise FileNotFoundError(f'CV font unavailable: {path}')
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=index))
        FONT_SOURCES[name] = {'filename': path.name, 'source': 'bundled runtime' if path.is_relative_to(RUNTIME) else 'system font', 'subfont_index': index, 'sha256': sha256(path)}
    pdfmetrics.registerFontFamily('CVSans', normal='CVSans', bold='CVSansBold', italic='CVSansItalic', boldItalic='CVSansBold')
    pdfmetrics.registerFontFamily('CVSerif', normal='CVSerif', bold='CVSerifBold', italic='CVSerifItalic', boldItalic='CVSerifBold')
    pdfmetrics.registerFontFamily('CVChinese', normal='CVChinese', bold='CVChineseBold', italic='CVChinese', boldItalic='CVChineseBold')


@dataclass
class Block:
    kind: str
    text: str = ''
    source: str = ''
    gap: float = 0
    meta: dict[str, Any] = field(default_factory=dict)


def pub_authors(pub: dict[str, Any], short: bool = False) -> str:
    authors = pub.get('authors', [])
    if isinstance(authors, str):
        text = authors
    else:
        values = []
        for a in authors:
            if isinstance(a, str):
                values.append(a)
            else:
                values.append(a.get('name') or a.get('display') or ' '.join(filter(None, [a.get('given'), a.get('family')])) )
        if short and len(values) > 4:
            values = values[:3] + ['et al.']
        text = ', '.join(values)
    escaped = esc(text)
    # Full author lists remain copyable; the candidate's name is emphasized.
    for name in ['Yuyuan Han', 'Han Yuyuan', 'Han, Yuyuan', 'Y. Han']:
        escaped = escaped.replace(esc(name), f'<b>{esc(name)}</b>')
    return escaped


def pub_reference(pub: dict[str, Any], short: bool = False) -> str:
    title = str(pub.get('title', '')).strip()
    if not title:
        raise ValueError(f'Publication has no title: {pub.get("id")}')
    authors = pub_authors(pub, short=short)
    venue = pub.get('venue') or pub.get('journal') or ''
    year = pub.get('year')
    if not authors or not year or not venue:
        raise ValueError(f'Publication lacks authors, venue or year: {pub.get("id")}')
    target = publication_url(pub)
    pieces = [authors + '.', f'<b>{link(title, target)}</b>.']
    details = f'<i>{esc(venue)}</i>'
    volume = pub.get('volume')
    issue = pub.get('issue')
    pages = pub.get('pages') or pub.get('article_number') or pub.get('article')
    if volume:
        details += f' {esc(volume)}'
    if issue:
        details += f'({esc(issue)})'
    if pages:
        details += f', {esc(pages)}'
    details += f' ({esc(year)}).'
    pieces.append(details)
    doi = pub.get('doi')
    url = pub.get('url') or pub.get('doi_url')
    if doi:
        doi = str(doi).replace('https://doi.org/', '').replace('http://doi.org/', '')
        pieces.append(link(f'doi:{doi}', 'https://doi.org/' + doi))
    elif url:
        label = 'arXiv:' + str(pub.get('arxiv_id')) if pub.get('arxiv_id') else str(url).replace('https://', '').replace('http://', '')
        pieces.append(link(label, str(url)))
    return ' '.join(pieces)


def translated_education(profile: dict[str, Any], cfg: dict[str, Any], zh: bool) -> list[dict[str, Any]]:
    rows = []
    for e in profile['education']:
        row = dict(e)
        if zh:
            row.update(cfg['zh']['education'][e['institution']])
            row['display_dates'] = chinese_period(e['display_dates'])
        rows.append(row)
    return rows


def appointment_blocks(profile: dict[str, Any], cfg: dict[str, Any], zh: bool, compact: bool) -> list[Block]:
    out = []
    for i, app in enumerate(profile['appointments']):
        a = dict(app)
        if zh:
            a.update(cfg['zh']['appointment'])
            a['display_dates'] = chinese_period(app['display_dates'])
        label = f"<b>{esc(a['institution'])}</b> · {esc(a['role'])}"
        out.append(Block('entry', label, f'appointments[{i}]', meta={'right': a['display_dates']}))
        host_label = a.get('host_label', 'Host')
        out.append(Block('detail', f"{esc(a['department'])} · {esc(host_label)}: {esc(app['host'])}", f'appointments[{i}].host', gap=5 if compact else 8))
    return out


def education_blocks(profile: dict[str, Any], cfg: dict[str, Any], zh: bool, compact: bool) -> list[Block]:
    out = []
    for i, e in enumerate(translated_education(profile, cfg, zh)):
        out.append(Block('entry', f"<b>{esc(e['institution'])}</b> · {esc(e['degree'])}", f'education[{i}]', meta={'right': e['display_dates']}))
        out.append(Block('detail', esc(e['field']), f'education[{i}].field', gap=5 if compact else 8))
    return out


def project_blocks(profile: dict[str, Any], cfg: dict[str, Any], variant: dict[str, Any]) -> list[Block]:
    zh = variant['language'].startswith('zh')
    compact = variant['compact']
    projects = {p['id']: p for p in profile['projects']}
    publications = {p['id']: p for p in profile['publications']}
    out = []
    for pid in variant['project_ids']:
        p = dict(projects[pid])
        if zh:
            p.update(cfg['zh']['projects'][pid])
            p['period'] = chinese_period(projects[pid]['period'])
        paper = publications[p['publication_id']] if p.get('publication_id') else None
        title = link(p['title'], publication_url(paper)) if paper else esc(p['title'])
        # Publication status is informative; current work already has present-
        # tense bullets and a source-backed period, so it needs no extra badge.
        out.append(Block('project', f'<b>{title}</b>', f'projects.{pid}', meta={'right': p['status'] if paper else ''}))
        role = esc(f"{paper['venue']} · {paper['year']}") if paper else ''
        if paper:
            role += ' · ' + link('Paper' if not zh else '论文', publication_url(paper))
        if p.get('code_url'):
            role += ' · ' + link('Code' if not zh else '代码', p['code_url'])
        if role:
            out.append(Block('detail', role, f'projects.{pid}.role', gap=3))
        for i, bullet in enumerate(p['bullets']):
            out.append(Block('bullet', esc(bullet), f'projects.{pid}.bullets[{i}]', gap=2))
        out.append(Block('space', gap=5 if compact else 8))
    return out


def skills_blocks(profile: dict[str, Any], cfg: dict[str, Any], zh: bool) -> list[Block]:
    out = []
    for i, group in enumerate(profile['skills']):
        label = group['label']
        items = group['items']
        if zh:
            label = cfg['zh']['skill_labels'].get(label, label)
            items = [cfg['zh']['skills'].get(item, item) for item in items]
        out.append(Block('body', f'<b>{esc(label)}</b> · {esc(" / ".join(items))}', f'skills[{i}]', gap=2))
    return out


def awards_blocks(profile: dict[str, Any], cfg: dict[str, Any], zh: bool, compact: bool) -> list[Block]:
    out = []
    for i, award in enumerate(profile['awards']):
        title = cfg['zh']['awards'].get(award['title'], award['title']) if zh else award['title']
        out.append(Block('body', f"<b>{esc(award['year'])}</b> · {esc(title)}", f'awards[{i}]', gap=2))
    return out


def software_blocks(profile: dict[str, Any], cfg: dict[str, Any], variant: dict[str, Any]) -> list[Block]:
    out = []
    names = variant['software_ids']
    zh = variant['language'].startswith('zh')
    for i, software in enumerate(profile['software']):
        if software['name'] not in names:
            continue
        desc = cfg['zh']['software'][software['name']] if zh else software['description']
        out.append(Block('body', f"<b>{link(software['name'], software['url'])}</b> · {esc(desc)}", f'software[{i}]', gap=5))
    return out


def background_blocks(profile: dict[str, Any], cfg: dict[str, Any], zh: bool, compact: bool) -> list[Block]:
    rows = profile['research_background'][:1] if compact else profile['research_background']
    blocks = []
    for row in rows:
        title = row['title_zh'] if zh else row['title']
        body = row['short_zh' if zh else 'short']
        blocks.append(Block('body', f'<b>{esc(title)}</b> · {esc(body)}', f'research_background.{row["id"]}', gap=5))
    return blocks


def compact_education(profile: dict[str, Any], cfg: dict[str, Any], zh: bool) -> list[Block]:
    out = []
    for i, row in enumerate(translated_education(profile, cfg, zh)):
        title = f"<b>{esc(row['institution'])}</b> · {esc(row['degree'])}"
        out.append(Block('entry', title, f'education[{i}]', meta={'right': row['display_dates']}))
        out.append(Block('detail', esc(row['field']), f'education[{i}].field', gap=4))
    out += appointment_blocks(profile, cfg, zh, True)
    return out


def model(profile: dict[str, Any], cfg: dict[str, Any], variant: dict[str, Any]) -> list[list[Block]]:
    zh = variant['language'].startswith('zh')
    compact = variant['compact']
    pubs = profile['publications']
    if not pubs:
        raise ValueError('A final CV requires verified publications in profile.json')
    pub_map = {p['id']: p for p in pubs}
    chosen = pubs if variant['publication_ids'] == 'all' else [pub_map[pid] for pid in variant['publication_ids']]
    summary_key = 'internship_summary' if compact else 'summary'
    summary = cfg['zh'][summary_key] if zh else profile['narrative']['internship_summary' if compact else 'resume_summary']
    page1 = [Block('body', esc(summary), 'narrative.internship_summary' if compact else 'narrative.resume_summary', gap=5)]
    page1.append(Block('section', '教育与科研经历' if zh else 'EDUCATION & RESEARCH APPOINTMENTS'))
    page1 += compact_education(profile, cfg, zh)
    page1.append(Block('section', '代表性研究' if zh else 'SELECTED RESEARCH'))
    page1 += project_blocks(profile, cfg, variant)
    page1.append(Block('section', '当前研究' if zh else 'CURRENT RESEARCH'))
    current = cfg['zh']['current_summary'] if zh else profile['narrative']['current_summary']
    current_text = esc(current).replace('；', '。<br/>', 1) if zh else esc(current)
    page1.append(Block('body', current_text, 'narrative.current_summary', gap=7))
    if compact:
        page1.append(Block('section', '奖励' if zh else 'AWARD'))
        page1 += awards_blocks(profile, cfg, zh, compact)
        page1.append(Block('section', '其他经历' if zh else 'ADDITIONAL EXPERIENCE'))
        page1 += background_blocks(profile, cfg, zh, compact)
        return [page1]
    page2 = [Block('section', '论文' if zh else 'PUBLICATIONS')]
    journals = sorted((p for p in chosen if not is_preprint(p)), key=lambda p: str(p['year']), reverse=True)
    preprints = [p for p in chosen if is_preprint(p)]
    page2.append(Block('subsection', '同行评议期刊论文' if zh else 'Peer-reviewed journal articles'))
    for i, pub in enumerate(journals):
        page2.append(Block('publication', pub_reference(pub), f'publications.{pub["id"]}', gap=8, meta={'number': i + 1}))
    if preprints:
        page2.append(Block('subsection', '预印本' if zh else 'Preprints'))
        for i, pub in enumerate(preprints):
            page2.append(Block('publication', pub_reference(pub), f'publications.{pub["id"]}', gap=8, meta={'number': len(journals) + i + 1}))
    page2.append(Block('section', '研究代码' if zh else 'RESEARCH CODE'))
    page2 += software_blocks(profile, cfg, variant)
    page2.append(Block('section', '其他经历' if zh else 'ADDITIONAL EXPERIENCE'))
    page2 += background_blocks(profile, cfg, zh, compact)
    page2.append(Block('section', '奖励' if zh else 'AWARD'))
    page2 += awards_blocks(profile, cfg, zh, compact)
    return [page1, page2]


def is_preprint(pub: dict[str, Any]) -> bool:
    return any('preprint' in str(pub.get(key, '')).lower() or 'arxiv' in str(pub.get(key, '')).lower() for key in ['type', 'status', 'venue', 'journal'])


def publication_url(pub: dict[str, Any]) -> str:
    url = pub.get('url') or pub.get('doi_url')
    if is_preprint(pub) and url and str(url).startswith('https://arxiv.org/'):
        return str(url)
    if pub.get('doi'):
        doi = str(pub['doi']).replace('https://doi.org/', '').replace('http://doi.org/', '')
        return 'https://doi.org/' + doi
    if url:
        return str(url)
    raise ValueError(f'Publication has no link target: {pub.get("id")}')


def compact_reference(pub: dict[str, Any]) -> str:
    venue = pub.get('venue') or pub.get('journal')
    title = link(pub['title'], publication_url(pub))
    return f'<b>{title}</b>. <i>{esc(venue)}</i> ({esc(pub["year"])}).'


class CVTemplate:
    """One text-first ReportLab layout for all variants (no layout tables)."""
    def __init__(self, profile: dict[str, Any], config: dict[str, Any], variant: dict[str, Any], path: Path):
        self.profile, self.config, self.variant, self.path = profile, config, variant, path
        self.zh = variant['language'].startswith('zh')
        self.compact = variant['compact']
        self.palette = {k: colors.HexColor(v) for k, v in config['palette'].items()}
        self.font = 'CVChinese' if self.zh else 'CVSans'
        self.bold = 'CVChineseBold' if self.zh else 'CVSansBold'
        self.title_font = 'CVChineseBold' if self.zh else 'CVSansBold'
        self.body_size = variant['body_size']
        self.leading = variant['leading']
        self.c = canvas.Canvas(str(path), pagesize=A4, pageCompression=1, invariant=1)
        self.c.setAuthor(profile['person']['name'])
        self.c.setTitle(profile['person']['name'] + ' | ' + variant['label'])
        self.c.setSubject('Computational imaging, physics-aware learning and image-free perception')
        self.c.setCreator(profile['person']['name'])
        self.c._doc.info.producer = ''
        self.positions: list[dict[str, Any]] = []
        self.y = 0.0

    def style(self, kind: str, **updates: Any) -> ParagraphStyle:
        settings: dict[str, Any] = dict(fontName=self.font, fontSize=self.body_size, leading=self.leading, textColor=self.palette['ink'], allowWidows=0, allowOrphans=0, alignment=TA_LEFT, splitLongWords=0, uriWasteReduce=0)
        if self.zh:
            settings['wordWrap'] = 'CJK'
        if kind == 'detail':
            settings.update(fontSize=self.body_size - 0.55, leading=self.leading - 1.0, textColor=self.palette['muted'])
        elif kind == 'publication':
            settings.update(fontName='CVSans', fontSize=self.variant['publication_size'], leading=self.variant['publication_leading'])
            settings.pop('wordWrap', None)
        elif kind in ('project', 'entry'):
            settings.update(fontSize=self.body_size + (0.2 if kind == 'project' else 0), leading=self.leading + 0.1)
        settings.update(updates)
        return ParagraphStyle(kind, **settings)

    def paragraph(self, text: str, kind: str, width: float = WIDTH, x: float = LEFT) -> float:
        p = Paragraph(text, self.style(kind))
        _, height = p.wrap(width, PAGE_H)
        if self.y - height < BOTTOM:
            raise ValueError(f'{self.variant["id"]} page overflow at {strip_markup(text)[:100]!r}; y={self.y:.1f}, height={height:.1f}')
        p.drawOn(self.c, x, self.y - height)
        self.y -= height
        return height

    def header(self, number: int, total: int) -> None:
        c, p = self.c, self.profile['person']
        c.setFillColor(self.palette['paper'])
        c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        if number == 1:
            name_size, field_size = (26 if self.zh else 28), 10.2
            name_baseline = PAGE_H - 68
            c.setFont(self.title_font, name_size)
            c.setFillColor(self.palette['ink'])
            name = p['name_zh'] + '  /  ' + p['name'] if self.zh else p['name']
            c.drawString(LEFT, name_baseline, name)
            # Place the next line from font bounds, leaving 9 pt of clear space.
            # A fixed 13 pt baseline step overlapped a 32 pt name's descenders.
            name_bottom = name_baseline + pdfmetrics.getDescent(self.title_font, name_size)
            field_top_offset = field_size + pdfmetrics.getDescent(self.font, field_size)
            self.y = name_bottom - 9 - field_top_offset
            c.setFont(self.font, field_size)
            c.setFillColor(self.palette['accent'])
            field = self.config['zh']['field'] if self.zh else self.profile['narrative']['field']
            c.drawString(LEFT, self.y, field)
            self.y -= 18
            contacts = [link(p['email'], 'mailto:' + p['email']), link(p['institutional_email'], 'mailto:' + p['institutional_email']), esc(self.config['zh']['location'] if self.zh else p['location'])]
            self.paragraph(' · '.join(contacts), 'detail')
            self.y -= 3
            names = ['GitHub', 'Google Scholar', 'ORCID']
            profiles = {x['name']: x['url'] for x in self.profile['profiles']}
            shortlabels = {'GitHub': 'GitHub / Hanyuyuan6', 'Google Scholar': 'Google Scholar', 'ORCID': 'ORCID / 0009-0005-7643-6331'}
            self.paragraph(' · '.join(link(shortlabels[n], profiles[n]) for n in names), 'detail')
            self.y -= 10 if self.compact else 13
        else:
            c.setFont(self.title_font, 17)
            c.setFillColor(self.palette['ink'])
            c.drawString(LEFT, PAGE_H - 53, p['name_zh'] + ' / ' + p['name'] if self.zh else p['name'])
            c.setFont(self.font, 8)
            c.setFillColor(self.palette['muted'])
            c.drawRightString(PAGE_W - RIGHT, PAGE_H - 52, self.variant['label'])
            self.y = PAGE_H - 76
        c.setStrokeColor(self.palette['rule'])
        c.setLineWidth(0.5)
        c.line(LEFT, 38, PAGE_W - RIGHT, 38)
        c.setFillColor(self.palette['muted'])
        c.setFont(self.font, 7.3)
        updated = datetime.strptime(self.profile['updated'], '%Y-%m-%d').strftime('%b %Y')
        footer_label = self.variant['label'] + ' · ' + updated
        c.drawString(LEFT, 25, footer_label)
        c.drawRightString(PAGE_W - RIGHT, 25, f'{number} / {total}')

    def draw_block(self, block: Block, page: int) -> None:
        start = self.y
        if block.kind == 'space':
            self.y -= block.gap
            return
        if block.kind == 'section':
            self.y -= 10 if self.compact else 11
            c = self.c
            c.setFillColor(self.palette['accent'])
            c.setFont(self.bold, 9.0 if not self.zh else 10.6)
            c.drawString(LEFT, self.y - 9, block.text)
            c.setStrokeColor(self.palette['rule'])
            c.setLineWidth(0.5)
            textw = pdfmetrics.stringWidth(block.text, self.bold, 9.0 if not self.zh else 10.6)
            c.line(LEFT + textw + 12, self.y - 6, PAGE_W - RIGHT, self.y - 6)
            self.y -= 20 if self.compact else 22
        elif block.kind == 'subsection':
            self.y -= 2
            self.paragraph('<b>' + esc(block.text) + '</b>', 'detail')
            self.y -= 7
        elif block.kind in ('project', 'entry'):
            # Reserve only the width the date/status needs. Titles wrap before
            # that reserved lane; all extracted text remains in reading order.
            right = clean_text(block.meta.get('right', ''))
            rsize = self.body_size - 1.0
            rightwidth = pdfmetrics.stringWidth(right, self.font, rsize)
            reserve = rightwidth + 14 if right else 0
            self.paragraph(block.text, block.kind, width=WIDTH - reserve)
            if right:
                self.c.setFont(self.font, rsize)
                self.c.setFillColor(self.palette['muted'])
                self.c.drawRightString(PAGE_W - RIGHT, start - self.leading + 3, right)
            self.y -= 2
        elif block.kind == 'bullet':
            self.c.setFillColor(self.palette['accent'])
            self.c.circle(LEFT + 2.1, self.y - 6, 1.1, stroke=0, fill=1)
            self.paragraph(block.text, 'body', width=WIDTH - 12, x=LEFT + 12)
            self.y -= block.gap
        elif block.kind == 'publication' and block.meta.get('number'):
            self.c.setFillColor(self.palette['accent'])
            self.c.setFont('CVSansBold', self.variant['publication_size'] - 0.5)
            self.c.drawString(LEFT, self.y - self.variant['publication_leading'] + 3, f'{block.meta["number"]:02d}')
            self.paragraph(block.text, 'publication', width=WIDTH - 23, x=LEFT + 23)
            self.y -= block.gap
        else:
            self.paragraph(block.text, block.kind)
            self.y -= block.gap
        self.positions.append({'page': page, 'kind': block.kind, 'source': block.source, 'top': round(start, 2), 'bottom': round(self.y, 2), 'text': strip_markup(block.text)})
        if self.y < BOTTOM:
            raise ValueError(f'{self.variant["id"]} page {page}: content below lower bound at {block.kind}: {strip_markup(block.text)[:90]} (y={self.y:.1f})')

    def build(self, pages: list[list[Block]]) -> dict[str, Any]:
        for number, blocks in enumerate(pages, 1):
            self.header(number, len(pages))
            for block in blocks:
                self.draw_block(block, number)
            self.c.showPage()
        self.c.save()
        return {'page_count': len(pages), 'lowest_content_y': min(p['bottom'] for p in self.positions), 'positions': self.positions}


def render_html(profile: dict[str, Any], config: dict[str, Any], variant: dict[str, Any], pages: list[list[Block]], path: Path, source_fingerprint: str) -> None:
    zh = variant['language'].startswith('zh')
    person = profile['person']
    display_name = person['name_zh'] + ' / ' + person['name'] if zh else person['name']
    fieldname = config['zh']['field'] if zh else profile['narrative']['field']
    profiles = {p['name']: p['url'] for p in profile['profiles']}
    links = ' · '.join([link(person['email'], 'mailto:' + person['email']), link(person['institutional_email'], 'mailto:' + person['institutional_email']), esc(config['zh']['location'] if zh else person['location']), link('GitHub', profiles['GitHub']), link('Google Scholar', profiles['Google Scholar']), link('ORCID', profiles['ORCID'])])
    bodies = []
    for page_no, blocks in enumerate(pages, 1):
        elements = []
        for block in blocks:
            attrs = f' class="{esc(block.kind)}"' + (f' data-source="{esc(block.source)}"' if block.source else '')
            if block.kind == 'space':
                continue
            if block.kind == 'section':
                elements.append(f'<h2{attrs}>{esc(block.text)}</h2>')
            elif block.kind == 'subsection':
                elements.append(f'<h3{attrs}>{esc(block.text)}</h3>')
            elif block.kind in ('entry', 'project'):
                right = f'<span class="right">{esc(block.meta["right"])}</span>' if block.meta.get('right') else ''
                elements.append(f'<p{attrs}><span>{block.text}</span>{right}</p>')
            elif block.kind == 'bullet':
                elements.append(f'<p{attrs}><span aria-hidden="true">•</span><span>{block.text}</span></p>')
            else:
                number = f'<span class="pub-number">{block.meta["number"]:02d}</span>' if block.meta.get('number') else ''
                elements.append(f'<p{attrs}>{number}<span>{block.text}</span></p>')
        header = f'<header><p class="document-label">{esc(variant["label"])}</p><h1>{esc(display_name)}</h1><p class="field">{esc(fieldname)}</p><p class="contact">{links}</p></header>' if page_no == 1 else f'<header class="continued"><p>{esc(display_name)}</p><span>{esc(variant["label"])}</span></header>'
        bodies.append(f'<section class="page" aria-label="Page {page_no}">{header}<main>{"".join(elements)}</main><footer><span>{esc(variant["label"])} · {esc(profile["updated"])}</span><span>{page_no} / {len(pages)}</span></footer></section>')
    palette = config['palette']
    css = f'''*{{box-sizing:border-box}}:root{{--paper:{palette['paper']};--ink:{palette['ink']};--muted:{palette['muted']};--accent:{palette['accent']};--rule:{palette['rule']};--link:{palette.get('link', palette['accent'])}}}body{{margin:0;padding:32px 20px;background:#edf0f3;color:var(--ink);font-family:"Noto Sans","Helvetica Neue",Arial,"PingFang SC","Heiti SC",sans-serif;font-size:13px;line-height:1.52}}a{{color:var(--link);text-decoration:none}}a:hover{{color:var(--link);text-decoration:underline}}.page{{position:relative;max-width:794px;min-height:1123px;margin:0 auto 28px;padding:51px 59px 75px;background:var(--paper);box-shadow:0 8px 35px #25282416}}h1{{font-family:inherit;font-size:37px;line-height:1.18;margin:0 0 14px;font-weight:700;letter-spacing:-.5px}}.document-label{{float:right;margin:9px 0;font-size:10px;letter-spacing:1.4px;text-transform:uppercase;color:var(--muted)}}.field{{color:var(--accent);font-size:13.5px;margin:0 0 12px}}.contact{{color:var(--muted);font-size:10.5px;line-height:1.7;max-width:100%;overflow-wrap:anywhere;margin:0 0 21px}}h2{{display:flex;align-items:center;gap:16px;font-size:11.7px;letter-spacing:1.35px;line-height:1.3;color:var(--accent);margin:20px 0 11px;font-weight:700}}h2:after{{content:"";height:1px;background:var(--rule);flex:1}}h3{{font-size:11px;color:var(--muted);font-weight:600;margin:5px 0 11px}}p{{margin:0 0 4px}}.entry,.project{{display:flex;justify-content:space-between;gap:15px;font-size:13px;align-items:baseline;margin-top:4px}}.right{{font-size:11px;white-space:nowrap;color:var(--muted);font-weight:400}}.detail{{font-size:11.5px;color:var(--muted);margin-bottom:8px}}.bullet{{display:flex;gap:9px;margin-left:0;margin-bottom:4px}}.bullet>span:first-child{{color:var(--accent);flex:0 0 7px}}.publication{{display:flex;gap:14px;font-size:12px;line-height:1.5;margin-bottom:13px}}.publication i{{font-style:italic}}.pub-number{{color:var(--accent);font-size:11px;flex:0 0 17px;padding-top:2px}}.body{{margin-bottom:5px}}.continued{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:20px}}.continued:before{{position:absolute;top:51px}}.continued p{{font-size:23px;font-weight:600}}.continued>span{{font-size:10.5px;color:var(--muted)}}footer{{position:absolute;bottom:28px;left:59px;right:59px;border-top:1px solid var(--rule);padding-top:12px;display:flex;justify-content:space-between;font-size:9.5px;color:var(--muted)}}.internship .page{{font-size:12.7px}}.internship h2{{margin-top:16px;margin-bottom:9px}}.internship .publication{{font-size:11.2px;margin-bottom:6px}}.internship .detail{{margin-bottom:5px}}html[lang="zh-CN"] body{{line-height:1.65}}html[lang="zh-CN"] h1{{font-family:"Heiti SC","PingFang SC",sans-serif;letter-spacing:0}}@media(max-width:700px){{body{{padding:12px 8px}}.page{{min-height:0;padding:30px 22px 80px}}h1{{font-size:35px}}.document-label{{float:none;font-size:9px;margin:0 0 10px}}header:before{{margin-top:-10px}}.contact{{font-size:11px}}.entry,.project{{display:block}}.right{{display:block;margin-top:3px}}h2{{font-size:11px;letter-spacing:.7px}}footer{{left:22px;right:22px}}}}@media print{{body{{padding:0;background:white}}.page{{box-shadow:none;margin:0;max-width:none;width:210mm;height:297mm;min-height:297mm;break-after:page;padding:13.5mm 15.6mm 20mm}}.page:last-child{{break-after:auto}}a{{text-decoration:none}}@page{{size:A4;margin:0}}}}'''
    title = person['name'] + ' | ' + variant['label']
    body_class = variant['id'] + (' internship' if variant['compact'] and variant['id'] != 'internship' else '')
    content = f'<!doctype html>\n<html lang="{esc(variant["language"])}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><meta name="source-fingerprint" content="{source_fingerprint}"><title>{esc(title)}</title><style>{css}</style></head><body class="{esc(body_class)}">{"".join(bodies)}</body></html>\n'
    content = content.replace(f' color="{LINK_COLOR}"', '')
    path.write_text(content, encoding='utf-8', newline='\n')


def validate_fonts(pages: list[list[Block]], variant: dict[str, Any]) -> None:
    """Check every emitted code point against the embedded font's cmap."""
    zh = variant['language'].startswith('zh')
    bodyfont = pdfmetrics.getFont('CVChinese' if zh else 'CVSans')
    pubfont = pdfmetrics.getFont('CVSans')
    failures = []
    for blocks in pages:
        for block in blocks:
            face = pubfont.face if block.kind == 'publication' else bodyfont.face
            text = strip_markup(block.text) + clean_text(block.meta.get('right', ''))
            missing = {c for c in text if not c.isspace() and ord(c) not in face.charToGlyph}
            if missing:
                failures.append({'source': block.source, 'missing': sorted(missing)})
    if failures:
        raise ValueError(f'Unsupported font glyphs: {failures}')


class AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == 'a':
            self.current = {'uri': dict(attrs).get('href') or '', 'text': ''}

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current['text'] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == 'a' and self.current is not None:
            self.links.append(self.current)
            self.current = None


def verify_pdf_links(path: Path, pages: list[list[Block]], positions: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    """Require visible link labels to lie inside real PDF URI annotations."""
    norm = lambda text: re.sub(r'\s+', '', text)
    verified = []
    publication_titles = 0
    project_papers = 0
    project_codes = 0
    profile_links = {p['name']: p['url'] for p in profile['profiles']}
    header_links = [
        {'uri': 'mailto:' + profile['person'][key], 'text': profile['person'][key]}
        for key in ['email', 'institutional_email']
    ] + [
        {'uri': profile_links['GitHub'], 'text': 'GitHub / Hanyuyuan6'},
        {'uri': profile_links['Google Scholar'], 'text': 'Google Scholar'},
        {'uri': profile_links['ORCID'], 'text': 'ORCID / 0009-0005-7643-6331'},
    ]
    with pdfplumber.open(path) as pdf:
        for page_number, blocks in enumerate(pages, 1):
            page = pdf.pages[page_number - 1]
            expected = []
            for block in blocks:
                collector = AnchorCollector()
                collector.feed(block.text)
                if not collector.links:
                    continue
                pos = next(p for p in positions if p['page'] == page_number and p['kind'] == block.kind and p['source'] == block.source)
                for anchor in collector.links:
                    # CJK font ascents can put the annotation above the flow box.
                    expected.append((anchor, block.source, page.height - pos['top'] - 8, page.height - pos['bottom'] + 8))
                if block.kind == 'publication':
                    publication_titles += 1
                if block.kind == 'detail' and block.source.startswith('projects.'):
                    project_papers += sum(a['text'] in ['Paper', '论文'] for a in collector.links)
                    project_codes += sum(a['text'] in ['Code', '代码'] for a in collector.links)
            if page_number == 1:
                expected += [(a, 'header', 0, 180) for a in header_links]
            for anchor, source, top, bottom in expected:
                annotations = [a for a in page.hyperlinks if a.get('uri') == anchor['uri'] and a['top'] >= top and a['bottom'] <= bottom]
                covered = ''.join(c['text'] for c in page.chars if any(a['x0'] - 0.6 <= (c['x0'] + c['x1']) / 2 <= a['x1'] + 0.6 and a['top'] - 0.6 <= (c['top'] + c['bottom']) / 2 <= a['bottom'] + 0.6 for a in annotations))
                if norm(anchor['text']) not in norm(covered):
                    raise ValueError(f'{path.name}: no PDF link annotation over visible {source} label {anchor["text"]!r}')
                verified.append({'page': page_number, 'source': source, 'label': anchor['text'], 'uri': anchor['uri'], 'annotation_count': len(annotations)})
    return {'verified_link_labels': len(verified), 'publication_title_links': publication_titles, 'project_paper_links': project_papers, 'project_code_links': project_codes, 'visible_labels_match_annotations': True, 'links': verified}


def validate_pdf(path: Path, pages: list[list[Block]], expected_pages: int, positions: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    reader = PdfReader(path)
    if len(reader.pages) != expected_pages:
        raise ValueError(f'{path.name}: expected {expected_pages} pages, got {len(reader.pages)}')
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    if '\ufffd' in text or '\x00' in text or not text.strip():
        raise ValueError(f'{path.name}: invalid or empty extracted text')
    if 'Yuyuan Han' not in text:
        raise ValueError(f'{path.name}: candidate name missing in extraction')
    # These are the owner's explicitly selected accomplishments, not an
    # inference from the variant configuration that could itself omit one.
    plain = re.sub(r'\s+', '', text)
    required = ['TheLiftSpectrum', 'Hadamard', 'LDGI']
    if any(term not in plain for term in required):
        raise ValueError(f'{path.name}: a selected research project is missing')
    if not ('electro-optic' in text.lower() or '电光' in text):
        raise ValueError(f'{path.name}: electro-optic materials/device experience missing')
    if any(term in text for term in ['CET-6', 'Programming and modeling', 'TECHNICAL SKILLS', '专业技能']):
        raise ValueError(f'{path.name}: generic skills/language section should not be displayed')
    with pdfplumber.open(path) as actual:
        chars = actual.pages[0].chars
        name_chars = [ch for ch in chars if ch['top'] < 100 and ch['size'] >= 24]
        subtitle_chars = [ch for ch in chars if ch['top'] < 120 and abs(ch['size'] - 10.2) < 0.05]
        if not name_chars or not subtitle_chars:
            raise ValueError(f'{path.name}: cannot locate name and subtitle for spacing check')
        header_gap = min(ch['top'] for ch in subtitle_chars) - max(ch['bottom'] for ch in name_chars)
        if not 8 <= header_gap <= 16:
            raise ValueError(f'{path.name}: name/subtitle clear gap {header_gap:.2f} pt, expected 8-16 pt')
    fonts = []
    annotations = 0
    for page in reader.pages:
        annotations += len(page.get('/Annots', []))
        for f in page['/Resources'].get('/Font', {}).values():
            obj = f.get_object()
            if obj.get('/BaseFont') == '/Helvetica':
                # ReportLab installs a dormant default font; it has no CV text.
                continue
            descriptor = obj.get('/FontDescriptor')
            embedded = bool(descriptor and any(k in descriptor.get_object() for k in ['/FontFile', '/FontFile2', '/FontFile3']))
            if not embedded:
                raise ValueError(f'{path.name}: unembedded used font {obj.get("/BaseFont")}')
            fonts.append(str(obj.get('/BaseFont')))
    for blocks in pages:
        for block in blocks:
            if block.kind == 'publication':
                title = strip_markup(block.text)
                if len(title) < 15:
                    raise ValueError(f'{path.name}: empty publication block')
    return {'page_count': len(reader.pages), 'extracted_characters': len(text), 'embedded_fonts': sorted(set(fonts)), 'link_annotation_count': annotations, 'hyperlink_coverage': verify_pdf_links(path, pages, positions, profile), 'text_extractable': True, 'replacement_characters': 0, 'name_subtitle_gap_pt': round(header_gap, 2)}


def clean_pdf_provenance(path: Path) -> dict[str, Any]:
    """Run the owner's required cleaner on the actual authored PDF."""
    cleaner = Path.home() / '.agents/skills/nature-shared/scripts/provenance_hygiene.py'
    if not cleaner.is_file():
        raise FileNotFoundError('Required provenance_hygiene.py is unavailable')
    cleaned = path.with_name('.' + path.stem + '.cleaned.pdf')
    command = [sys.executable, str(cleaner), 'clean', str(path), '-o', str(cleaned), '--force', '--json']
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f'PDF provenance cleanup failed ({done.returncode}): {done.stderr[-400:]} {done.stdout[-400:]}')
    report = json.loads(done.stdout)
    if report.get('residue'):
        raise ValueError(f'PDF provenance cleanup left residue: {report["residue"]}')
    cleaned.replace(path)
    return {key: report[key] for key in ['layer', 'residue', 'objects_cleaned', 'content'] if key in report} | {'exit_code': done.returncode, 'verified': True}


def read_prior(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {}


def preserve_visual_qa(manifest: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    qa = prior.get('visual_qa', {})
    hashes = {o['variant']: o['pdf']['sha256'] for o in manifest['outputs']}
    if qa.get('status') == 'passed' and qa.get('reviewed_pdf_sha256') == hashes:
        manifest['visual_qa'] = qa
    return manifest


def build(profile_path: Path, output_dir: Path, private_dir: Path) -> dict[str, Any]:
    global LINK_COLOR
    profile_path, output_dir, private_dir = Path(profile_path).resolve(), Path(output_dir).resolve(), Path(private_dir).resolve()
    if private_dir == HERE or HERE in private_dir.parents:
        raise ValueError('The Chinese CV must stay outside the public website repository')
    if output_dir == private_dir or output_dir in private_dir.parents:
        raise ValueError('The Chinese CV must stay outside the public output directory')
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    config_path = HERE / 'cv-variants.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    LINK_COLOR = config['palette'].get('link', LINK_COLOR)
    validate_translations(profile, config)
    load_fonts()
    output_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)
    inputs = {'profile': {'path': profile_path.name, 'sha256': sha256(profile_path)}, 'template': {'path': Path(__file__).name, 'sha256': sha256(Path(__file__))}, 'variants': {'path': config_path.name, 'sha256': sha256(config_path)}}
    fingerprint = hashlib.sha256(json.dumps({k: v['sha256'] for k, v in inputs.items()}, sort_keys=True).encode()).hexdigest()
    manifest: dict[str, Any] = {'schema_version': 1, 'built_at': datetime.now(timezone.utc).isoformat(), 'source_fingerprint': fingerprint, 'inputs': inputs, 'fonts': FONT_SOURCES, 'outputs': [], 'visual_qa': {'status': 'pending', 'method': 'pdftoppm and view_image on every latest PDF page'}}
    prior_public = read_prior(HERE / 'cv-build.json')
    prior_private = read_prior(private_dir / 'cv-build.json')
    prior_outputs = {o['variant']: o for prior in [prior_public, prior_private] for o in prior.get('outputs', [])}
    for variant in config['variants']:
        pages = model(profile, config, variant)
        if len(pages) != variant['pages']:
            raise ValueError('Content model page count disagrees with variants config')
        validate_fonts(pages, variant)
        dest = output_dir if variant['destination'] == 'public' else private_dir
        pdf_path = dest / (variant['filename'] + '.pdf')
        html_path = dest / (variant['filename'] + '.html')
        generated_path = dest / ('.' + variant['filename'] + '.generated.pdf')
        geometry = CVTemplate(profile, config, variant, generated_path).build(pages)
        authored_pdf_sha256 = sha256(generated_path)
        prior = prior_outputs.get(variant['id'], {})
        if prior.get('authored_pdf_sha256') == authored_pdf_sha256 and prior.get('provenance', {}).get('verified') and pdf_path.is_file() and prior.get('pdf', {}).get('sha256') == sha256(pdf_path):
            generated_path.unlink()
            provenance = prior['provenance']
        else:
            generated_path.replace(pdf_path)
            provenance = clean_pdf_provenance(pdf_path)
        render_html(profile, config, variant, pages, html_path, fingerprint)
        checks = validate_pdf(pdf_path, pages, variant['pages'], geometry['positions'], profile)
        manifest['outputs'].append({'variant': variant['id'], 'language': variant['language'], 'destination': variant['destination'], 'authored_pdf_sha256': authored_pdf_sha256, 'provenance': provenance, 'pdf': {'path': pdf_path.name, 'sha256': sha256(pdf_path), 'bytes': pdf_path.stat().st_size}, 'html': {'path': html_path.name, 'sha256': sha256(html_path), 'bytes': html_path.stat().st_size}, 'checks': checks, 'layout': geometry, 'sources': sorted({b.source for p in pages for b in p if b.source})})
    public_manifest = preserve_visual_qa(dict(manifest, outputs=[o for o in manifest['outputs'] if o['destination'] == 'public']), prior_public)
    private_manifest = preserve_visual_qa(dict(manifest, outputs=[o for o in manifest['outputs'] if o['destination'] == 'private']), prior_private)
    (HERE / 'cv-build.json').write_text(json.dumps(public_manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    (private_dir / 'cv-build.json').write_text(json.dumps(private_manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--private-out', type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.profile, args.out, args.private_out)
    print(json.dumps({'source_fingerprint': manifest['source_fingerprint'], 'outputs': [{'variant': o['variant'], 'pages': o['checks']['page_count'], 'pdf': o['pdf']['path'], 'html': o['html']['path']} for o in manifest['outputs']]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
