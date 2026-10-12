#!/usr/bin/env python3
"""
import_substack.py: copies new Substack posts into the site as plain files.

    python3 scripts/import_substack.py            # import new posts
    python3 scripts/import_substack.py --dry-run  # show what would be imported

Rules (from site.json):
  - Only posts published on or after "substack_import_since" are imported.
  - Titles that start with "1-3-1" go to content/newsletter/. Everything else goes to content/articles/.
  - A post is never imported twice (matched by its Substack link).
  - Substack's subscribe buttons and widgets are removed; text, images, quotes and footnotes are kept.

Runs once a day on GitHub automatically. Uses only the Python standard library.
"""
import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/New_York")
except Exception:  # no time zone data on this machine
    LOCAL_TZ = dt.timezone(dt.timedelta(hours=-5))
NS = {"content": "http://purl.org/rss/1.0/modules/content/"}

KEEP_TAGS = {"p", "a", "em", "strong", "b", "i", "u", "s", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
             "ul", "ol", "li", "figure", "figcaption", "picture", "source", "img", "hr", "br", "code", "pre",
             "sup", "sub", "table", "thead", "tbody", "tr", "th", "td", "iframe"}
UNWRAP_TAGS = {"div", "span", "section", "article", "label"}          # keep their children, drop the tag
DROP_TAGS = {"script", "style", "form", "button", "svg", "input", "noscript", "textarea", "select"}
DROP_CLASSES = ("subscription-widget", "subscribe-widget", "button-wrapper", "image-link-expand", "pencraft",
                "fake-", "cta-caption", "share", "comment", "restack", "paywall", "poll")
KEEP_ATTRS = {"href", "src", "srcset", "sizes", "alt", "title", "width", "height", "type", "id",
              "allowfullscreen", "frameborder", "loading", "colspan", "rowspan"}
VOID = {"img", "source", "br", "hr", "input", "meta", "link", "wbr"}


class Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out, self.stack, self.drop_depth = [], [], 0

    def _should_drop(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        return tag in DROP_TAGS or any(c in cls for c in DROP_CLASSES)

    def handle_starttag(self, tag, attrs):
        if self.drop_depth:
            if tag not in VOID:
                self.drop_depth += 1
            return
        if self._should_drop(tag, attrs):
            if tag not in VOID:
                self.drop_depth = 1
            return
        if tag in KEEP_TAGS:
            kept = "".join(f' {k}="{escape(v or "", quote=True)}"' if v is not None else f" {k}"
                           for k, v in attrs if k in KEEP_ATTRS)
            if tag == "img" and "loading" not in dict(attrs):
                kept += ' loading="lazy"'
            self.out.append(f"<{tag}{kept}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.drop_depth:
            self.drop_depth -= 1
            return
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                if t in KEEP_TAGS:
                    self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.drop_depth:
            self.out.append(data)

    def handle_entityref(self, name):
        if not self.drop_depth:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.drop_depth:
            self.out.append(f"&#{name};")


def clean_html(html_in):
    c = Cleaner()
    c.feed(html_in)
    c.close()
    html_out = "".join(c.out)
    html_out = re.sub(r"<p>\s*</p>", "", html_out)
    html_out = re.sub(r"<a[^>]*>\s*</a>", "", html_out)
    html_out = re.sub(r"\n{3,}", "\n\n", html_out)
    return html_out.strip()


def slug_from_link(link, title):
    m = re.search(r"/p/([^/?#]+)", link)
    if m:
        return m.group(1)
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:80] or "post"


def already_imported(link):
    for kind in ("articles", "newsletter"):
        folder = ROOT / "content" / kind
        if not folder.exists():
            continue
        for f in folder.iterdir():
            if f.suffix in (".md", ".html"):
                head = f.read_text(encoding="utf-8", errors="ignore")[:1500]
                if f"source: {link}" in head:
                    return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", default=CFG.get("substack_import_since", "2000-01-01"))
    ap.add_argument("--feed", default=None, help="read the feed from a local file instead of the web")
    a = ap.parse_args()

    since = dt.date.fromisoformat(a.since)
    prefix = CFG.get("newsletter_title_prefix", "1-3-1:").rstrip(":").strip().lower()
    if a.feed:
        xml_bytes = Path(a.feed).read_bytes()
    else:
        url = CFG["substack"].rstrip("/") + "/feed"
        req = urllib.request.Request(url, headers={"User-Agent": "nishtarnotes-importer/1.0 (+https://nishtarnotes.com)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            xml_bytes = r.read()

    root = ET.fromstring(xml_bytes)
    new = 0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        if not (title and link and pub):
            continue
        posted = parsedate_to_datetime(pub).astimezone(LOCAL_TZ).date()
        if posted < since or already_imported(link):
            continue
        kind = "newsletter" if title.lower().startswith(prefix) else "articles"
        body = clean_html(item.findtext("content:encoded", default="", namespaces=NS))
        desc = unescape((item.findtext("description") or "").strip().replace("\n", " "))
        slug = slug_from_link(link, title)
        dest = ROOT / "content" / kind / f"{posted.isoformat()}-{slug}.html"
        front = ["---", f"title: {title}", f"date: {posted.isoformat()}", f"source: {link}"]
        if desc:
            front.append(f"excerpt: {desc}")
            if kind == "articles":
                front.append(f"subtitle: {desc}")
        front += ["# topic: Great Work   (optional: set a topic here, otherwise it is guessed from the text)", "---", ""]
        print(f"{'Would import' if a.dry_run else 'Imported'} {kind}: {title} -> {dest.relative_to(ROOT)}")
        if not a.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("\n".join(front) + body + "\n", encoding="utf-8")
        new += 1
    print(f"{new} new post(s)." if new else "Nothing new on Substack.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the daily build because Substack is down
        print(f"Substack import skipped: {e}", file=sys.stderr)
