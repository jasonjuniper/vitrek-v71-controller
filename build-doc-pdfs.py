"""
build-doc-pdfs.py — Convert selected Markdown docs to print-ready PDFs.

Pipeline:
  1. Read the Markdown file (assumed to contain raw <svg> blocks for figures)
  2. Render Markdown -> HTML via the Python `markdown` library with tables +
     fenced code blocks + attr_list extensions
  3. Wrap HTML in a print-friendly template (page size, margins, Juniper brand)
  4. Write the HTML to a temp file
  5. Invoke Chrome headless `--print-to-pdf` to render to PDF

Output: docs/pdf/<basename>.pdf

Run with system Python:
  & 'C:\\Users\\ENG2\\AppData\\Local\\Programs\\Python\\Python313\\python.exe' `
    'C:\\dev\\hi-pot_UART\\build-doc-pdfs.py'
"""

from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT  = r'C:\dev\hi-pot_UART'
DOCS_DIR   = os.path.join(REPO_ROOT, 'docs')
OUTPUT_DIR = os.path.join(DOCS_DIR,  'pdf')
CHROME     = r'C:\Program Files\Google\Chrome\Application\chrome.exe'

# Juniper brand kit — sibling repo with canonical Poppins font + CSS
BRAND_KIT  = r'C:\dev\juniper-brand-kit'
FONTS_DIR  = os.path.join(BRAND_KIT, 'fonts')
BANNER_URL = 'file:///' + os.path.join(BRAND_KIT, 'assets', 'juniper-banner.svg').replace('\\', '/')


def _doc_banner(md_text):
    """Juniper banner for the top of a doc PDF, unless the source already embeds it."""
    if 'juniper-banner' in md_text:
        return ''
    return '<div class="doc-banner"><img src="' + BANNER_URL + '"></div>\n'


def _font_url(name: str) -> str:
    p = os.path.join(FONTS_DIR, name).replace('\\', '/')
    return 'file:///' + p


FONT_LIGHT    = _font_url('Poppins-Light.ttf')
FONT_REGULAR  = _font_url('Poppins-Regular.ttf')
FONT_MEDIUM   = _font_url('Poppins-Medium.ttf')
FONT_SEMIBOLD = _font_url('Poppins-SemiBold.ttf')
FONT_BOLD     = _font_url('Poppins-Bold.ttf')

# (markdown_path, pdf_basename) — files to include in the PDF build
DOCS = [
    (os.path.join(REPO_ROOT,  'README.md'),                     'README.pdf'),
    (os.path.join(DOCS_DIR,   'wiring-guide.md'),               'wiring-guide.pdf'),
    (os.path.join(DOCS_DIR,   'plc-ladder-logic.md'),           'plc-ladder-logic.pdf'),
    (os.path.join(DOCS_DIR,   'logo-soft-comfort-guide.md'),    'logo-soft-comfort-guide.pdf'),
    (os.path.join(DOCS_DIR,   'hmi-architecture.md'),           'hmi-architecture.pdf'),
]

