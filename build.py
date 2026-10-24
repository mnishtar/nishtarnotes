#!/usr/bin/env python3
"""
build.py: turns the plain-text files in content/ into the website.

    python3 build.py              # builds the real site into _site/
    python3 build.py --preview    # builds a preview (links end in index.html, forms don't submit)
    python3 build.py --examples   # also includes the sample posts in examples/ (never use for the real site)

Uses only the Python standard library, so it will still run in ten years
without installing anything.

Content lives in three folders:
    content/notes/        daily Notes        (YYYY-MM-DD-slug.md)
    content/articles/     monthly Articles   (YYYY-MM-DD-slug.md or .html)
    content/newsletter/   weekly Five Notes       (YYYY-MM-DD-slug.md or .html)

Each file starts with a small header:
    ---
    title: The title
    date: 2026-10-12
    topic: Attention          (optional; guessed from the text if left out)
    excerpt: One-sentence teaser (optional; first paragraph is used if left out)
    subtitle: Line under the title (optional, Articles only)
    source: https://... (optional; original link, e.g. on Substack)
    ---
    The body, in Markdown.
"""
import argparse
import datetime as dt
import html
import json
import re
import shutil
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))
TOPICS = [t for g in CFG["topic_groups"] for t in g["topics"]]
TOPIC_BY_KEY = {}
for _t in TOPICS:
    TOPIC_BY_KEY[_t["slug"]] = _t
    TOPIC_BY_KEY[_t["name"].lower()] = _t
DEFAULT_TOPIC = TOPICS[0]

KINDS = {
    "notes": {"label": "Note", "plural": "Notes", "dir": "notes"},
    "articles": {"label": "Article", "plural": "Articles", "dir": "articles"},
    "newsletter": {"label": "Five Notes", "plural": "Newsletters", "dir": "newsletter"},
}


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

def esc(s):
    return html.escape(str(s), quote=True)


def title_html(s):
    """Escape a title and keep "Five Notes" from breaking across lines."""
    return esc(s).replace("Five Notes", '<span class="nw">Five Notes</span>')


def long_date(d):
    return f"{d:%B} {d.day}, {d.year}"


def short_date(d):
    return f"{d:%b} {d.day}".upper()


def strip_tags(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(re.sub(r"\s+", " ", s)).strip()


def truncate(text, limit=190):
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.")
    return cut + "…"


def slugify(s):
    s = html.unescape(s).lower()
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:80].rstrip("-") or "untitled"


def guess_topic(text):
    """Pick the topic whose keywords appear most often. Ties go to the first topic."""
    lower = " " + text.lower() + " "
    best, best_score = DEFAULT_TOPIC, 0
    for t in TOPICS:
        score = 0
        for k in t["keywords"]:
            score += len(re.findall(r"(?<![a-z])" + re.escape(k.lower()) + r"(?![a-z])", lower))
        if score > best_score:
            best, best_score = t, score
    return best


# ----------------------------------------------------------------------------
# Markdown (the small subset a writer needs)
# ----------------------------------------------------------------------------

