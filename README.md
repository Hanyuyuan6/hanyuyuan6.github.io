# Yuyuan Han — academic website

Source for <https://hanyuyuan6.github.io/>: a static, research-first homepage plus an academic CV and a one-page research-internship résumé. All facts live in `profile.json`; everything else is generated from it.

## Layout

| Path | Role |
|---|---|
| `profile.json` | Single source of truth: identity, news, selected research, publications, education, code, awards |
| `template.html`, `style.css` | Homepage markup and styles |
| `theme.json` | The one colour source: five light palettes (`selected` picks one) and the automatic dark palette, used by the site, icons, social card and CVs |
| `build_site.py` | Builds `dist/`: homepage, 404 page, sitemap, robots.txt, YH monogram icons, social card, `publications.bib` and responsive figure previews |
| `build_cv.py`, `cv-variants.json` | Builds the CVs (same content model, variant-specific selection) |
| `research-figures.json` | Reviewed paper figures: source, hash, preview crop and alt text |
| `fonts/` | Noto Sans 2.015 (SIL Open Font License 1.1, see `fonts/OFL.txt`) for reproducible PDFs |
| `check.py` | Content, link, image, metadata and allowlist checks on the built site and PDFs |
| `check_layout.py` | Renders the site in Chromium at 1440/1024/390 px (and 1440 px dark) and checks spacing, overflow, image sharpness and BibTeX-toggle alignment |
| `watch_profiles.py` | Weekly job: compares ORCID and GitHub with `profile.json` (works, biography, keywords, links, emails, affiliations, reviews) and opens one issue on drift, with a paste-ready checklist for platforms without an API |
| `public-files.json` | Explicit allowlist of every published file |

## Build and check

Python 3.12:

```sh
python -m pip install -r requirements-build.txt
python build.py --site-only        # website + checks, reusing the reviewed PDFs
python build.py --public-only      # also rebuild the English CVs (any OS)
python build.py                    # all four CVs; Chinese variants go to ../简历 and need macOS STHeiti
python build_cv.py --verify        # rebuild the public CVs in a temporary folder and compare with dist/cv
python -m pip install playwright==1.62.0 && python -m playwright install chromium
python check_layout.py             # writes full-page screenshots to .qa/layout/
```

GitHub Actions runs the same build, the CV reproducibility check and the layout check on every push and pull request, and deploys `dist/` from `main` to GitHub Pages only when all of them pass.

When a CV's content changes, `check.py` stops until the new pages have been looked at: render every page, read it, then record the new PDF hashes in `cv-review.json`. Passing checks is not a design review.

## Common updates

1. **New paper.** Add one record to `profile.json` → `publications` with the full ordered author list, venue, volume/issue/pages, year, DOI and link; mark arXiv versions `Preprint`. Optionally add a News item. Rebuild, review the CV pages, commit.
2. **New selected work.** Add a project to `projects`, list it in `selected_project_ids`, and add its figure to `research-figures.json` with a SHA-256 hash and a `teaser.crop` box; previews are generated automatically.
3. **End of the visiting period.** Update `person`, `appointments` and `directions` together, then rebuild.
4. **Change the colours.** Set `selected` in `theme.json` to `harbor`, `paper`, `classic`, `graphite` or `ivory` (or `dark` to `null` to drop dark mode), run `python build.py --public-only`, review the CV pages and commit. `check.py` holds every palette to WCAG AA contrast.

Private application materials, credentials and Chinese CV outputs never belong in this repository; the checker rejects any file that is not in `public-files.json`.
