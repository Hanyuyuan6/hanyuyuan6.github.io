#!/usr/bin/env python3
"""Build the public academic website from the shared, reviewed profile.

Requires Pillow for derived images. This builder writes index.html, a Lift
research page, 404.html, styles, scripts, robots.txt, sitemap.xml, icons, a social card, responsive previews
and the reviewed full-size images, preserving separately generated CVs.
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import date
from html import escape
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont


SOURCE_DIR = Path(__file__).resolve().parent
SITE_URL = "https://hanyuyuan6.github.io/"
TOKENS = re.compile(r"@@([A-Z_]+)@@")
THEME_KEYS = ("bg", "ink", "muted", "link", "hover", "line", "tint", "wash", "accent")
# The mark: a Y whose two arms carry light into one gold pixel, the single detector a
# single-pixel camera reads, and whose stem carries the signal out. Straight segments on
# the 32-unit grid, so the same polygons draw the SVG icon and the raster ICO/PNG; at
# 16 px the pixel is exactly 2×2 with a dark row above and below it.
MARK_SHAPES = (  # path, gradient start and end on the grid (None: flat), theme.json colour keys
    ("M5 6H10L14 10V12H11Z", (5, 6, 14, 12), ("light", "light_to")),
    ("M22 6H27L21 12H18V10Z", (27, 6, 18, 12), ("light", "light_to")),
    ("M14 20H18V26H14Z", (16, 20, 16, 26), ("signal", "signal_to")),
    ("M14 14H18V18H14Z", None, ("pixel", "pixel")),
)
MARK_KEYS = ("tile", "tile_to", "light", "light_to", "signal", "signal_to", "pixel")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def load_theme(source_dir: Path) -> dict[str, Any]:
    """theme.json is the one colour source for the site, icons, social card and CVs."""
    theme = json.loads((source_dir / "theme.json").read_text(encoding="utf-8"))
    palettes = theme.get("palettes", {})
    for name, palette in palettes.items():
        for key in THEME_KEYS:
            if not re.fullmatch(r"#[0-9a-f]{6}", str(palette.get(key, ""))):
                raise ValueError(f"Palette {name!r} needs a lowercase #rrggbb {key}")
    for role in ("selected", "dark"):
        if theme.get(role) is not None and theme[role] not in palettes:
            raise ValueError(f"theme.json {role} names an unknown palette: {theme[role]!r}")
    if any(not re.fullmatch(r"#[0-9a-f]{6}", str(theme.get("mark", {}).get(key, ""))) for key in MARK_KEYS):
        raise ValueError(f"theme.json mark needs lowercase #rrggbb {', '.join(MARK_KEYS)}")
    return theme


def theme_css(theme: dict[str, Any]) -> str:
    """Custom properties for the selected palette, plus the automatic dark one."""
    def block(selector: str, palette: dict[str, str], scheme: str, indent: str = "") -> str:
        body = "".join(f"{indent}  --{key}: {palette[key]};\n" for key in THEME_KEYS)
        return f"{indent}{selector} {{\n{indent}  color-scheme: {scheme};\n{body}{indent}}}\n"

    css = "/* Generated from theme.json by build_site.py. */\n"
    css += block(":root", theme["palettes"][theme["selected"]], "light")
    if theme.get("dark"):
        css += "@media (prefers-color-scheme: dark) {\n"
        css += block(":root", theme["palettes"][theme["dark"]], "dark", "  ") + "}\n"
    return css + "\n"


def theme_meta(theme: dict[str, Any]) -> str:
    light = theme["palettes"][theme["selected"]]["bg"]
    if not theme.get("dark"):
        return f'<meta name="color-scheme" content="light">\n  <meta name="theme-color" content="{light}">'
    dark = theme["palettes"][theme["dark"]]["bg"]
    return (
        '<meta name="color-scheme" content="light dark">\n'
        f'  <meta name="theme-color" content="{light}" media="(prefers-color-scheme: light)">\n'
        f'  <meta name="theme-color" content="{dark}" media="(prefers-color-scheme: dark)">'
    )


def e(value: Any) -> str:
    """Escape every source value before it reaches HTML text or attributes."""
    return escape(str(value), quote=True)


def sequence(profile: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = profile.get(key, [])
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{key} must be a list of objects")
    return value


def slug(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    if not normalized:
        raise ValueError("A public entry needs a non-empty identifier")
    return normalized


def url(value: Any, *, local: bool = False) -> str:
    """Accept web links or explicitly requested safe, relative CV paths."""
    value = str(value).strip()
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        if parsed.username or parsed.password:
            raise ValueError("Public URLs must not contain credentials")
        return value
    if local and not parsed.scheme and not parsed.netloc:
        path = PurePosixPath(parsed.path)
        if not path.is_absolute() and ".." not in path.parts and "\\" not in value:
            if path.parts and path.parts[0] == "cv" and path.suffix.lower() == ".pdf":
                return value
    raise ValueError(f"Unsupported public link: {value!r}")


def link(label: Any, href: Any, *, local: bool = False, css: str = "") -> str:
    class_attr = f' class="{e(css)}"' if css else ""
    return f'<a href="{e(url(href, local=local))}"{class_attr}>{e(label)}</a>'


def email_link(address: Any, *, css: str = "") -> str:
    address = str(address).strip()
    if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", address):
        raise ValueError("The public contact email is missing or invalid")
    class_attr = f' class="{e(css)}"' if css else ""
    return f'<a href="mailto:{e(address)}"{class_attr}>{e(address)}</a>'


def status(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return ""
    key = text.casefold()
    if key == "ongoing":
        return ""
    if "preprint" in key:
        css = "preprint"
    elif key in {"published", "publication"}:
        css = "published"
    elif "planned" in key:
        css = "planned"
    else:
        css = "ongoing"
    return f'<span class="status status-{css}">{e(text)}</span>'


def author_list(publication: dict[str, Any], person: dict[str, Any]) -> str:
    authors = publication.get("authors", [])
    if not isinstance(authors, list):
        raise ValueError("Publication authors must be a list")
    names = {str(person["name"]).casefold(), "yuyuan han", "han, yuyuan"}
    return ", ".join(
        f'<strong class="self-author">{e(name)}</strong>'
        if str(name).casefold() in names
        else e(name)
        for name in authors
    )


def is_preprint(publication: dict[str, Any]) -> bool:
    return str(publication.get("status", "")).casefold() == "preprint"


def venue_line(publication: dict[str, Any], *, full: bool = True) -> str:
    venue = str(publication.get("venue", "")).strip()
    label = f"<cite>{e(venue)}</cite>" + (" preprint" if is_preprint(publication) else "")
    parts = [label] if venue else []
    if full:
        volume = str(publication.get("volume", "") or "").strip()
        issue = str(publication.get("issue", "") or "").strip()
        if volume:
            parts.append(e(volume) + (f"({e(issue)})" if issue else ""))
        pages = str(publication.get("pages", "") or "").strip()
        if pages:
            parts.append(e(pages))
    year = str(publication.get("year", "") or "").strip()
    if year:
        parts.append(e(year))
    return " · ".join(parts)


def family_given(name: str) -> tuple[str, str]:
    parts = name.split()
    return parts[-1], " ".join(parts[:-1])


def bibtex(publication: dict[str, Any]) -> str:
    """A BibTeX record built only from the verified profile metadata."""
    authors = [family_given(str(name)) for name in publication["authors"]]
    stop = {"a", "an", "the", "on", "of", "for", "with", "how", "in"}
    first_word = next(w for w in re.findall(r"[A-Za-z]+", publication["title"]) if w.lower() not in stop)
    key = f"{authors[0][0].lower()}{publication['year']}{first_word.lower()}"

    def tex(value: Any) -> str:
        return str(value).replace("\\", "").replace("&", r"\&").replace("%", r"\%").replace("–", "--")

    fields = [("title", "{" + tex(publication["title"]) + "}"), ("author", " and ".join(f"{f}, {g}" for f, g in authors))]
    if is_preprint(publication):
        kind = "misc"
        fields += [("year", publication["year"]), ("eprint", publication.get("arxiv_id", "")), ("archivePrefix", "arXiv")]
    else:
        kind = "article"
        fields.append(("journal", tex(publication["venue"])))
        for name, value in (("volume", publication.get("volume")), ("number", publication.get("issue")), ("pages", publication.get("pages"))):
            if value:
                fields.append((name, tex(value)))
        fields.append(("year", publication["year"]))
    fields.append(("doi", publication["doi"]))
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value != "")
    return f"@{kind}{{{key},\n{body}\n}}"


def publication_links(publication: dict[str, Any], project: dict[str, Any] | None = None) -> str:
    links = []
    paper_url = publication.get("url")
    doi = str(publication.get("doi", "") or "").strip()
    if paper_url:
        links.append(f'<a href="{e(url(paper_url))}" aria-label="Paper: {e(publication["title"])}">Paper</a>')
    elif doi:
        links.append(f'<a href="https://doi.org/{e(doi)}" aria-label="Paper: {e(publication["title"])}">Paper</a>')
    code_url = (project or {}).get("code_url") or publication.get("code_url")
    if code_url:
        links.append(f'<a href="{e(url(code_url))}" aria-label="Code: {e(publication["title"])}">Code</a>')
    links.append(
        f'<details class="bibtex"><summary aria-label="BibTeX for {e(publication["title"])}">BibTeX</summary>'
        f'<pre>{e(bibtex(publication))}</pre></details>'
    )
    return '<div class="resource-links">' + "".join(links) + "</div>"


def reviewed_asset(record: dict[str, Any], source_dir: Path, *, portrait: bool = False) -> PurePosixPath:
    """Only copy the reviewed local image, with explicit intrinsic dimensions."""
    path = PurePosixPath(str(record.get("path", "")))
    valid_path = path == PurePosixPath("assets/portrait.webp") if portrait else (
        len(path.parts) == 3
        and path.parts[:2] == ("assets", "research")
        and path.suffix == ".webp"
    )
    if not valid_path or path.is_absolute() or ".." in path.parts or "\\" in str(path):
        raise ValueError("Reviewed images must use the approved local WebP paths")
    if any(type(record.get(key)) is not int or record[key] <= 0 for key in ("width", "height")):
        raise ValueError("Reviewed images need positive integer intrinsic dimensions")
    if not isinstance(record.get("alt"), str) or not record["alt"].strip():
        raise ValueError("Reviewed images need meaningful alt text")
    digest = str(record.get("sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Reviewed images need a SHA-256 hash")
    source = source_dir / path
    if source.is_symlink() or not source.is_file():
        raise ValueError("A reviewed image is missing or is a symlink")
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError("An image differs from its reviewed source")
    return path


def save_webp(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "WEBP", quality=82, method=6)


def derive_images(figure_records: dict[str, Any], source_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Responsive previews of the whole reviewed originals (never cropped or edited)."""
    derived: dict[str, Any] = {"figures": {}, "portrait": []}
    for record in figure_records["figures"]:
        with Image.open(source_dir / record["path"]) as original:
            whole = original.convert("RGB")
        variants = []
        for width in sorted({min(w, whole.width) for w in figure_records["preview_widths"]}):
            height = round(whole.height * width / whole.width)
            relative = PurePosixPath(f"assets/research/{record['id']}-{width}.webp")
            save_webp(whole.resize((width, height), Image.LANCZOS), output_dir / relative)
            variants.append({"path": str(relative), "width": width, "height": height})
        derived["figures"][record["id"]] = variants
    portrait = figure_records["portrait"]
    with Image.open(source_dir / portrait["path"]) as original:
        image = original.convert("RGB")
        for width in portrait["widths"]:
            relative = PurePosixPath(f"assets/portrait-{width}.webp")
            save_webp(image.resize((width, width), Image.LANCZOS), output_dir / relative)
            derived["portrait"].append({"path": str(relative), "width": width, "height": width})
    return derived