def md_inline(s, fn_numbers):
    codes = []

    def keep_code(m):
        codes.append("<code>" + html.escape(m.group(1)) + "</code>")
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", keep_code, s)
    s = re.sub(r"&(?!#?\w+;)", "&amp;", s)
    s = re.sub(r"<(?![a-zA-Z/!])", "&lt;", s)
    s = re.sub(r'!\[([^\]]*)\]\((\S+?)(?:\s+"([^"]*)")?\)',
               lambda m: f'<img src="{m.group(2)}" alt="{esc(m.group(1))}" loading="lazy">', s)
    s = re.sub(r"\[\^([\w-]+)\]",
               lambda m: (f'<sup class="fn-ref"><a href="#fn-{m.group(1)}" id="fnref-{m.group(1)}">'
                          f'{fn_numbers.setdefault(m.group(1), len(fn_numbers) + 1)}</a></sup>'), s)
    s = re.sub(r"\[([^\]]+)\]\((\S+?)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])", r"<em>\1</em>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)
    return s


def markdown(text, fn_numbers=None, footnotes=True):
    fn_numbers = {} if fn_numbers is None else fn_numbers
    fn_defs = {}
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^\[\^([\w-]+)\]:\s*(.*)$", line)
        if m:
            fn_defs[m.group(1)] = m.group(2)
        else:
            lines.append(line)

    out, para, i = [], [], 0

    def flush():
        if para:
            out.append("<p>" + md_inline(" ".join(x.strip() for x in para), fn_numbers) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush(); i += 1; continue
        m = re.match(r"^(#{1,4})\s+(.*?)\s*#*$", stripped)
        if m:
            flush()
            level = {1: 2, 2: 2, 3: 3, 4: 4}[len(m.group(1))]
            out.append(f"<h{level}>{md_inline(m.group(2), fn_numbers)}</h{level}>")
            i += 1; continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush(); out.append("<hr>"); i += 1; continue
        if stripped.startswith(">"):
            flush()
            inner = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                inner.append(re.sub(r"^\s*>\s?", "", lines[i])); i += 1
            out.append("<blockquote>" + markdown("\n".join(inner), fn_numbers, False) + "</blockquote>")
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            flush()
            ordered = bool(re.match(r"^\d", stripped))
            items = []
            while i < len(lines) and lines[i].strip():
                l = lines[i]
                mm = re.match(r"^\s*([-*+]|\d+[.)])\s+(.*)$", l)
                if mm:
                    items.append(mm.group(2))
                elif items:
                    items[-1] += " " + l.strip()
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{md_inline(x, fn_numbers)}</li>" for x in items) + f"</{tag}>")
            continue
        if stripped.startswith("<"):
            flush()
            block = []
            while i < len(lines) and lines[i].strip():
                block.append(lines[i]); i += 1
            out.append("\n".join(block))
            continue
        para.append(line); i += 1
    flush()

    if footnotes and fn_numbers:
        items = sorted(fn_numbers.items(), key=lambda kv: kv[1])
        lis = "".join(
            f'<li id="fn-{k}">{md_inline(fn_defs.get(k, ""), {})} <a href="#fnref-{k}" class="fn-back" aria-label="Back to text">↩</a></li>'
            for k, _ in items)
        out.append(f'<div class="footnotes"><p class="footnotes-label">Notes</p><ol>{lis}</ol></div>')
    return "\n".join(out)


# ----------------------------------------------------------------------------
# Loading content
# ----------------------------------------------------------------------------

def parse_file(path):
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip('"')
            body = text[end + 4:].lstrip("\n")
    return meta, body


def load_items(kind, folders):
    items, seen = [], set()
    for folder in folders:
        if not folder.exists():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in (".md", ".html") or path.name.startswith((".", "_")):
                continue
            meta, body = parse_file(path)
            stem = path.stem
            m = re.match(r"^(\d{4}-\d{2}-\d{2})-?(.*)$", stem)
            date_s = meta.get("date") or (m.group(1) if m else None)
            try:
                date = dt.date.fromisoformat(date_s[:10]) if date_s else dt.date.fromtimestamp(path.stat().st_mtime)
            except ValueError:
                print(f"  ! {path.name}: bad date '{date_s}', using file date")
                date = dt.date.fromtimestamp(path.stat().st_mtime)
            if path.suffix.lower() == ".html":
                body_html = body
            else:
                body_html = markdown(body)
            plain = strip_tags(body_html)
            title = meta.get("title") or (plain.split(". ")[0][:90] if plain else stem)
            slug = meta.get("slug") or (m.group(2) if m and m.group(2) else slugify(title))
            base, n = slug, 2
            while slug in seen:
                slug = f"{base}-{n}"; n += 1
            seen.add(slug)
            topic_key = (meta.get("topic") or meta.get("category") or "").lower()
            topic = TOPIC_BY_KEY.get(topic_key) or TOPIC_BY_KEY.get(slugify(topic_key)) or guess_topic(title + " " + plain)
            first_p = re.search(r"<p[^>]*>(.*?)</p>", re.sub(r"<sup[^>]*>.*?</sup>", "", body_html, flags=re.S), re.S)
            excerpt = meta.get("excerpt") or truncate(strip_tags(first_p.group(1)) if first_p else plain)
            items.append({
                "kind": kind, "title": title, "date": date, "slug": slug, "topic": topic,
                "excerpt": excerpt, "subtitle": meta.get("subtitle", ""), "source": meta.get("source", ""),
                "body": body_html, "words": len(plain.split()),
                "path": f"{KINDS[kind]['dir']}/{slug}/",
            })
    items.sort(key=lambda x: (x["date"], x["title"]), reverse=True)
    return items


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------

class Linker:
    """Builds relative links so the site works on any domain, in a folder, or opened from disk."""

    def __init__(self, depth, preview, absolute=False):
        self.prefix = "/" if absolute else "../" * depth
        self.preview = preview

    def __call__(self, path=""):
        if path.startswith(("http://", "https://", "#", "mailto:")):
            return path
        if path == "" or path.endswith("/"):
            url = self.prefix + path + ("index.html" if self.preview else "")
            return url or "./"
        return self.prefix + path


def template(name):
    return (ROOT / "templates" / name).read_text(encoding="utf-8")


def fill(tpl, L, ctx):
    tpl = re.sub(r"\{\{link:(.*?)\}\}", lambda m: L(m.group(1).strip()), tpl)
    tpl = re.sub(r"\{\{cfg:(\w+)\}\}", lambda m: esc(CFG.get(m.group(1), "")), tpl)
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(ctx.get(m.group(1), "")), tpl)


class Builder:
    def __init__(self, out, preview, examples):
        self.out, self.preview = out, preview
        self.form_id = 0
        folders = lambda k: [ROOT / "content" / k] + ([ROOT / "examples" / k] if examples else [])
        self.items = {k: load_items(k, folders(k)) for k in KINDS}
        self.all = sorted((i for k in KINDS for i in self.items[k]), key=lambda x: x["date"], reverse=True)

    # ---- shared pieces ----

    def signup(self, heading, button, note):
        self.form_id += 1
        fid = f"email-{self.form_id}"
        action = CFG["substack"].rstrip("/") + "/api/v1/free?nojs=true"
        return (
            f'<aside class="signup-card" aria-label="Newsletter signup">'
            f'<p class="signup-heading">{heading}</p>'
            f'<form class="signup-form" action="{esc(action)}" method="post" target="_blank" rel="noopener">'
            f'<label class="sr-only" for="{fid}">Email address</label>'
            f'<input id="{fid}" type="email" name="email" placeholder="Your email address" autocomplete="email" required>'
            f'<button type="submit">{button}</button></form>'
            f'<p class="signup-note">{note}</p>'
            f'<p class="signup-status" role="status" hidden></p></aside>')

    def topic_link(self, L, topic):
        return f'<a class="topic-tag" href="{L("topics/" + topic["slug"] + "/")}">{esc(topic["name"])}</a>'

    def page(self, rel_path, title, content, L, nav="", description="", body_class="", canonical=""):
        nav_attrs = {f"nav_{k}": (' aria-current="page"' if k == nav else "") for k in
                     ("notes", "articles", "newsletter", "app", "about")}
        full_title = title if title == CFG["name"] else f"{title} | {CFG['name']}"
        preview_js = ""
        if self.preview:
            preview_js = ("<script>document.querySelectorAll('.signup-form').forEach(function(f){"
                          "f.addEventListener('submit',function(e){e.preventDefault();"
                          "var s=f.parentNode.querySelector('.signup-status');s.hidden=false;"
                          "s.textContent='Preview only. On nishtarnotes.com this button subscribes you through Substack.';});});"
                          "</script>")
        html_out = fill(template("base.html"), L, {
            "title": esc(full_title), "description": esc(description or CFG["description"]),
            "canonical": esc(CFG["url"].rstrip("/") + "/" + canonical),
            "content": content, "body_class": body_class, "year": dt.date.today().year,
            "preview_js": preview_js, **nav_attrs,
        })
        target = self.out / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_out, encoding="utf-8")

    # ---- lists ----

    def item_rows(self, L, items, show_kind=False, show_excerpt=True):
        rows = []
        for it in items:
            kind = f'<span class="row-kind">{KINDS[it["kind"]]["label"] if it["kind"] != "newsletter" else "Five Notes"}</span>' if show_kind else ""
            ex = f'<p class="row-excerpt">{esc(it["excerpt"])}</p>' if show_excerpt and it["excerpt"] else ""
            rows.append(
                f'<li class="row"><a class="row-link" href="{L(it["path"])}">'
                f'<span class="row-title">{title_html(it["title"])}</span>{ex}</a>'
                f'<p class="row-meta">{kind}<time datetime="{it["date"].isoformat()}">{long_date(it["date"])}</time></p></li>')
        return f'<ul class="rows">{"".join(rows)}</ul>'

    def note_stream(self, L, notes):
        out = []
        for n in notes:
            out.append(
                f'<article class="note-full">'
                f'<p class="note-meta"><time datetime="{n["date"].isoformat()}">{long_date(n["date"])}</time>'
                f'<span aria-hidden="true">·</span>{self.topic_link(L, n["topic"])}</p>'
                f'<h2 class="note-title"><a href="{L(n["path"])}">{esc(n["title"])}</a></h2>'
                f'<div class="prose">{n["body"]}</div></article>')
        return "".join(out)

    def month_archive(self, L, items):
        groups = {}
        for it in items:
            groups.setdefault((it["date"].year, it["date"].month), []).append(it)
        out = []
        for (y, m), its in groups.items():
            lis = "".join(
                f'<li><a href="{L(i["path"])}">{esc(i["title"])}</a><time datetime="{i["date"].isoformat()}">{short_date(i["date"])}</time></li>'
                for i in its)
            out.append(f'<section class="month"><h3>{dt.date(y, m, 1):%B %Y}</h3><ul>{lis}</ul></section>')
        return "".join(out)

    def issue_archive(self, L, issues):
        years = {}
        for it in issues:
            years.setdefault(it["date"].year, []).append(it)
        out = []
        for y, its in years.items():
            lis = "".join(
                f'<li><time datetime="{i["date"].isoformat()}">{short_date(i["date"])}</time>'
                f'<a href="{L(i["path"])}">{title_html(i["title"])}</a></li>' for i in its)
            out.append(f'<section class="issue-year"><h3>{y}</h3><ol class="issue-list">{lis}</ol></section>')
        return "".join(out)

    def topic_columns(self, L, kind, per_topic=3):
        groups = []
        for g in CFG["topic_groups"]:
            cols = []
            for t in g["topics"]:
                its = [i for i in self.items[kind] if i["topic"]["slug"] == t["slug"]]
                if its:
                    lis = "".join(f'<li><a href="{L(i["path"])}">{esc(i["title"])}</a></li>' for i in its[:per_topic])
                    listing = f'<ul class="hairline-list">{lis}</ul>'
                else:
                    listing = f'<p class="topic-empty">{esc(t["blurb"])}</p>'
                cols.append(
                    f'<section class="topic-col"><h3><a href="{L("topics/" + t["slug"] + "/")}">{esc(t["name"])}</a></h3>'
                    f'{listing}<a class="more-link" href="{L("topics/" + t["slug"] + "/")}">Read more on {esc(t["name"])}<span aria-hidden="true"> →</span></a></section>')
            groups.append(f'<div class="topic-group"><p class="group-label">{esc(g["name"])}</p>'
                          f'<div class="topic-cols">{"".join(cols)}</div></div>')
        return "".join(groups)

    def topic_tiles(self, L):
        groups = []
        for g in CFG["topic_groups"]:
            tiles = []
            for t in g["topics"]:
                count = sum(1 for i in self.all if i["topic"]["slug"] == t["slug"] and i["kind"] != "newsletter")
                c = f'<span class="tile-count">{count} {"piece" if count == 1 else "pieces"}</span>' if count else ""
                tiles.append(f'<a class="tile" href="{L("topics/" + t["slug"] + "/")}"><span class="tile-name">{esc(t["name"])}</span>'
                             f'<span class="tile-blurb">{esc(t["blurb"])}</span>{c}</a>')
            groups.append(f'<div class="tile-group"><p class="group-label">{esc(g["name"])}</p><div class="tiles">{"".join(tiles)}</div></div>')
        return "".join(groups)

    @staticmethod
    def empty(text):
        return f'<p class="empty-state">{text}</p>'

    # ---- pages ----

    def build_home(self):
        L = Linker(0, self.preview)
        notes, issues, arts = self.items["notes"], self.items["newsletter"], self.items["articles"]
        latest_notes = self.item_rows(L, notes[:5]) if notes else self.empty("The first Note is on its way. A new one publishes most days.")
        latest_issue = self.item_rows(L, issues[:3]) if issues else self.empty("The first issue of Five Notes is coming soon. Sign up above and it will land in your inbox on a Friday.")
        latest_articles = self.item_rows(L, arts[:3]) if arts else self.empty(esc(CFG["articles_coming"]))
        content = fill(template("home.html"), L, {
            "signup": self.signup("One quote, three ideas, one question. Every Friday.", "Get Five Notes",
                                  "One email a week. Unsubscribe anytime."),
            "latest_notes": latest_notes, "latest_issue": latest_issue, "latest_articles": latest_articles,
            "topic_tiles": self.topic_tiles(L),
        })
        self.page("index.html", CFG["name"], content, L, body_class="page-home")

    def build_notes(self):
        L = Linker(1, self.preview)
        notes = self.items["notes"]
        k = CFG.get("notes_on_notes_page", 10)
        stream = self.note_stream(L, notes[:k]) if notes else self.empty("The first Note is on its way. A new one publishes most days.")
        archive = ""
        if len(notes) > k:
            archive = f'<section class="archive"><h2 class="section-title">The archive</h2>{self.month_archive(L, notes[k:])}</section>'
        content = fill(template("notes.html"), L, {
            "signup": self.signup("Get Five Notes in your inbox every Friday.", "Get Five Notes", "One email a week. Unsubscribe anytime."),
            "stream": stream, "archive": archive, "count": len(notes),
        })
        self.page("notes/index.html", "Notes", content, L, nav="notes", canonical="notes/",
                  description="Short daily essays by Mahd Nishtar on attention, health and Great Work.")

    def build_articles(self):
        L = Linker(1, self.preview)
        arts = self.items["articles"]
        notice = "" if arts else f'<p class="notice">{esc(CFG["articles_coming"])}</p>'
        content = fill(template("articles.html"), L, {
            "signup": self.signup("Research-backed ideas for doing your Great Work.", "Get the free newsletter",
                                  "One email a week. Unsubscribe anytime."),
            "notice": notice, "columns": self.topic_columns(L, "articles"),
            "recent": self.item_rows(L, arts[:6]) if arts else "",
            "recent_heading": '<h2 class="section-title">Most recent</h2>' if arts else "",
        })
        self.page("articles/index.html", "Articles", content, L, nav="articles", canonical="articles/",
                  description="Monthly deep dives by Mahd Nishtar on attention, health and Great Work.")

    def build_newsletter(self):
        L = Linker(1, self.preview)
        issues = self.items["newsletter"]
        archive = self.issue_archive(L, issues) if issues else self.empty("The first issue is coming soon. Sign up and it will be waiting in your inbox on a Friday.")
        content = fill(template("newsletter.html"), L, {
            "signup": self.signup("Get Five Notes every Friday.", "Try it", "One email a week. Unsubscribe anytime."),
            "archive": archive,
        })
        self.page("newsletter/index.html", "Five Notes", content, L, nav="newsletter", canonical="newsletter/",
                  description="Five Notes: 1 quote, 3 ideas and 1 question from Nishtar, every Friday.")

    def build_static(self, name, nav, title, description):
        L = Linker(1, self.preview)
        content = fill(template(f"{name}.html"), L, {
            "signup": self.signup("Get Five Notes every Friday.", "Try it", "One email a week. Unsubscribe anytime."),
        })
        self.page(f"{name}/index.html", title, content, L, nav=nav, canonical=f"{name}/",
                  description=description, body_class=f"page-{name}")

    def build_topics(self):
        for t in TOPICS:
            L = Linker(2, self.preview)
            arts = [i for i in self.items["articles"] if i["topic"]["slug"] == t["slug"]]
            notes = [i for i in self.items["notes"] if i["topic"]["slug"] == t["slug"]]
            parts = []
            if arts:
                parts.append(f'<section class="topic-section"><h2 class="section-title">Articles</h2>{self.item_rows(L, arts)}</section>')
            if notes:
                parts.append(f'<section class="topic-section"><h2 class="section-title">Notes</h2>{self.item_rows(L, notes)}</section>')
            if not parts:
                parts.append(self.empty(f"Nothing on {esc(t['name'])} yet. New Notes publish most days, and each one is filed under its topic automatically."))
            group = next(g["name"] for g in CFG["topic_groups"] if t in g["topics"])
            content = fill(template("topic.html"), L, {
                "group": esc(group), "name": esc(t["name"]), "blurb": esc(t["blurb"]),
                "sections": "".join(parts),
                "signup": self.signup(f"Get my best ideas on {esc(t['name'].lower())} every Friday.", "Get Five Notes",
                                      "One email a week. Unsubscribe anytime."),
            })
            self.page(f"topics/{t['slug']}/index.html", t["name"], content, L, nav="articles",
                      canonical=f"topics/{t['slug']}/", description=t["blurb"])

    def build_items(self):
        for kind, items in self.items.items():
            for idx, it in enumerate(items):
                L = Linker(2, self.preview)
                newer = items[idx - 1] if idx > 0 else None
                older = items[idx + 1] if idx + 1 < len(items) else None
                nav_links = []
                if older:
                    nav_links.append(f'<a class="prev" href="{L(older["path"])}"><span class="nav-dir">Previous</span><span class="nav-title">{title_html(older["title"])}</span></a>')
                if newer:
                    nav_links.append(f'<a class="next" href="{L(newer["path"])}"><span class="nav-dir">Next</span><span class="nav-title">{title_html(newer["title"])}</span></a>')
                related = [i for i in self.all if i is not it and i["topic"] is it["topic"] and i["kind"] != "newsletter"][:3]
                related_html = ""
                if related and kind != "newsletter":
                    related_html = (f'<section class="keep-reading"><h2 class="section-title">More on {self.topic_link(L, it["topic"])}</h2>'
                                    f'{self.item_rows(L, related, show_kind=True, show_excerpt=False)}</section>')
                meta = f'<time datetime="{it["date"].isoformat()}">{long_date(it["date"])}</time>'
                if kind != "newsletter":
                    meta += f'<span aria-hidden="true">·</span>{self.topic_link(L, it["topic"])}'
                if kind == "articles" and it["words"] > 300:
                    meta += f'<span aria-hidden="true">·</span><span>{max(1, round(it["words"] / 230))} min read</span>'
                source = ""
                if it["source"]:
                    host = re.sub(r"^https?://(www\.)?", "", it["source"]).split("/")[0]
                    where = "Substack" if "substack" in host else "X" if host in ("x.com", "twitter.com") else host
                    source = f'<p class="source-note">Also published on <a href="{esc(it["source"])}">{esc(where)}</a>.</p>'
                signup_text = {
                    "notes": ("Get Five Notes in your inbox every Friday.", "Get Five Notes"),
                    "articles": ("Get new Articles and Five Notes every Friday.", "Get Five Notes"),
                    "newsletter": ("Get the next issue in your inbox on Friday.", "Try it"),
                }[kind]
                kicker = KINDS[kind]["label"]
                kicker_link = L(KINDS[kind]["dir"] + "/")
                content = fill(template("item.html"), L, {
                    "kind": kind, "kicker": f'<a href="{kicker_link}">{title_html(kicker)}</a>', "title": title_html(it["title"]),
                    "subtitle": f'<p class="item-subtitle">{esc(it["subtitle"])}</p>' if it["subtitle"] else "",
                    "meta": meta, "body": it["body"], "source": source,
                    "signup": self.signup(signup_text[0], signup_text[1], "One email a week. Unsubscribe anytime."),
                    "pager": f'<nav class="pager" aria-label="More {KINDS[kind]["plural"]}">{"".join(nav_links)}</nav>' if nav_links else "",
                    "related": related_html,
                })
                navkey = {"notes": "notes", "articles": "articles", "newsletter": "newsletter"}[kind]
                self.page(f'{it["path"]}index.html', it["title"], content, L, nav=navkey,
                          canonical=it["path"], description=it["excerpt"])

    def build_404(self):
        L = Linker(0, self.preview, absolute=not self.preview)
        content = fill(template("404.html"), L, {})
        self.page("404.html", "Page not found", content, L)

    def build_feeds(self):
        base = CFG["url"].rstrip("/") + "/"
        items = []
        for it in self.all[:50]:
            when = dt.datetime.combine(it["date"], dt.time(12, 0), tzinfo=dt.timezone.utc)
            items.append(
                f"<item><title>{esc(it['title'])}</title><link>{base}{it['path']}</link>"
                f"<guid isPermaLink=\"true\">{base}{it['path']}</guid><pubDate>{format_datetime(when)}</pubDate>"
                f"<category>{esc(KINDS[it['kind']]['label'])}</category>"
                f"<description><![CDATA[{it['body'].replace(']]>', ']]&gt;')}]]></description></item>")
        rss = (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
               f"<title>{esc(CFG['name'])}</title><link>{base}</link><description>{esc(CFG['description'])}</description>"
               f"<language>en</language>{''.join(items)}</channel></rss>")
        (self.out / "feed.xml").write_text(rss, encoding="utf-8")
        pages = ["", "notes/", "articles/", "newsletter/", "app/", "about/"] + \
                [f"topics/{t['slug']}/" for t in TOPICS] + [i["path"] for i in self.all]
        urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in pages)
        (self.out / "sitemap.xml").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
            encoding="utf-8")
        (self.out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}sitemap.xml\n", encoding="utf-8")

    def build(self):
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        shutil.copytree(ROOT / "assets", self.out, dirs_exist_ok=True)
        self.build_home()
        self.build_notes()
        self.build_articles()
        self.build_newsletter()
        self.build_static("app", "app", "Tribes",
                          "Tribes helps families build healthy habits together: one shared goal, a daily check-in and everyone's progress in one place.")
        self.build_static("about", "about", "About", "About Mahd Nishtar, MD.")
        self.build_topics()
        self.build_items()
        self.build_404()
        self.build_feeds()
        if not self.preview:
            (self.out / "CNAME").write_text(CFG["domain"] + "\n", encoding="utf-8")
            (self.out / ".nojekyll").write_text("", encoding="utf-8")
        counts = ", ".join(f"{len(v)} {k}" for k, v in self.items.items())
        print(f"Built {self.out.relative_to(ROOT) if self.out.is_relative_to(ROOT) else self.out}: {counts}")


def main():
    ap = argparse.ArgumentParser(description="Build the site.")
    ap.add_argument("--preview", action="store_true", help="explicit index.html links, forms don't submit")
    ap.add_argument("--examples", action="store_true", help="include sample posts from examples/")
    ap.add_argument("--out", default=None, help="output folder (default _site)")
    a = ap.parse_args()
    out = Path(a.out).resolve() if a.out else ROOT / "_site"
    Builder(out, a.preview, a.examples).build()


if __name__ == "__main__":
    main()
