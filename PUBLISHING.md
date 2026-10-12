# nishtarnotes.com: how it works and how to set it up

## How publishing works once it's set up

| What | Where you write it | How it reaches the site |
|---|---|---|
| **Note** (most days) | Eden, as you do now | Any Eden post over 100 words becomes a Note. Its first line is the title. Live in about 2 minutes. |
| **1-3-1** (Sundays) | Substack | Give it a title that starts with `1-3-1:`. The site picks it up within 3 hours and files it under Newsletter. |
| **Article** (monthly) | Substack | Any other Substack post. The site picks it up within 3 hours and files it under Articles. |

Each piece is filed under a topic (Great Work, Attention, Meditation, Sleep, Exercise, Diet) based on its words. To pick the topic yourself, add a line like `topic: Sleep` to the top of the file.

Everything you publish is saved as a plain text file in the `content/` folder. Those files are the site. They will open in any text editor in 2036.

---

## One-time setup (about 45 minutes)

### Part A: Put the site on GitHub (10 min)

1. Sign in at github.com as **mnishtar**. Click **New repository**, name it `nishtarnotes`, set it to **Public**, and create it.
2. Upload everything in this folder except `_site` and `_archive-v1`. The easiest way is **GitHub Desktop** (desktop.github.com): *File → Add local repository*, choose this folder, then *Publish*. If you upload in the browser instead, press **Cmd+Shift+.** in Finder first so the hidden `.github` folder shows up, and include it.
   - Or ask Claude to push it for you.
3. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. **Settings → Actions → General → Workflow permissions → Read and write permissions → Save.**
5. **Actions tab → Publish site → Run workflow.** After about a minute the site is live at `https://mnishtar.github.io/nishtarnotes/`.

### Part B: Point nishtarnotes.com at it (10 min, then up to 24 hours to take effect)

1. Namecheap → **Domain List → nishtarnotes.com → Manage → Advanced DNS**.
2. Delete the parking records Namecheap added (usually a `CNAME www → parkingpage.namecheap.com` and a `URL Redirect @`).
3. Add these records:

   | Type | Host | Value |
   |---|---|---|
   | A Record | @ | 185.199.108.153 |
   | A Record | @ | 185.199.109.153 |
   | A Record | @ | 185.199.110.153 |
   | A Record | @ | 185.199.111.153 |
   | CNAME Record | www | mnishtar.github.io. |

4. Back on GitHub: **Settings → Pages → Custom domain → `nishtarnotes.com` → Save.** When the DNS check turns green, tick **Enforce HTTPS**.

### Part C: The relay that carries Notes from Eden (15 min)

The relay is a small free program on Cloudflare. Eden tells it whenever a post goes out, and it saves the mini-essays to GitHub.

1. Create a free account at cloudflare.com.
2. **Workers & Pages → Create → Create Worker.** Name it `nishtar-relay` and click **Deploy**.
3. Click **Edit code**, delete what's there, paste everything from `relay/worker.js`, and click **Deploy**.
4. Make a GitHub key for the relay: github.com → your photo → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
   - Repository access: **Only select repositories → nishtarnotes**
   - Permissions: **Contents → Read and write**
   - Expiration: the longest offered. Put a reminder in your calendar to make a new one before it expires.
   - Copy the token.
5. In Cloudflare: **nishtar-relay → Settings → Variables and Secrets**, and add:
   - `GITHUB_TOKEN`, type **Secret**: the token from step 4
   - `GITHUB_REPO`, type **Text**: `mnishtar/nishtarnotes`
6. Copy the relay's address from the top of the page. It looks like `https://nishtar-relay.yourname.workers.dev`.

### Part D: Connect Eden (3 min, needs Eden Starter plan or above)

1. Eden → **Settings → API → Webhooks → Add endpoint**.
2. Paste the relay address from Part C, step 6.
3. Choose the event **post.published**.
4. Copy the signing secret. In Cloudflare, add it as `EDEN_SECRET`, type **Secret**.
5. Test it: publish a mini-essay from Eden. Within a couple of minutes, a new file appears in `content/notes/` on GitHub and the Note appears on the site.

### Part E: Check the signup form (2 min)

Once the site is live, type your own email into any signup box. You should get Substack's confirmation email. If it doesn't arrive, tell Claude to switch the form to Substack's built-in signup box.

---

## Your routine

- **Daily:** post in Eden. Nothing else to do.
- **Sunday:** send the 1-3-1 from Substack with a title starting `1-3-1:`.
- **Monthly:** publish the Article on Substack.
- **Monthly:** export your subscriber list from Substack (**Dashboard → Subscribers → Export**) and keep the file. Your list is yours wherever you go.

## When something goes wrong

- **A Note didn't appear.** It needs more than 100 words. Check GitHub → `content/notes/`. If the file is there, check the **Actions** tab for a red ✗ and open it to see why. If the file isn't there, Eden retries for a few days. If every try fails for 3 days, Eden turns the webhook off and emails you. The usual cause is an expired GitHub token (Part C, step 4).
- **Post something by hand.** On GitHub, open `content/notes/` (or `articles/`, `newsletter/`), click **Add file → Create new file**, paste a template from `drafts/`, and commit. Or ask Claude.
- **Fix a typo in a Note.** Open the file on GitHub, click the pencil icon, edit, and commit. The site rebuilds itself.
- **Hide something.** Delete its file, or rename it to start with `_`.

## Changing the site

- Page text (home, About, App, Newsletter): `templates/*.html`
- Links, topics, launch message, the Substack import start date: `site.json`
- Colors and fonts: `assets/styles.css`
- Preview on your computer: run `python3 serve.py` and open http://localhost:8080
- Sample posts for trying out the design are in `examples/`. They never appear on the real site.

## Built to last

The site is plain HTML built by one Python file that uses no add-on packages. If GitHub Pages ever goes away, run `python3 build.py` and upload the `_site` folder to any web host. Eden, Cloudflare and Substack only carry posts in. Your writing lives in `content/`.