def figure_number(figure: dict[str, Any]) -> str:
    """'Fig. 1 (a–i)' -> 'Fig. 1': the preview shows every panel, so the caption names only the figure."""
    match = re.match(r"Fig\. \d+", figure["figure"])
    if match is None:
        raise ValueError(f"Figure label must start with 'Fig. N': {figure['figure']}")
    return match.group(0)


def srcset(variants: list[dict[str, Any]]) -> str:
    return ", ".join(f'{e(v["path"])} {v["width"]}w' for v in variants)


def path_polygons(path: str) -> list[list[tuple[float, float]]]:
    """Parse absolute M/L/H/V/Z path commands into point lists."""
    polygons: list[list[tuple[float, float]]] = []
    x = y = 0.0
    for command, args in re.findall(r"([MLHVZ])([^MLHVZ]*)", path):
        values = [float(v) for v in args.split()]
        if command == "M":
            polygons.append([])
        if command in "ML":
            for x, y in zip(values[0::2], values[1::2]):
                polygons[-1].append((x, y))
        elif command == "H":
            for x in values:
                polygons[-1].append((x, y))
        elif command == "V":
            for y in values:
                polygons[-1].append((x, y))
    return polygons


def mark_icon(size: int, mark: dict[str, str], *, radius: float = 7) -> Image.Image:
    """Draw at 512 px and downsample, so small sizes stay crisp and anti-aliased."""
    scale, full = 512 / 32, 512
    image = Image.new("RGBA", (full, full), (0, 0, 0, 0))
    start, end = (tuple(int(mark[key][i:i + 2], 16) for i in (1, 3, 5)) for key in ("tile", "tile_to"))
    # Same geometry as the SVG's objectBoundingBox gradient on the tile:
    # t = (u + v) / 2, from the top-left to the bottom-right corner.
    tile = Image.new("RGB", (full, full))
    tile.putdata([tuple(round(a + (b - a) * (x + y) / (2 * (full - 1))) for a, b in zip(start, end)) for y in range(full) for x in range(full)])
    shape = Image.new("L", (full, full), 0)
    ImageDraw.Draw(shape).rounded_rectangle((0, 0, full - 1, full - 1), radius=radius * scale, fill=255)
    image.paste(tile, (0, 0), shape)
    for path, line, (key_from, key_to) in MARK_SHAPES:
        mask = Image.new("L", (full, full), 0)
        for polygon in path_polygons(path):
            ImageDraw.Draw(mask).polygon([(px * scale, py * scale) for px, py in polygon], fill=255)
        image.paste(linear_fill(full, scale, line, mark[key_from], mark[key_to]), (0, 0), mask)
    # Area averaging keeps the 16 px pixel a clean 2×2 square; Lanczos rings around small shapes.
    return image.resize((size, size), Image.BOX)