PAGE_SIZE_CSS = 'Letter'
PAGE_MARGIN   = '0.5in'

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @font-face {{
    font-family: "Poppins"; font-style: normal; font-weight: 300;
    src: url("{font_light}") format("truetype");
  }}
  @font-face {{
    font-family: "Poppins"; font-style: normal; font-weight: 400;
    src: url("{font_regular}") format("truetype");
  }}
  @font-face {{
    font-family: "Poppins"; font-style: normal; font-weight: 500;
    src: url("{font_medium}") format("truetype");
  }}
  @font-face {{
    font-family: "Poppins"; font-style: normal; font-weight: 600;
    src: url("{font_semibold}") format("truetype");
  }}
  @font-face {{
    font-family: "Poppins"; font-style: normal; font-weight: 700;
    src: url("{font_bold}") format("truetype");
  }}
  :root {{
    --juniper-navy:     #1a1a2e;
    --juniper-charcoal: #3c3c3d;
    --juniper-offwhite: #e8e7e3;
    --juniper-tagline:  #5a5a5a;
    --juniper-accent:   #6e8cff;
    --juniper-primary:  #1565c0;
    --juniper-font: "Poppins", "Helvetica Neue", "Helvetica", "Inter", "Segoe UI", Arial, sans-serif;
    --bg:             #ffffff;
    --bg-elev:        #f5f5f5;
    --border:         #e0e0e0;
    --hairline:       #eeeeee;
    --table-head-bg:  #f5f5f5;
  }}
  @page {{
    size: {page_size};
    margin: {page_margin};
  }}
  html, body {{
    font-family: var(--juniper-font);
    line-height: 1.45;
    color: var(--juniper-navy);
    font-size: 10.5pt;
    margin: 0; padding: 0;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  body {{ max-width: 7.0in; margin: 0 auto; }}
  h1 {{
    font-weight: 700; font-size: 22pt; color: var(--juniper-navy);
    margin-top: 0; border-bottom: 2px solid var(--juniper-navy);
    padding-bottom: 6px; letter-spacing: -0.01em;
  }}
  h2 {{
    font-weight: 600; font-size: 14.5pt; color: var(--juniper-navy);
    margin-top: 1.4em; page-break-after: avoid;
    border-bottom: 1px solid var(--hairline); padding-bottom: 3px;
  }}
  h3 {{
    font-weight: 600; font-size: 12pt; color: var(--juniper-charcoal);
    margin-top: 1em; page-break-after: avoid;
  }}
  h4 {{
    font-weight: 600; font-size: 10.5pt; color: var(--juniper-charcoal);
    margin-top: 0.8em; page-break-after: avoid;
  }}
  strong, b {{ font-weight: 600; }}
  table {{
    border-collapse: collapse; margin: 0.75em 0;
    font-size: 9.8pt; width: auto; page-break-inside: avoid;
  }}
  th, td {{
    border: 1px solid var(--border); padding: 5px 9px;
    text-align: left; vertical-align: top;
  }}
  th {{ background: var(--table-head-bg); color: var(--juniper-navy); font-weight: 600; }}
  svg {{ display: block; margin: 0.8em auto; max-width: 100%; height: auto; page-break-inside: avoid; }}
  code {{
    background: var(--bg-elev); padding: 0.1em 0.35em;
    border-radius: 3px; font-size: 0.9em;
    font-family: "SF Mono", Monaco, Consolas, "Liberation Mono", monospace;
    color: var(--juniper-charcoal);
  }}
  pre {{
    background: var(--bg-elev); border-left: 3px solid var(--juniper-accent);
    padding: 0.6em 0.9em; font-size: 0.88em;
    font-family: "SF Mono", Monaco, Consolas, "Liberation Mono", monospace;
    color: var(--juniper-charcoal); border-radius: 0 3px 3px 0;
    page-break-inside: avoid; overflow-x: auto; white-space: pre-wrap;
  }}
  pre code {{ background: transparent; padding: 0; }}
  blockquote {{
    border-left: 3px solid var(--juniper-accent); background: var(--bg-elev);
    margin: 0.8em 0; padding: 0.5em 0.9em; color: var(--juniper-charcoal);
    page-break-inside: avoid;
  }}
  blockquote p {{ margin: 0.2em 0; }}
  ul, ol {{ margin: 0.5em 0; padding-left: 1.5em; }}
  li {{ margin: 0.2em 0; }}
  hr {{ border: 0; border-top: 1px solid var(--hairline); margin: 1em 0; }}
  p[align="center"] img {{ max-width: 80%; height: auto; }}
  a {{ color: var(--juniper-primary); text-decoration: none; }}
  .juniper-doc-footer {{
    margin-top: 2em; padding-top: 0.6em;
    border-top: 1px solid var(--hairline); text-align: center;
    font-weight: 400; font-size: 8.5pt; color: var(--juniper-tagline);
    letter-spacing: 0.04em;
  }}
  .juniper-doc-footer a {{ color: var(--juniper-tagline); }}
  sub {{ color: var(--juniper-tagline); font-size: 9pt; }}
  .doc-banner img {{ width:100%; height:auto; margin:0 0 16pt 0; }}
</style>
</head>
<body>
{body}
<div class="juniper-doc-footer">
  Generated {date} · <a href="https://juniperdesign.com">Juniper Design</a> · juniper-test-station
</div>
</body>
</html>
"""


def render_markdown(md_text: str) -> str:
    import re
    import markdown

    svg_blocks: list[str] = []
    sentinel = '@@SVG_BLOCK_{}@@'

    def _capture(m):
        idx = len(svg_blocks)
        svg_blocks.append(m.group(0))
        return sentinel.format(idx)

    md_protected = re.sub(r'<svg\b[^>]*>.*?</svg>', _capture, md_text, flags=re.DOTALL)

    md = markdown.Markdown(
        extensions=['tables', 'fenced_code', 'attr_list', 'md_in_html'],
        output_format='html5',
    )
    html = md.convert(md_protected)

    for idx, svg in enumerate(svg_blocks):
        html = html.replace(f'<p>{sentinel.format(idx)}</p>', svg)
        html = html.replace(sentinel.format(idx), svg)

    return html


def build_pdf(md_path: str, pdf_basename: str) -> None:
    if not os.path.exists(md_path):
        raise FileNotFoundError(md_path)
    with open(md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    title = pdf_basename
    for line in md_text.splitlines():
        if line.startswith('# '):
            title = line[2:].strip()
            break

    md_dir = os.path.dirname(os.path.abspath(md_path))
    import re
    def _rewrite(m):
        prefix, rel, suffix = m.group(1), m.group(2), m.group(3)
        if rel.startswith(('http://', 'https://', 'data:', 'file:///')):
            return m.group(0)
        abs_p = os.path.normpath(os.path.join(md_dir, rel))
        return prefix + 'file:///' + abs_p.replace('\\', '/') + suffix
    md_text = re.sub(r'(src=")([^"]+)(")', _rewrite, md_text)

    body_html = _doc_banner(md_text) + render_markdown(md_text)
    html_doc = HTML_TEMPLATE.format(
        title=title, body=body_html,
        page_size=PAGE_SIZE_CSS, page_margin=PAGE_MARGIN,
        date=datetime.date.today().isoformat(),
        font_light=FONT_LIGHT, font_regular=FONT_REGULAR,
        font_medium=FONT_MEDIUM, font_semibold=FONT_SEMIBOLD, font_bold=FONT_BOLD,
    )

    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, encoding='utf-8') as tmp:
        tmp.write(html_doc)
        html_path = tmp.name

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(OUTPUT_DIR, pdf_basename)
    file_url = 'file:///' + html_path.replace('\\', '/')

    cmd = [
        CHROME, '--headless=new', '--disable-gpu', '--no-sandbox',
        '--no-pdf-header-footer', f'--print-to-pdf={pdf_path}', file_url,
    ]
    print(f'[+] {os.path.basename(md_path)} -> {pdf_basename}')
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print('    Chrome stderr:', result.stderr[:500])
        if os.path.exists(pdf_path):
            size_kb = os.path.getsize(pdf_path) / 1024
            print(f'    OK -> {pdf_path} ({size_kb:.1f} KB)')
        else:
            print(f'    FAILED: no PDF produced')
    finally:
        try:
            os.remove(html_path)
        except OSError:
            pass


def main() -> int:
    if not os.path.exists(CHROME):
        print(f'ERROR: Chrome not found at {CHROME}')
        return 1
    for md_path, pdf_basename in DOCS:
        try:
            build_pdf(md_path, pdf_basename)
        except FileNotFoundError as e:
            print(f'    SKIP: {e}')
    print(f'\nAll PDFs in {OUTPUT_DIR}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
