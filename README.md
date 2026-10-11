# Yuyuan Han — academic website

A static, research-first website and two public CVs. Content lives in `profile.json`; presentation lives in `template.html`, `style.css` and the shared CV template `build_cv.py`. No web framework, analytics, remote fonts or account system is required.

## Build and check

Use Python 3.12 for the build and checks. Website generation uses the standard library; validation additionally uses `pypdf`. The `--site-only` path reuses the committed, reviewed CV files and needs no local fonts, CV authoring tools or private parent-directory files. It is the cross-platform path used by Linux CI.

```sh
python3 -m pip install -r requirements-check.txt
python3 build.py --site-only
```

Full CV authoring is a separate local workflow using `reportlab` and `pdfplumber`. To regenerate the site and all four configured CV variants in the author's configured environment:

```sh
python3 -m pip install -r requirements-build.txt
python3 build.py --private-out ../简历
```

The Chinese academic CV and internship résumé are written outside this repository. Do not pass a private output directory anywhere inside this repository.

Full CV authoring currently targets the author's macOS environment: Noto Sans/Serif fonts in the Codex runtime under `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/`, STHeiti Light/Medium fonts in `/System/Library/Fonts/`, and the required cleanup script at `~/.agents/skills/nature-shared/scripts/provenance_hygiene.py`. These resources are not redistributed here. Another authoring environment needs the corresponding fonts and cleanup tool and must adapt the paths in `load_fonts()` and `clean_pdf_provenance()` in `build_cv.py`; missing resources fail explicitly. These requirements do not apply to `--site-only` or GitHub Pages CI.

`check.py` verifies metadata, internal anchors and local links, PDF text and page counts, file hashes, current sources and the reviewed public file list. `cv-review.json` binds visual review to the exact public PDFs. When regenerated PDFs differ, validation deliberately stops until the new pages have been rendered and inspected. Read every page; record their SHA-256 hashes only after visual review. This is not an automated visual-quality claim.

## Three common updates

1. **Add a paper.** Add one record to `profile.json.publications`, including full ordered authors, DOI, year, status and a verified paper link. Use its stable `id` in `projects` or `cv-variants.json` when selecting it. Keep an arXiv version marked `Preprint`. Regenerate, inspect the affected PDFs, then check. The academic CV selects all publications automatically; expand its layout if the content outgrows two pages rather than making the text unreadable.
2. **Tailor the internship résumé.** Edit the internship selection in `cv-variants.json`. Keep research facts in `profile.json`; select the relevant projects and publications rather than duplicating them. Regenerate and review the one-page output. Chinese translations must be revised when the corresponding English research content changes.
3. **Finish the visiting appointment.** Update the public identity, appointment dates, location and institutional email together in `profile.json`. Keep the long-term email as the main contact. Revise the relevant narrative and platform copy, regenerate the documents, and review the platform profiles separately.

`public-files.json` is an explicit allowlist for both the future repository and deployed `dist/` folder. A new file requires intentional review and a manifest update; the checker never silently adds files to this list. Private applications, credentials, identity documents and account-change records do not belong here.

## Portrait and research figures

`research-figures.json` records the selected P2 portrait (`assets/portrait.webp`) and three complete paper figures: Lift Fig. 1 (L3), SHSI Fig. 9 (S1), and LDGI Fig. 2 (D1). The research assets remain in `assets/research/`. Metadata includes original source links, figure numbers, alt text, intrinsic dimensions and SHA-256 hashes. The builder checks hashes and copies only these four reviewed images into `dist/`; the checker independently reads their WebP dimensions and verifies their hashes, HTML dimensions, alt text and full-size links. Figures are extracted from the original papers, not generated illustrations.

The 920px page pairs the name and contact block with the unchanged 144px portrait. The research statement introduces “Learning to sense the physical world.” Selected research uses a 220 × 160px contained thumbnail beside a 24px-separated text column. On narrow screens the figure is at most 260 × 170px, followed by text. All complete figure panels remain in the original files, with full-size links; no figure caption is added. The contribution, evidence, and Paper/Code links stay visible; author order is preserved in the full citation; the full citation can be expanded.

White, dark-gray text (#20252b), muted text (#5d6570), and blue links (#205b87) form the shared website/PDF palette. Web type uses system sans-serif fonts at 34/22/17/16px for name/section/paper/body, with 24px between research entries. PDF name/subtitle text-box spacing remains 9pt. Both academic CVs contain all nine works; all four CVs include Lift, SHSI, LDGI, and electro-optic materials/device experience. Generic skills and language lists are excluded from presentation. Tools appear only when tied to actual research work. Internship documents do not duplicate a separate publication list; each project links its paper and code.

The explicit `selected_project_ids` and CV selections prevent LDGI from disappearing through positional slicing. Actual generated PDF text is checked for all three selected works and electro-optic experience. Those checks do not assess design quality. Native PDF pages have been visually reviewed; local website browser pixel QA remains unverified.

## Publishing

The prepared GitHub Pages workflow checks pushes and pull requests. Only a successful main-branch run on a public repository can deploy, and only `dist/` is uploaded. It does not regenerate PDFs in CI. Choose GitHub Actions as the Pages build source when the owner approves initial publication.

The first public release and profile updates require the owner's review of the actual site, documents and public file list. This directory alone does not establish that a website is live.

Reference: [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

当前用词与顺序：四版CV将Education & Research Appointments置于Selected Research前；PhD student明确在读身份，不推断candidacy或博士起始日期。Lift重点为干净数据训练的比较中免成像分割更耐受测量噪声；LDGI为面向一维OPA线光场的算法。主页每篇研究只保留一条主介绍；材料器件为后置简短经历。三篇论文的Code随成果提供，Research Code仅列附加仓库，避免重复。
