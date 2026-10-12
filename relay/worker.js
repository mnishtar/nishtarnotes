/**
 * NISHTAR relay: Eden -> nishtarnotes.com
 *
 * A Cloudflare Worker (free plan) that receives Eden's "post.published"
 * webhook and saves each mini-essay as a Note in the site's GitHub repo.
 * Saving the file triggers the site to rebuild, so the Note is live in
 * about a minute.
 *
 * Rules (from our setup):
 *   - Only posts over MIN_WORDS words become Notes (quick takes and replies stay off the site).
 *   - The first line of the post is the title. The rest is the body.
 *   - The date is the day it was posted, in TIMEZONE.
 *
 * Settings (Cloudflare dashboard > Worker > Settings > Variables and Secrets):
 *   EDEN_SECRET   (secret)  the signing secret Eden shows when you add the webhook
 *   GITHUB_TOKEN  (secret)  a fine-grained GitHub token with "Contents: Read and write" on the site repo
 *   GITHUB_REPO   (text)    e.g. mnishtar/nishtarnotes
 *   GITHUB_BRANCH (text)    optional, defaults to main
 *   MIN_WORDS     (text)    optional, defaults to 100
 *   MAX_WORDS     (text)    optional, defaults to 1000 (longer posts are Articles, imported from Substack)
 *   TIMEZONE      (text)    optional, defaults to America/New_York
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("NISHTAR relay is running. Eden posts arrive here.", { status: 200 });
    }

    const raw = await request.text();

    // 1. Make sure the request really came from Eden.
    const ok = await verifySignature(raw, request.headers, env.EDEN_SECRET);
    if (!ok) return reply({ error: "Signature did not match. Check EDEN_SECRET." }, 401);

    let event;
    try { event = JSON.parse(raw); } catch { return reply({ error: "Body was not JSON." }, 400); }

    // 2. Only published posts, only mini-essays.
    if (event.type !== "post.published") return reply({ skipped: `event ${event.type}` });
    const data = event.data || {};
    const text = String(data.text || "").replace(/\r\n?/g, "\n").trim();
    const words = text.split(/\s+/).filter(Boolean).length;
    const minWords = parseInt(env.MIN_WORDS || "100", 10);
    if (words <= minWords) return reply({ skipped: `${words} words, not a mini-essay` });
    // Long essays and the 1-3-1 newsletter reach the site through Substack
    // (Articles and Newsletter), so they must not also become Notes.
    const maxWords = parseInt(env.MAX_WORDS || "1000", 10);
    if (words > maxWords) return reply({ skipped: `${words} words, an Article (comes from Substack)` });
    if (/^#*\s*1-3-1\b/i.test(text)) return reply({ skipped: "1-3-1 newsletter (comes from Substack)" });

    // 3. First line is the title, the rest is the body.
    const lines = text.split("\n");
    let title = lines.shift().replace(/^#+\s*/, "").trim();
    let body = lines.join("\n").trim();
    if (title.length > 140) {               // a first line that is really a paragraph
      body = title + "\n\n" + body;
      title = title.split(/\s+/).slice(0, 10).join(" ") + "…";
    }
    body = toParagraphs(body);

    // 4. Date in your time zone, and a readable file name.
    const tz = env.TIMEZONE || "America/New_York";
    const when = new Date(data.posted_at || event.created_at || Date.now());
    const date = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(when);
    const path = `content/notes/${date}-${slugify(title)}.md`;

    // 5. Link back to the post, preferring Substack.
    const source = pickSource(data);

    const file =
      "---\n" +
      `title: ${title.replace(/\n/g, " ")}\n` +
      `date: ${date}\n` +
      (source ? `source: ${source}\n` : "") +
      `eden_post_id: ${data.post_id || event.id || ""}\n` +
      "---\n" +
      body + "\n";

    // 6. Save it to GitHub (skip if this Note is already there).
    const repo = env.GITHUB_REPO;
    const branch = env.GITHUB_BRANCH || "main";
    const api = `https://api.github.com/repos/${repo}/contents/${encodeURI(path)}`;
    const headers = {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "nishtar-relay",
    };

    const existing = await fetch(`${api}?ref=${branch}`, { headers });
    if (existing.status === 200) return reply({ skipped: "already saved", path });

    const put = await fetch(api, {
      method: "PUT",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        message: `Note: ${title}`,
        content: toBase64(file),
        branch,
      }),
    });
    if (!put.ok) {
      const detail = await put.text();
      // A non-2xx answer makes Eden retry later (1 min, 5 min, 30 min, ...).
      return reply({ error: `GitHub said ${put.status}`, detail: detail.slice(0, 300) }, 502);
    }
    return reply({ saved: path, words });
  },
};

async function verifySignature(raw, headers, secret) {
  if (!secret) return false;
  const sig = (headers.get("X-Eden-Signature") || "").replace(/^sha256=/, "").trim().toLowerCase();
  const ts = headers.get("X-Eden-Timestamp") || "";
  if (!sig || !ts) return false;
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}.${raw}`));
  const hex = [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("");
  if (hex.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < hex.length; i++) diff |= hex.charCodeAt(i) ^ sig.charCodeAt(i);
  return diff === 0;
}

// Social posts often put each line on its own line. Keep that look:
// every line becomes its own paragraph, except runs of list items.
function toParagraphs(body) {
  const isList = (l) => /^\s*([-*•]|\d+[.)])\s+/.test(l);
  const lines = body.split("\n").map((l) => l.trimEnd()).filter((l, i, a) => l || (a[i - 1] && a[i - 1].trim()));
  let out = "";
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i].replace(/^\s*•\s+/, "- ");
    if (i === 0) { out = l; continue; }
    const glue = isList(lines[i]) && isList(lines[i - 1]) ? "\n" : "\n\n";
    out += l ? glue + l : "";
  }
  return out.replace(/\n{3,}/g, "\n\n").trim();
}

function pickSource(data) {
  const urls = [];
  const p = data.platforms;
  const entries = Array.isArray(p) ? p : p && typeof p === "object" ? Object.values(p) : [];
  for (const e of entries) if (e && e.url) urls.push(e.url);
  if (Array.isArray(data.links)) for (const l of data.links) urls.push(typeof l === "string" ? l : l && l.url);
  if (data.url) urls.push(data.url);
  const clean = urls.filter(Boolean);
  return clean.find((u) => /substack\.com/.test(u)) || clean[0] || "";
}

function slugify(s) {
  return s.toLowerCase().replace(/[’'`]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 70).replace(/-+$/, "") || "note";
}

function toBase64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function reply(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}
