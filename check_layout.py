#!/usr/bin/env python3
"""Render the built pages in a real browser and fail on layout regressions.

At 1440, 1024 and 390 px widths it checks that nothing scrolls sideways, that
every heading in the main content clears the block above it by at least 12 px,
that headings of one level share a single size within a section, that every
image loads at no less than its displayed width, and that each BibTeX toggle
reads as one more link (same type, colour and baseline, no disclosure marker).
The widest view is repeated in dark mode. Full-page screenshots are written
for a human look; passing checks are not a design review.

Usage: python check_layout.py [--site dist] [--out .qa/layout]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
VIEWPORTS = [(1440, 900, 'light'), (1024, 768, 'light'), (390, 844, 'light'), (1440, 900, 'dark')]
MIN_GAP = 12

MEASURE = r"""
() => {
  const main = document.querySelector('main') || document.body;
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    const folded = el.closest('details:not([open])') && el.tagName !== 'SUMMARY';
    return !folded && r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  // Text and image leaves only: list items and figures carry padding that is
  // itself the spacing being measured.
  const leaves = [...main.querySelectorAll('h1,h2,h3,h4,p,img,pre,time,summary')].filter(visible);
  const gaps = [];
  for (const h of main.querySelectorAll('h2,h3,h4')) {
    if (!visible(h)) continue;
    const r = h.getBoundingClientRect();
    let above = -Infinity, from = null;
    for (const el of leaves) {
      if (el === h || el.contains(h) || h.contains(el)) continue;
      const q = el.getBoundingClientRect();
      const overlaps = q.left < r.right - 1 && q.right > r.left + 1;
      if (overlaps && q.bottom <= r.top + 2 && q.bottom > above) { above = q.bottom; from = el; }
    }
    if (from) gaps.push({heading: h.textContent.trim().slice(0, 60), level: h.tagName,
      gap: Math.round((r.top - above) * 10) / 10, after: from.tagName + ': ' + from.textContent.trim().slice(0, 40)});
  }
  const levels = [];
  for (const section of main.querySelectorAll('section')) {
    const sizes = {};
    for (const h of section.querySelectorAll('h3')) {
      if (!visible(h) || h.closest('article, li')) continue;
      const s = getComputedStyle(h); sizes[s.fontSize + '/' + s.fontWeight] = (sizes[s.fontSize + '/' + s.fontWeight] || 0) + 1;
    }
    if (Object.keys(sizes).length > 1) levels.push({section: section.id, sizes});
  }
  // Raw pixel width of the chosen candidate (from its -NNN.webp name), compared
  // with the displayed width at up to 2x density.
  const images = [...document.images].map(img => { const src = (img.currentSrc || img.src).split('/').pop();
    const match = src.match(/-(\d+)\.webp$/); return {src, loaded: img.naturalWidth > 0,
    pixels: match ? Number(match[1]) : img.naturalWidth,
    needed: Math.round(img.getBoundingClientRect().width * Math.min(window.devicePixelRatio, 2))}; });
  // A BibTeX toggle must match the Paper link beside it: same font, colour and
  // text baseline (within 1 px), and no list-item marker in any engine.
  const textBottom = el => { const range = document.createRange(); range.selectNodeContents(el);
    return range.getBoundingClientRect().bottom; };
  const bibtex = [];
  for (const row of main.querySelectorAll('.resource-links')) {
    const link = row.querySelector(':scope > a'), summary = row.querySelector('.bibtex > summary');
    if (!link || !summary || !visible(summary)) continue;
    const a = getComputedStyle(link), s = getComputedStyle(summary);
    const problems = [];
    for (const key of ['fontSize', 'fontWeight', 'fontFamily', 'color']) if (a[key] !== s[key]) problems.push(`${key} ${s[key]} vs ${a[key]}`);
    const offset = Math.abs(textBottom(link) - textBottom(summary));
    if (offset > 1) problems.push(`baseline off by ${offset.toFixed(1)} px`);
    if (s.display === 'list-item' && s.listStyleType !== 'none') problems.push('disclosure marker visible');
    if (problems.length) bibtex.push({row: (row.closest('[id]') || {}).id, problems});
  }
  const linkColour = getComputedStyle(main.querySelector('a')).color;
  return {overflow: document.documentElement.scrollWidth - window.innerWidth, gaps, levels, images, bibtex, linkColour};
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--site', type=Path, default=ROOT / 'dist')
    parser.add_argument('--out', type=Path, default=ROOT / '.qa' / 'layout')
    parser.add_argument('--executable', help='Optional Chromium/headless-shell binary to use.')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pages = ['index.html', '404.html', 'research/lift.html']
    failures, report = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(**({'executable_path': args.executable} if args.executable else {}))
        theme = json.loads((ROOT / 'theme.json').read_text(encoding='utf-8'))
        for width, height, scheme in VIEWPORTS:
            if scheme == 'dark' and not theme.get('dark'):
                continue
            context = browser.new_context(viewport={'width': width, 'height': height}, device_scale_factor=2, color_scheme=scheme)
            page = context.new_page()
            for name in pages:
                page.goto((args.site / name).resolve().as_uri(), wait_until='load')
                # Load lazy images too, so full-page screenshots and size checks see them.
                page.evaluate("document.querySelectorAll('img[loading=lazy]').forEach(i => { i.loading = 'eager'; })")
                page.wait_for_function("[...document.images].every(i => i.complete && i.naturalWidth > 0)", timeout=15000)
                result = page.evaluate(MEASURE)
                shot = args.out / f'{Path(name).stem}-{width}{"-dark" if scheme == "dark" else ""}.png'
                page.screenshot(path=str(shot), full_page=True)
                tag = f'{name}@{width}px{" dark" if scheme == "dark" else ""}'
                for row in result['bibtex']:
                    failures.append(f'{tag}: BibTeX toggle in #{row["row"]} does not match its links: {"; ".join(row["problems"])}')
                expected = theme['palettes'][theme['dark'] if scheme == 'dark' else theme['selected']]['link']
                rgb = 'rgb({}, {}, {})'.format(*(int(expected[i:i + 2], 16) for i in (1, 3, 5)))
                if name == 'index.html' and result['linkColour'] != rgb:
                    failures.append(f'{tag}: links render {result["linkColour"]}, expected the {scheme} palette {expected}')
                if result['overflow'] > 0:
                    failures.append(f'{tag}: horizontal overflow of {result["overflow"]} px')
                # Expanded disclosures (BibTeX, credits) must not widen the page either.
                expanded = page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; }); document.documentElement.scrollWidth - window.innerWidth")
                if expanded > 0:
                    failures.append(f'{tag}: horizontal overflow of {expanded} px with all details expanded')
                if name == 'index.html':
                    # A complete preview must remain compact; the click opens the original.
                    for box in page.locator('.research-figure').evaluate_all('(els) => els.map(el => ({width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}))'):
                        if box['width'] > 280 or box['height'] > 180:
                            failures.append(f'{tag}: oversized research preview: {box}')
                    page.locator('.figure-link').first.click()
                    page.locator('dialog[open] img').wait_for(state='visible')
                    page.keyboard.press('Escape')
                    if page.locator('dialog[open]').count():
                        failures.append(f'{tag}: Escape did not close the complete-figure dialog')
                    page.locator('[data-research-question="design"]').click()
                    if not page.locator('#research-shsi').evaluate("el => el.classList.contains('is-related')"):
                        failures.append(f'{tag}: research question navigation did not emphasize the related paper')
                if name == 'research/lift.html':
                    # Enumerate all 12 filter combinations, including the no-filter state.
                    expected = json.loads((ROOT / 'profile.json').read_text(encoding='utf-8'))['projects'][0]['condition_comparison']['rows']
                    for dataset in ['all', *[row['name'] for row in expected]]:
                        for condition in ['both', 'clean', 'noisy']:
                            page.locator(f'input[name="dataset"][value="{dataset}"]').check()
                            page.locator(f'input[name="condition"][value="{condition}"]').check()
                            rows = page.locator('.result-row:visible').evaluate_all("els => els.map(el => ({name:el.closest('tbody').dataset.dataset,condition:el.dataset.condition,value:Number(el.dataset.value)}))")
                            wanted = [{'name':row['name'],'condition':key,'value':row[key]} for row in expected if dataset in ('all', row['name']) for key in ('clean','noisy') if condition in ('both',key)]
                            if rows != wanted:
                                failures.append(f'{tag}: filter {dataset}/{condition} shows {rows}, expected {wanted}')
                    page.get_by_role('button', name='1D → 2D lift', exact=True).click()
                    if page.locator('#lift-part-title').inner_text() != 'A content-adaptive spatial lift':
                        failures.append(f'{tag}: figure module locator failed')
                    page.get_by_role('button', name='Complete figure', exact=True).click()
                    if page.locator('#lift-figure-viewport').evaluate('el => el.scrollLeft !== 0 || el.scrollTop !== 0'):
                        failures.append(f'{tag}: complete-figure reset failed')
                    path_before = page.locator('#synthetic-wave').get_attribute('d')
                    page.locator('#synthetic-noise').check()
                    if path_before == page.locator('#synthetic-wave').get_attribute('d'):
                        failures.append(f'{tag}: teaching-wave control has no effect')
                for gap in result['gaps']:
                    if gap['gap'] < MIN_GAP:
                        failures.append(f'{tag}: "{gap["heading"]}" sits {gap["gap"]} px below {gap["after"]!r} (minimum {MIN_GAP})')
                for level in result['levels']:
                    failures.append(f'{tag}: inconsistent h3 styles in #{level["section"]}: {level["sizes"]}')
                for image in result['images']:
                    if not image['loaded']:
                        failures.append(f'{tag}: image failed to load: {image["src"]}')
                    elif image['pixels'] < 0.9 * image['needed']:
                        failures.append(f'{tag}: blurry image ({image["pixels"]} px for {image["needed"]} device px): {image["src"]}')
                report.append({'page': tag, 'screenshot': str(shot), 'min_heading_gap': min((g['gap'] for g in result['gaps']), default=None), 'images': result['images']})
            context.close()
        browser.close()
    print(json.dumps({'passed': not failures, 'failures': failures, 'pages': report}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