def linear_fill(full: int, scale: float, line: tuple[float, ...] | None, colour_from: str, colour_to: str) -> Image.Image:
    """A full-size fill matching an SVG userSpaceOnUse linear gradient (flat when line is None)."""
    if line is None:
        return Image.new("RGB", (full, full), colour_from)
    a, b = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (colour_from, colour_to))
    x1, y1, x2, y2 = (v * scale for v in line)
    dx, dy = x2 - x1, y2 - y1
    norm = dx * dx + dy * dy
    fill = Image.new("RGB", (full, full))
    fill.putdata([tuple(round(p + (q - p) * min(1.0, max(0.0, ((x + 0.5 - x1) * dx + (y + 0.5 - y1) * dy) / norm))) for p, q in zip(a, b))
                  for y in range(full) for x in range(full)])
    return fill


def mark_svg(mark: dict[str, str]) -> str:
    gradients, shapes = [], []
    for index, (path, line, (key_from, key_to)) in enumerate(MARK_SHAPES):
        if line is None:
            shapes.append(f'<path fill="{mark[key_from]}" d="{path}"/>')
            continue
        x1, y1, x2, y2 = line
        gradients.append(f'<linearGradient id="g{index}" gradientUnits="userSpaceOnUse" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">'
                         f'<stop offset="0" stop-color="{mark[key_from]}"/><stop offset="1" stop-color="{mark[key_to]}"/></linearGradient>')
        shapes.append(f'<path fill="url(#g{index})" d="{path}"/>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        f'<defs><linearGradient id="m" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{mark["tile"]}"/>'
        f'<stop offset="1" stop-color="{mark["tile_to"]}"/></linearGradient>{"".join(gradients)}</defs>'
        f'<rect width="32" height="32" rx="7" fill="url(#m)"/>{"".join(shapes)}</svg>\n'
    )


def icon_version(mark: dict[str, str]) -> str:
    """Browsers cache icons by URL for a long time; a new mark gets a new URL."""
    return hashlib.sha256(mark_svg(mark).encode("utf-8")).hexdigest()[:10]


def icon_links(prefix: str, version: str) -> str:
    """The icon set every public page declares: SVG, ICO for older browsers, iOS touch icon."""
    return (f'<link rel="icon" href="{prefix}favicon.svg?v={version}" type="image/svg+xml">'
            f'<link rel="icon" href="{prefix}favicon.ico?v={version}" sizes="48x48">'
            f'<link rel="apple-touch-icon" href="{prefix}apple-touch-icon.png?v={version}">')


def write_icons(output_dir: Path, mark: dict[str, str]) -> None:
    """The Y-and-pixel mark: legible at 16 px, unlike the old 4×4 pattern."""
    (output_dir / "favicon.svg").write_text(mark_svg(mark), encoding="utf-8", newline="\n")
    # Each ICO size is drawn on its own, so the ICO writer never rescales a frame.
    frames = [mark_icon(size, mark) for size in (16, 32, 48)]
    frames[-1].save(output_dir / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=frames[:-1])
    # iOS masks its own corners, so the touch icon fills the square.
    mark_icon(180, mark, radius=0).convert("RGB").save(output_dir / "apple-touch-icon.png", optimize=True)


