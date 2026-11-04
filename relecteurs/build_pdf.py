#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenere relecteurs/guide_relecteur_openrefine.pdf a partir du Markdown.

Prerequis (hors depot, uniquement pour celui qui regenere le PDF) :
  - le module Python `markdown`  : python3 -m pip install --user markdown
  - un Chrome/Chromium headless  : trouve automatiquement (google-chrome, chromium,
    chromium-browser) ou indique par la variable d'environnement CHROME_BIN.

Usage :
  python3 relecteurs/build_pdf.py            # ecrit guide_relecteur_openrefine.pdf
  python3 relecteurs/build_pdf.py --html     # ecrit aussi guide_relecteur_openrefine.html

Les images sont incorporees en base64 : le HTML intermediaire est autonome.
"""
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "guide_relecteur_openrefine.md")
PDF = os.path.join(HERE, "guide_relecteur_openrefine.pdf")

CSS = """
@page { size: A4; margin: 18mm 16mm; }
html { font-size: 11pt; }
body { font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", Arial, sans-serif; color: #1d1d1f;
       line-height: 1.45; max-width: 100%; margin: 0; }
.cover { padding: 60mm 0 20mm 0; page-break-after: always; text-align: left; }
.cover h1 { font-size: 26pt; margin: 0 0 10pt 0; line-height: 1.2; }
.cover .sub { font-size: 14pt; color: #555; margin-bottom: 30pt; }
.cover .box { border: 1px solid #c9c9c9; background: #f4f6fb; padding: 10pt 12pt; font-size: 10.5pt; }
h1 { font-size: 20pt; margin-top: 0; }
h2 { font-size: 15pt; margin-top: 22pt; padding-bottom: 3pt; border-bottom: 2px solid #2f5fa7;
     color: #2f5fa7; page-break-before: always; page-break-after: avoid; }
h2.first { page-break-before: auto; }
h3 { font-size: 12.5pt; margin-top: 16pt; color: #333; page-break-after: avoid; }
p { margin: 6pt 0; orphans: 3; widows: 3; }
ul, ol { margin: 4pt 0 6pt 0; padding-left: 20pt; }
li { margin: 2pt 0; }
code { font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 9.5pt; background: #f1f1f1;
       padding: 0 3px; border-radius: 3px; }
pre { background: #f1f1f1; padding: 6pt; font-size: 9pt; white-space: pre-wrap; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.8pt; page-break-inside: auto; }
th, td { border: 1px solid #cfcfcf; padding: 4pt 6pt; vertical-align: top; text-align: left; }
th { background: #e8eef8; }
tr { page-break-inside: avoid; }
img { max-width: 100%; height: auto; border: 1px solid #cfcfcf; display: block; margin: 8pt auto 2pt auto;
      page-break-inside: avoid; }
p.caption { font-size: 9pt; color: #555; text-align: center; margin: 0 0 10pt 0; font-style: italic; }
blockquote { margin: 8pt 0; padding: 6pt 10pt; border-left: 4px solid #2f5fa7; background: #f4f6fb; }
blockquote p { margin: 3pt 0; }
a { color: #1f4e9c; text-decoration: none; word-break: break-all; }
sup { font-size: 70%; }
"""


def find_chrome():
    cand = [os.environ.get("CHROME_BIN", "")] + [
        shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")]
    for c in cand:
        if c and os.path.exists(c):
            return c
    return None


def split_front_matter(text):
    meta = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"')
            text = text[end + 4:]
    return meta, text


def embed_images(html):
    def repl(m):
        src = m.group(1)
        path = os.path.join(HERE, src)
        if not os.path.exists(path):
            return m.group(0)
        ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
        data = base64.b64encode(open(path, "rb").read()).decode("ascii")
        return 'src="data:image/%s;base64,%s"' % (ext, data)
    return re.sub(r'src="([^"]+)"', repl, html)


def captions(html):
    # <p><img alt="..."></p>  ->  image + legende
    return re.sub(r'<p>(<img[^>]*alt="([^"]*)"[^>]*>)</p>',
                  lambda m: m.group(1) + '<p class="caption">' + m.group(2) + '</p>', html)


def main():
    try:
        import markdown
    except ImportError:
        sys.exit("Le module Python 'markdown' manque : python3 -m pip install --user markdown")
    want_html = "--html" in sys.argv
    meta, body = split_front_matter(open(SRC, encoding="utf-8").read())
    md = markdown.Markdown(extensions=["tables", "sane_lists", "smarty"],
                           extension_configs={"smarty": {"smart_quotes": False}})
    content = md.convert(body)
    # le H1 est deja sur la couverture
    content = re.sub(r"^\s*<h1>.*?</h1>", "", content, count=1, flags=re.S)
    content = re.sub(r"<h2>", '<h2 class="first">', content, count=1)
    content = captions(embed_images(content))
    title = meta.get("title", "Guide du relecteur")
    sub = meta.get("subtitle", "")
    cover = ('<div class="cover"><h1>%s</h1><div class="sub">%s</div>'
             '<div class="box">Version du %s · Dossier du relecteur, recettes et lot d\'exemple : '
             '<a href="https://github.com/Grand-Siecle/tei-entity-reconciliation/tree/main/relecteurs">'
             'github.com/Grand-Siecle/tei-entity-reconciliation/tree/main/relecteurs</a></div></div>'
             % (title, sub, __import__("datetime").date.today().strftime("%d/%m/%Y")))
    html = ('<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>%s</title>'
            '<style>%s</style></head><body>%s%s</body></html>' % (title, CSS, cover, content))
    if want_html:
        open(os.path.join(HERE, "guide_relecteur_openrefine.html"), "w", encoding="utf-8").write(html)
    chrome = find_chrome()
    if not chrome:
        sys.exit("Chrome/Chromium introuvable : installez-le ou renseignez CHROME_BIN.")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "guide.html")
        open(src, "w", encoding="utf-8").write(html)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
               "--print-to-pdf=" + PDF, "file://" + src]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not os.path.exists(PDF):
            sys.exit("Echec du rendu PDF :\n" + r.stderr[-2000:])
    print("PDF ecrit :", PDF, "(%d Ko)" % (os.path.getsize(PDF) // 1024))


if __name__ == "__main__":
    main()
