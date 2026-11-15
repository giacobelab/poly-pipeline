#!/usr/bin/env python3
"""Write the GitHub Pages landing page listing every lab that has a site.

  make-landing.py <public-dir>

<public-dir> holds one folder per lab (public/<lab>/index.html + build.json);
this writes public/index.html linking to each, in polyci.json order.
"""
import html, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
public = Path(sys.argv[1])
labs = json.loads((ROOT / "polyci.json").read_text())["labs"]

cards = []
for lab_id, lab in labs.items():
    build = public / lab_id / "build.json"
    if not build.exists():
        continue
    info = json.loads(build.read_text())
    cards.append(f'''    <a class="card" href="{html.escape(lab_id)}/">
      <h2>{html.escape(lab.get("title", lab_id))}</h2>
      <p>{f'{info["levels"]} levels &middot; ' if info.get("levels") else ""}{html.escape(info.get("label", ""))}</p>
      <span class="go">Start &rarr;</span>
    </a>''')

page = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PolyLinux labs</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%230b0d10'/%3E%3Ctext x='5' y='23' font-family='monospace' font-size='18' fill='%234fb3ff'%3E%3E_%3C/text%3E%3C/svg%3E">
<style>
  :root {{ --bg: #0b0d10; --panel: #14181d; --text: #d7dde4; --muted: #8b96a3; --accent: #4fb3ff; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; background: var(--bg); color: var(--text);
    font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ max-width: 860px; margin: 0 auto; padding: 56px 20px; }}
  h1 {{ margin: 0 0 6px; font-size: 28px; font-weight: 650; }}
  h1 span {{ color: var(--accent); }}
  p.lead {{ margin: 0 0 32px; color: var(--muted); }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }}
  a.card {{ display: block; padding: 20px; border-radius: 10px; background: var(--panel);
    border: 1px solid #222a33; color: inherit; text-decoration: none; }}
  a.card:hover {{ border-color: var(--accent); }}
  a.card h2 {{ margin: 0 0 6px; font-size: 19px; }}
  a.card p {{ margin: 0; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }}
  a.card .go {{ display: inline-block; margin-top: 14px; color: var(--accent); font-size: 14px; }}
  footer {{ margin-top: 40px; color: #5d6875; font-size: 12px; }}
  footer a {{ color: inherit; }}
</style>
</head>
<body>
<main>
  <h1>Poly<span>Linux</span> labs</h1>
  <p class="lead">Linux command-line labs that run entirely in your browser.
    Pick the one your instructor assigned.</p>
  <div class="cards">
{chr(10).join(cards) if cards else "    <p>No labs published yet.</p>"}
  </div>
  <footer>Runs on <a href="https://github.com/copy/v86">v86</a>. Nothing is sent to a server;
    reloading a lab page starts over.</footer>
</main>
</body>
</html>
'''
(public / "index.html").write_text(page)
print(f"landing page lists {len(cards)} lab(s)")