def social_card(profile: dict[str, Any], portrait_path: Path, source_dir: Path, output_dir: Path, palette: dict[str, str]) -> None:
    """A 1200×630 link-preview card (og:image) in the site's own type and colours."""
    person, narrative = profile["person"], profile["narrative"]
    fonts = source_dir / "fonts"
    bold = ImageFont.truetype(str(fonts / "NotoSans-Bold.ttf"), 64)
    regular = ImageFont.truetype(str(fonts / "NotoSans-Regular.ttf"), 28)
    small = ImageFont.truetype(str(fonts / "NotoSans-Regular.ttf"), 24)
    card = Image.new("RGB", (1200, 630), palette["bg"])
    # Round portrait inside a thin accent ring, drawn at 4x and reduced for clean edges.
    photo, ring, scale = 300, 4, 4
    with Image.open(portrait_path) as portrait:
        face = portrait.convert("RGB").resize((photo * scale, photo * scale), Image.LANCZOS)
    disc = Image.new("L", (photo * scale, photo * scale), 0)
    ImageDraw.Draw(disc).ellipse((0, 0, photo * scale - 1, photo * scale - 1), fill=255)
    halo = Image.new("L", ((photo + 2 * ring) * scale,) * 2, 0)
    ImageDraw.Draw(halo).ellipse((0, 0, (photo + 2 * ring) * scale - 1, (photo + 2 * ring) * scale - 1), fill=255)
    card.paste(Image.new("RGB", ((photo + 2 * ring),) * 2, palette["accent"]), (84 - ring, 165 - ring), halo.resize(((photo + 2 * ring),) * 2, Image.BOX))
    card.paste(face.resize((photo, photo), Image.BOX), (84, 165), disc.resize((photo, photo), Image.BOX))
    draw = ImageDraw.Draw(card)
    x, right = 432, 1116
    # The field line takes the largest size (28 px down to 22 px) that fits the margin.
    for size in range(28, 21, -1):
        regular = ImageFont.truetype(str(fonts / "NotoSans-Regular.ttf"), size)
        if x + draw.textlength(narrative["field"], font=regular) <= right:
            break
    # One identity line (owner, 2026-10-12: the PhD is enough); the block stays centred on the photo.
    lines = [
        (person["name"], bold, palette["ink"], 190),
        (narrative["field"], regular, palette["link"], 284),
        (f'{person["position"]} · {person["institution"]}', small, palette["muted"], 340),
        (person["homepage"].replace("https://", "").rstrip("/"), small, palette["link"], 408),
    ]
    for text, font, colour, y in lines:
        if x + draw.textlength(text, font=font) > right:
            raise ValueError(f"Social card text overflows its 84 px margin: {text!r}")
        draw.text((x, y), text, font=font, fill=colour)
    draw.line((x, 394, x + 96, 394), fill=palette["accent"], width=2)
    card.save(output_dir / "social-card.png", optimize=True)


def portrait_image(record: dict[str, Any], variants: list[dict[str, Any]], name: str) -> str:
    default = next(v for v in variants if v["width"] == 320)
    return (
        '<figure class="portrait">'
        f'<a class="portrait-link" href="{e(record["path"])}" '
        f'aria-label="Open full-size portrait of {e(name)}">'
        f'<img src="{e(default["path"])}" srcset="{srcset(variants)}" '
        'sizes="(max-width: 650px) 88px, 120px" '
        f'alt="{e(record["alt"])}" width="{default["width"]}" height="{default["height"]}" '
        'loading="eager" fetchpriority="high" decoding="async"></a></figure>'
    )


def highlights(project: dict[str, Any]) -> str:
    """Keep the reviewed results together in the optional evidence layer."""
    items = [str(item).strip() for item in project.get("highlights", []) if str(item).strip()]
    if not items:
        return ""
    return '<ul class="highlights" aria-label="Key results">' + "".join(f"<li>{e(item)}</li>" for item in items) + "</ul>"


def contribution_details(project: dict[str, Any], publication: dict[str, Any], person: dict[str, Any]) -> str:
    """One disclosure label and one evidence layer for every selected study."""
    points = project.get("detail_points", project.get("bullets", []))
    if not isinstance(points, list) or any(not isinstance(point, str) for point in points):
        raise ValueError("Selected research detail_points must be a list of strings")
    items = "".join(f'<li>{e(point)}</li>' for point in points if point.strip())
    conditions = project.get("conditions", [])
    if isinstance(conditions, str):
        conditions = [conditions]
    if not isinstance(conditions, list) or any(not isinstance(item, str) for item in conditions):
        raise ValueError("Selected research conditions must be text or a list of strings")
    evidence = (f'<ul class="contribution-list">{items}</ul>' if items else "")
    evidence += highlights(project)
    evidence += "".join(f'<p class="evidence-condition">{e(item)}</p>' for item in conditions if item.strip())
    return (
        '<details class="work-details"><summary>Contribution &amp; evidence</summary>'
        f'<div class="work-detail-content">{evidence}'
        f'<p class="authors">{author_list(publication, person)}</p></div></details>'
    )


def research_questions(projects: list[dict[str, Any]]) -> str:
    """Native links remain useful without JavaScript; emphasis adds orientation."""
    topics = (
        ("design", "Measurement design", "How should light be measured?", ("shsi",), "Sparse Hadamard"),
        ("representation", "Representation", "How do measurements become task features?", ("lift",), "The Lift Spectrum"),
        ("inference", "Inference paths", "Which computation fits the task?", ("lift", "loop"), "Lift · LDGI"),
    )
    present = {str(project["id"]) for project in projects}
    if not {"lift", "shsi", "loop"}.issubset(present):
        return ""
    links = "".join(
        f'<a href="#research-{works[0]}" data-research-question="{key}" data-related-works="{" ".join(works)}">'
        f'<strong>{label}</strong><span>{question}</span><small>{titles}</small></a>'
        for key, label, question, works, titles in topics
    )
    return f'<nav class="question-map" aria-label="Explore research by question">{links}</nav>'


def selected_research(
    projects: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    person: dict[str, Any],
    figures: dict[str, dict[str, Any]],
    previews: dict[str, list[dict[str, Any]]],
) -> tuple[str, set[str]]:
    by_id = {str(publication["id"]): publication for publication in publications}
    selected_ids: set[str] = set()
    entries = []
    for index, project in enumerate(projects, start=1):
        publication_id = str(project.get("publication_id", ""))
        publication = by_id.get(publication_id)
        if publication is None:
            raise ValueError(f"Selected research references an unknown publication: {publication_id}")
        if publication_id in selected_ids:
            raise ValueError(f"A publication was selected twice: {publication_id}")
        selected_ids.add(publication_id)
        title = publication["title"]
        entry_id = slug(project.get("id", f"research-{index}"))
        figure = figures[publication_id]
        variants = previews[publication_id]
        default = next(v for v in variants if v["width"] == 640)
        aspect = figure["width"] / figure["height"]
        figure_html = (
            '<figure class="research-figure">'
            f'<a class="figure-link" href="{e(figure["path"])}" '
            f'aria-label="Open {e(figure["figure"])} of {e(title)} at full size">'
            f'<img src="{e(default["path"])}" srcset="{srcset(variants)}" '
            'sizes="(max-width: 650px) 260px, 210px" '
            f'alt="{e(figure["alt"])}" width="{default["width"]}" height="{default["height"]}" '
            f'loading="{"eager" if index == 1 else "lazy"}" decoding="async"></a></figure>'
        )
        resources = publication_links(publication, project)
        context = str(project.get("context", "") or "").strip()
        context_html = f'<p class="work-context">{e(context)}</p>' if context else ""
        if project["id"] == "lift":
            resources = resources.replace(
                '</div>', '<a class="research-detail-link" href="research/lift.html">Explore the study</a></div>', 1
            )
        entries.append(
            f'<article class="research-entry" id="research-{e(entry_id)}" style="--ar: {aspect:.4f}">'
            f'{figure_html}<div class="work-body">'
            f'<h3 class="work-finding">{e(str(project.get("summary", "")).strip())}</h3>'
            f'<p class="paper-title">{link(title, publication["url"])}</p>'
            f'<p class="work-meta">{venue_line(publication, full=False)}</p>'
            f'{context_html}'
            f'{resources}{contribution_details(project, publication, person)}</div></article>'
        )
    return "\n".join(entries), selected_ids


def all_publications(publications: list[dict[str, Any]], selected_ids: set[str], person: dict[str, Any]) -> str:
    """The complete record, newest first; selected works stay in it (marked), not removed."""
    entries = []
    for publication in publications:
        selected = str(publication["id"]) in selected_ids
        entries.append(
            f'<li class="publication publication-entry{" is-selected" if selected else ""}" '
            f'id="publication-{e(slug(publication["id"]))}" data-publication-id="{e(publication["id"])}">'
            f'<h3>{link(publication["title"], publication["url"])}</h3>'
            f'<p class="authors">{author_list(publication, person)}</p>'
            f'<p class="venue">{venue_line(publication)}</p>'
            f'{publication_links(publication)}</li>'
        )
    return "\n".join(entries)


def news(rows: list[dict[str, Any]]) -> str:
    entries, previous = [], None
    for row in rows:
        match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", str(row.get("date", "")))
        if not match:
            raise ValueError(f"News dates must be YYYY-MM: {row.get('date')!r}")
        if previous and row["date"] > previous:
            raise ValueError("News must be listed newest first")
        previous = row["date"]
        text, target = str(row["text"]), row.get("link_text")
        if row.get("url") and target and target in text:
            before, after = text.split(target, 1)
            body = e(before) + link(target, row["url"]) + e(after)
        else:
            body = e(text)
        label = f'{MONTHS[int(match[2]) - 1]} {match[1]}'
        entries.append(f'<li><time datetime="{e(row["date"])}">{e(label)}</time><p>{body}</p></li>')
    return "\n".join(entries)


def current_directions(directions: list[dict[str, Any]]) -> str:
    return "\n".join(
        '<li class="direction-entry"><div class="direction-heading">'
        f'<h3>{e(direction.get("title", ""))}</h3>{status(direction.get("status", ""))}</div>'
        f'<p>{e(direction.get("description", ""))}</p></li>'
        for direction in directions
    )


def mentor_link(name: str, url: str = "") -> str:
    if not url:
        return e(name)
    return (
        f'<a class="mentor-link" href="{e(url)}" target="_blank" rel="noopener noreferrer" '
        f'aria-label="{e(name)} — official homepage (opens in a new tab)">'
        f'{e(name)}<span aria-hidden="true">↗</span></a>'
    )


def education(rows: list[dict[str, Any]], visits: list[dict[str, Any]]) -> str:
    """Degrees, newest first; a visiting period follows the PhD it belongs to, as on the CVs."""
    entries = []
    for index, row in enumerate(rows):
        field = str(row.get("field", "") or "").strip()
        degree_line = e(row.get("degree", "")) + (f" · {e(field)}" if field else "")
        advisor = str(row.get("advisor", "") or "").strip()
        dates = str(row.get("display_dates", "") or "").strip()
        entries.append(
            '<li><h4>' + e(row.get("institution", "")) + "</h4>"
            f'<p>{degree_line}</p>'
            + (f'<p class="entry-note">Advisor: {mentor_link(advisor, row.get("advisor_url", ""))}</p>' if advisor else "")
            + (f'<p class="entry-dates">{e(dates)}</p>' if dates else "")
            + "</li>"
        )
        if index == 0 and visits:
            entries.append(appointments(visits))
    return "\n".join(entries)


def appointments(rows: list[dict[str, Any]]) -> str:
    entries = []
    for row in rows:
        department = str(row.get("department", "") or "").strip()
        host = str(row.get("host", "") or "").strip()
        dates = str(row.get("display_dates", "") or "").strip()
        entries.append(
            f'<li><h4>{e(row.get("institution", ""))}</h4>'
            f'<p>{e(row.get("role", ""))}</p>'
            + (f'<p class="entry-note">{e(department)}</p>' if department else "")
            + (f'<p class="entry-note">Supervisor: {mentor_link(host, row.get("host_url", ""))}</p>' if host else "")
            + (f'<p class="entry-dates">{e(dates)}</p>' if dates else "")
            + "</li>"
        )
    return "\n".join(entries)


def software(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        '<li class="software-entry">'
        f'<h3>{link(row.get("name", ""), row["url"])}</h3>'
        f'<p>{e(row.get("description", ""))}</p></li>'
        for row in rows
    )


def profile_links(rows: list[dict[str, Any]], *, primary: bool = False) -> str:
    if primary:
        rows = [row for row in rows if str(row.get("name", "")).casefold() in {
            "google scholar", "scholar", "orcid", "github"
        }]
    return "\n".join(f'<li>{link(row.get("name", ""), row["url"])}</li>' for row in rows)


def cv_links(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f'<span class="cv-option"><a class="cv-link" href="{e(url(row["path"], local=True))}">'
        f'{e(row.get("label", "CV"))}<span class="file-type">PDF</span></a>'
        f'<a class="cv-online" href="{e(str(PurePosixPath(url(row["path"], local=True)).with_suffix(".html")))}" '
        f'aria-label="Read {e(row.get("label", "CV"))} online">Read online</a></span>'
        for row in rows
    )


def awards(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    items = "".join(
        f'<li><span>{e(row.get("title", ""))}</span>'
        f'<span class="entry-dates">{e(row.get("year", ""))}</span>'
        + (f' {link("Code", row["code_url"])}' if row.get("code_url") else "")
        + "</li>"
        for row in rows
    )
    return f'<div class="awards-block"><h3>Award</h3><ul>{items}</ul></div>'


def service(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    items = "".join(
        f'<li><span>{e(row["role"])} for {e(", ".join(row["venues"][:-1]))} and {e(row["venues"][-1])}</span>'
        f'<span class="entry-dates">{e(row["count"])} reviews · {e(row["years"])}</span></li>'
        for row in rows
    )
    return f'<div class="awards-block"><h3>Service</h3><ul>{items}</ul></div>'


def write_support_files(profile: dict[str, Any], output_dir: Path, style_version: str, icon_version: str) -> None:
    updated = str(profile.get("updated", ""))
    pages = ["", "research/lift.html", *(row["path"] for row in sequence(profile, "cvs"))]
    urls = "".join(f"  <url><loc>{e(SITE_URL + path)}</loc><lastmod>{e(updated)}</lastmod></url>\n" for path in pages)
    (output_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n",
        encoding="utf-8", newline="\n",
    )
    (output_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8", newline="\n")
    name = e(profile["person"]["name"])
    (output_dir / "404.html").write_text(
        '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '  <meta name="robots" content="noindex">\n'
        f'  <title>Page not found · {name}</title>\n'
        f'  {icon_links("/", icon_version)}\n'
        f'  <link rel="stylesheet" href="/style.css?v={style_version}">\n</head>\n<body>\n'
        '  <main class="page-shell not-found">\n    <h1>Page not found</h1>\n'
        f'    <p>This page does not exist. Go to <a href="/">{name}’s homepage</a>.</p>\n'
        '  </main>\n</body>\n</html>\n',
        encoding="utf-8", newline="\n",
    )


def condition_table(rows: list[dict[str, Any]]) -> str:
    """Six reported margins on one fixed scale; filtering never recalculates a value."""
    if len(rows) != 3 or len({str(row.get("name", "")) for row in rows}) != 3:
        raise ValueError("The Lift comparison requires three distinct reported datasets")
    groups = []
    for row in rows:
        name = str(row.get("name", "")).strip()
        if not name:
            raise ValueError("The Lift comparison needs dataset names")
        results = []
        for key, label in (("clean", "Clean"), ("noisy", "20 dB")):
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not -25 <= value <= 25:
                raise ValueError("Lift comparison values must be finite reported margins within the fixed −25…+25 scale")
            number = ("−" if value < 0 else "+") + f"{abs(value):.1f}"
            left = 50 + min(value, 0) * 2
            width = abs(value) * 2
            css = "reconstruction" if value < 0 else "image-free"
            winner = "Reconstruction-first" if value < 0 else "Image-free"
            results.append(
                f'<tr class="result-row" data-condition="{key}" data-value="{value}">'
                f'<th scope="row" class="test-condition">{label}</th>'
                '<td class="plot-cell" aria-hidden="true"><span class="plot-zero"></span>'
                f'<span class="bar {css}" style="left:{left:.1f}%;width:{width:.1f}%"></span></td>'
                f'<td class="number {css}"><span aria-hidden="true">{number}</span>'
                f'<span class="sr-only">{number} percentage points. {winner} is higher.</span></td></tr>'
            )
        groups.append(
            f'<tbody data-dataset="{e(name)}"><tr class="dataset-heading">'
            f'<th colspan="3" scope="rowgroup">{e(name)}</th></tr>{"".join(results)}</tbody>'
        )
    return (
        '<table class="comparison-table" aria-label="Reported foreground mIoU differences, image-free minus reconstruction-first, in percentage points">'
        '<colgroup><col class="condition-column"><col><col class="value-column"></colgroup>'
        '<thead class="sr-only"><tr><th scope="col">Dataset and test condition</th>'
        '<th scope="col">Fixed scale from minus 25 to plus 25</th><th scope="col">Difference, percentage points</th></tr></thead>'
        f'{"".join(groups)}</table>'
    )


def synthetic_wave_path() -> str:
    """A labelled teaching input for the pathway explanation, never an experiment."""
    values = [0.51 + 0.21 * math.sin(i * 0.33) + 0.10 * math.cos(i * 0.91) for i in range(64)]
    return " ".join(f'{"M" if i == 0 else "L"}{8 + i * 704 / 63:.2f} {92 - value * 73:.2f}' for i, value in enumerate(values))


def write_lift_page(
    profile: dict[str, Any], source_dir: Path, output_dir: Path,
    figures: dict[str, dict[str, Any]], common: dict[str, str],
) -> None:
    """The approved evidence comparison, path explanation and original-figure guide."""
    project = next(row for row in sequence(profile, "projects") if row["id"] == "lift")
    publication = next(row for row in sequence(profile, "publications") if row["id"] == project["publication_id"])
    comparison = project.get("condition_comparison")
    if not isinstance(comparison, dict):
        raise ValueError("The Lift detail page needs projects[lift].condition_comparison in profile.json")
    rows = comparison.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("The Lift condition comparison rows must be a list of objects")
    conditions = comparison.get("conditions", [])
    if not isinstance(conditions, list) or not conditions or any(not isinstance(item, str) for item in conditions):
        raise ValueError("The Lift condition comparison requires its reviewed comparison conditions")
    figure = figures[project["publication_id"]]
    script = (source_dir / "research" / "lift.js").read_text(encoding="utf-8")
    points = project.get("detail_points", project.get("bullets", []))
    if not isinstance(points, list) or any(not isinstance(point, str) for point in points):
        raise ValueError("Lift detail_points must be a list of strings")
    point_list = '<ul class="contribution-list">' + "".join(f'<li>{e(point)}</li>' for point in points if point.strip()) + '</ul>'
    replacements = {
        **common,
        "LIFT_SHORT_TITLE": e(publication["title"].split(":", 1)[0]),
        "LIFT_TITLE": e(publication["title"]),
        "LIFT_SUMMARY": e(project["summary"]),
        "LIFT_CONTEXT": e(project.get("context", "")),
        "LIFT_VENUE": venue_line(publication, full=False),
        "LIFT_AUTHORS": author_list(publication, profile["person"]),
        "LIFT_PAPER_URL": e(url(publication["url"])),
        "LIFT_CODE_URL": e(url(project["code_url"])),
        "LIFT_CONTRIBUTIONS": point_list,
        "LIFT_FIGURE": e("../" + str(figure["path"])),
        "LIFT_FIGURE_ALT": e(figure["alt"]),
        "LIFT_FIGURE_WIDTH": e(figure["width"]),
        "LIFT_FIGURE_HEIGHT": e(figure["height"]),
        "CONDITION_TABLE": condition_table(rows),
        "DATASET_OPTIONS": "".join(
            f'<label class="choice"><input type="radio" name="dataset" value="{e(row["name"])}"><span>{e(row["name"])}</span></label>'
            for row in rows
        ),
        "EVIDENCE_SOURCE": e(url(comparison.get("source_url", ""))),
        "COMPARISON_CONDITIONS": "".join(f'<p>{e(item)}</p>' for item in conditions),
        "SYNTHETIC_WAVE": synthetic_wave_path(),
        "LIFT_SCRIPT_VERSION": hashlib.sha256(script.encode("utf-8")).hexdigest()[:10],
    }
    template = (source_dir / "research" / "lift-template.html").read_text(encoding="utf-8")
    missing = set(TOKENS.findall(template)) - replacements.keys()
    if missing:
        raise ValueError(f"Unknown Lift-page tokens: {sorted(missing)}")
    document = TOKENS.sub(lambda match: replacements[match.group(1)], template)
    if TOKENS.search(document):
        raise ValueError("The Lift page contains an unresolved template token")
    destination = output_dir / "research"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "lift.html").write_text(document, encoding="utf-8", newline="\n")
    (destination / "lift.js").write_text(script, encoding="utf-8", newline="\n")


def build(profile: dict[str, Any], output_dir: Path, *, source_dir: Path | None = None) -> Path:
    """Build the public site while leaving CV output and unrelated assets alone."""
    source_dir = Path(source_dir) if source_dir else SOURCE_DIR
    output_dir = Path(output_dir)
    person = profile.get("person", {})
    narrative = profile.get("narrative", {})
    if not isinstance(person, dict) or not person.get("name"):
        raise ValueError("person.name is required")
    if not isinstance(narrative, dict) or not narrative.get("hero") or not narrative.get("bio"):
        raise ValueError("narrative.hero and narrative.bio are required")
    description = str(narrative.get("meta_description", "")).strip()
    if not 50 <= len(description) <= 160:
        raise ValueError("narrative.meta_description must be 50-160 characters for search snippets")
    project_map = {p["id"]: p for p in sequence(profile, "projects")}
    projects = [project_map[pid] for pid in profile["selected_project_ids"]]
    publications = sorted(
        sequence(profile, "publications"),
        key=lambda publication: int(publication.get("year", 0) or 0),
        reverse=True,
    )
    profiles = sequence(profile, "profiles")
    cvs = sequence(profile, "cvs")
    ids = [str(publication["id"]) for publication in publications]
    if len(ids) != len(set(ids)):
        raise ValueError("Publication identifiers must be unique")
    theme = load_theme(source_dir)
    palette = theme["palettes"][theme["selected"]]
    stylesheet = theme_css(theme) + (source_dir / "style.css").read_text(encoding="utf-8")
    style_version = hashlib.sha256(stylesheet.encode("utf-8")).hexdigest()[:10]
    site_script = (source_dir / "site.js").read_text(encoding="utf-8")
    figure_records = json.loads((source_dir / "research-figures.json").read_text(encoding="utf-8"))
    figures = {record["id"]: record for record in figure_records["figures"]}
    if len(figures) != len(figure_records["figures"]):
        raise ValueError("Duplicate research figure IDs")
    if set(figures) != {project["publication_id"] for project in projects}:
        raise ValueError("Each selected paper must have exactly one reviewed figure")
    portrait = figure_records.get("portrait")
    if not isinstance(portrait, dict):
        raise ValueError("The reviewed portrait metadata is required")
    assets = [reviewed_asset(portrait, source_dir, portrait=True)]
    assets += [reviewed_asset(figures[project["publication_id"]], source_dir) for project in projects]
    output_dir.mkdir(parents=True, exist_ok=True)
    derived = derive_images(figure_records, source_dir, output_dir)
    selected, selected_ids = selected_research(projects, publications, person, figures, derived["figures"])
    roles = [str(person.get("position", "") or "").strip(), str(person.get("institution", "") or "").strip()]
    institution = "".join(f"<p>{e(role)}</p>" for role in roles if role)
    name_zh = str(person.get("name_zh", "") or "").strip()
    direction = str(narrative.get("long_term") or narrative.get("direction", "") or "").strip()
    welcome = str(narrative.get("welcome", "") or "").strip()
    updated = date.fromisoformat(str(profile.get("updated", "2026-10-11"))).strftime("%B %Y")
    title = f'{person["name"]} · {narrative["field"]}'
    structured = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": person["name"],
        "alternateName": name_zh,
        "url": SITE_URL,
        "image": SITE_URL + "assets/portrait-480.webp",
        "jobTitle": person.get("position", ""),
        "email": str(person.get("email", "")),
        "affiliation": [{"@type": "CollegeOrUniversity", "name": person.get("institution", "")}]
        + [{"@type": "CollegeOrUniversity", "name": row["institution"]} for row in sequence(profile, "appointments")],
        "knowsAbout": profile.get("platform_copy", {}).get("keywords", []),
        "sameAs": [url(row["url"]) for row in profiles],
    }
    structured_json = json.dumps(structured, ensure_ascii=False, indent=2)
    structured_json = structured_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    replacements = {
        "TITLE": e(title),
        "DESCRIPTION": e(description),
        "SOCIAL_ALT": e(f'{person["name"]}: {narrative.get("field", "")}'),
        "STRUCTURED_DATA": structured_json,
        "NAME": e(person["name"]),
        "CHINESE_NAME": f'<span lang="zh-Hans" class="chinese-name">{e(name_zh)}</span>' if name_zh else "",
        "PORTRAIT": portrait_image(portrait, derived["portrait"], str(person["name"])),
        "IDENTITY": institution,
        "FIELD": e(narrative["field"]),
        "HERO": e(narrative["hero"]),
        "BIO": e(narrative["bio"]),
        "RESEARCH_BACKGROUND": "".join(f'<p><strong>{e(row["title"])}</strong> · {e(row["summary"])}</p>' for row in sequence(profile, "research_background")),
        "WELCOME": f'<p class="welcome">{e(welcome)}</p>' if welcome else "",
        "PERSONAL": f'<p class="personal">{e(narrative["personal"])}</p>' if narrative.get("personal") else "",
        "CV_LINKS": cv_links(cvs[:2]),
        "EMAIL": email_link(person.get("email", "")),
        "PRIMARY_PROFILES": profile_links(profiles, primary=True),
        "NEWS": news(sequence(profile, "news")),
        "SELECTED_RESEARCH": selected,
        "RESEARCH_QUESTIONS": research_questions(projects),
        "DIRECTIONS": current_directions(sequence(profile, "directions")),
        "DIRECTION_PARAGRAPH": f'<p class="future-paragraph">{e(direction)}</p>' if direction else "",
        "PUBLICATIONS": all_publications(publications, selected_ids, person),
        "PUBLICATION_COUNT": e(len(publications)),
        "STYLE_VERSION": style_version,
        "SITE_SCRIPT_VERSION": hashlib.sha256(site_script.encode("utf-8")).hexdigest()[:10],
        "ICON_VERSION": icon_version(theme["mark"]),
        "THEME_META": theme_meta(theme),
        "EDUCATION": education(sequence(profile, "education"), sequence(profile, "appointments")),
        "AWARDS": awards(sequence(profile, "awards")) + service(sequence(profile, "service")),
        "SOFTWARE": software([row for row in sequence(profile, "software") if row["name"] in profile["additional_code_ids"]]),
        "PROFILES": profile_links(profiles),
        "UPDATED": e(updated),
    }
    template = (source_dir / "template.html").read_text(encoding="utf-8")
    needed = set(TOKENS.findall(template))
    missing = needed - replacements.keys()
    if missing:
        raise ValueError(f"Unknown template tokens: {sorted(missing)}")
    document = TOKENS.sub(lambda match: replacements[match.group(1)], template)
    if TOKENS.search(document):
        raise ValueError("The generated page contains an unresolved template token")
    destination = output_dir / "index.html"
    destination.write_text(document, encoding="utf-8", newline="\n")
    (output_dir / "style.css").write_text(stylesheet, encoding="utf-8", newline="\n")
    (output_dir / "site.js").write_text(site_script, encoding="utf-8", newline="\n")
    write_lift_page(profile, source_dir, output_dir, figures, replacements)
    bib = "\n\n".join(bibtex(publication) for publication in publications)
    (output_dir / "publications.bib").write_text(bib + "\n", encoding="utf-8", newline="\n")
    for relative in assets:
        destination_asset = output_dir / relative
        destination_asset.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_dir / relative, destination_asset)
    write_icons(output_dir, theme["mark"])
    social_card(profile, source_dir / portrait["path"], source_dir, output_dir, palette)
    write_support_files(profile, output_dir, style_version, replacements["ICON_VERSION"])
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=SOURCE_DIR / "profile.json")
    parser.add_argument("--out", type=Path, default=SOURCE_DIR / "dist")
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    destination = build(profile, args.out)
    print(f"Built {destination}")


if __name__ == "__main__":
    main()
