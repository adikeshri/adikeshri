"""Rebuild profile-card-dark.svg / profile-card-light.svg from live GitHub data.

Runs on a schedule via GitHub Actions. Stdlib only, no third-party deps.
"""
import calendar
import json
import html
import os
import re
import urllib.request
from datetime import date, datetime, timezone

USER = "adikeshri"
BORN = date(1998, 6, 26)
FIRST_YEAR = 2017  # account creation year, used to bound the contribution scan
CARD_W, CARD_H = 860, 570
TITLE = "aditya@keshri:~/profile"

TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""

# a big block "A" instead of a stock neofetch mascot — mostly plain ASCII
# (no box-drawing glyphs) so it can't get mangled by an unexpected charset.
ART = [
    r"        /\        ",
    r"       /  \       ",
    r"      / /\ \      ",
    r"     / /  \ \     ",
    r"    / /----\ \    ",
    r"   / /      \ \   ",
    r"  /_/        \_\  ",
    r"                   ",
    r"    A . KESHRI     ",
]

TRAFFIC_LIGHTS = ["#ff5f56", "#ffbd2e", "#27c93f"]

PALETTES = {
    "dark": {
        "bg": "#0a0e16", "border": "#232a3d", "chrome": "#0d1220", "art": "#a78bfa",
        "prompt": "#a78bfa", "comment": "#5fa88a", "name": "#f8fafc",
        "label": "#2dd4bf", "val": "#cbd5e1", "dim": "#3f4863", "tile": "#131a2a",
        "ok": "#4ade80", "bad": "#f87171",
    },
    "light": {
        "bg": "#ffffff", "border": "#e2e8f0", "chrome": "#f6f8fa", "art": "#7c3aed",
        "prompt": "#7c3aed", "comment": "#3f7d5e", "name": "#0f172a",
        "label": "#0f766e", "val": "#334155", "dim": "#cbd5e1", "tile": "#f8fafc",
        "ok": "#15803d", "bad": "#b91c1c",
    },
}


def call(url, payload=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "{}")


def gql(query, variables=None):
    resp = call("https://api.github.com/graphql", {"query": query, "variables": variables or {}})
    if resp.get("errors"):
        raise RuntimeError(resp["errors"])
    return resp["data"]


def years_months_days(start, end):
    months = (end.year - start.year) * 12 + (end.month - start.month)
    days = end.day - start.day
    if days < 0:
        months -= 1
        pm_year, pm_month = (end.year, end.month - 1) if end.month > 1 else (end.year - 1, 12)
        days += calendar.monthrange(pm_year, pm_month)[1]
    years, months = divmod(months, 12)
    return years, months, days


def contribution_totals():
    # One contributionsCollection per year (GitHub caps each window at a year),
    # aliased so they all come back in a single request.
    aliases = "\n".join(
        f'y{yr}: contributionsCollection(from: "{yr}-01-01T00:00:00Z", to: "{yr + 1}-01-01T00:00:00Z") {{ '
        "totalCommitContributions totalIssueContributions totalPullRequestContributions "
        "totalPullRequestReviewContributions restrictedContributionsCount }"
        for yr in range(FIRST_YEAR, datetime.now(timezone.utc).year + 1)
    )
    data = gql(f'query {{ user(login: "{USER}") {{ {aliases} }} }}')["user"]
    commits = sum(v["totalCommitContributions"] + v["restrictedContributionsCount"] for v in data.values())
    contributions = sum(
        v["totalCommitContributions"] + v["totalIssueContributions"]
        + v["totalPullRequestContributions"] + v["totalPullRequestReviewContributions"]
        + v["restrictedContributionsCount"]
        for v in data.values()
    )
    return commits, contributions


LOC_QUERY = """
query($owner: String!, $name: String!, $id: ID!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { target { ... on Commit {
      history(first: 100, author: { id: $id }, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { additions deletions }
      }
    } } }
  }
}"""


def loc_totals(repos, user_id):
    # repos: iterable of (owner, name) — own repos plus repos contributed to
    # under other accounts/orgs, so a PR merged into someone else's project
    # counts toward LOC the same way an own-repo commit does.
    added = removed = 0
    for owner, name in repos:
        cursor = None
        try:
            while True:
                ref = gql(LOC_QUERY, {"owner": owner, "name": name, "id": user_id, "cursor": cursor})["repository"]["defaultBranchRef"]
                if ref is None:
                    break
                hist = ref["target"]["history"]
                added += sum(n["additions"] for n in hist["nodes"])
                removed += sum(n["deletions"] for n in hist["nodes"])
                if not hist["pageInfo"]["hasNextPage"]:
                    break
                cursor = hist["pageInfo"]["endCursor"]
        except Exception as exc:
            print(f"loc({owner}/{name}) skipped: {exc}")
    return added, removed


def fetch_stats():
    u = gql(f"""
    query {{
      user(login: "{USER}") {{
        id
        followers {{ totalCount }}
        repositories(first: 100, ownerAffiliations: OWNER) {{
          totalCount
          nodes {{ name stargazerCount isFork }}
        }}
        repositoriesContributedTo(first: 100, contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) {{
          totalCount
          nodes {{ name owner {{ login }} }}
        }}
      }}
    }}""")["user"]
    own_repos = [(USER, n["name"]) for n in u["repositories"]["nodes"] if not n["isFork"]]
    org_repos = [(n["owner"]["login"], n["name"]) for n in u["repositoriesContributedTo"]["nodes"]]
    added, removed = loc_totals(own_repos + org_repos, u["id"])
    commits, contributions = contribution_totals()
    return {
        "repos": u["repositories"]["totalCount"],
        "stars": sum(n["stargazerCount"] for n in u["repositories"]["nodes"]),
        "followers": u["followers"]["totalCount"],
        "contributed": u["repositoriesContributedTo"]["totalCount"],
        "commits": commits,
        "contributions": contributions,
        "loc_add": added,
        "loc_del": removed,
        "loc": added - removed,
    }


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


def text(x, y, s, fill, size=13, anchor="start", weight="400"):
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" '
            f'text-anchor="{anchor}" font-weight="{weight}" xml:space="preserve">{html.escape(s)}</text>')


def fit_chars(s, max_chars):
    return s if len(s) <= max_chars else s[: max_chars - 1].rstrip() + "…"


def spans(x, y, parts, size=13):
    # parts: (text, fill, font-weight) tuples rendered as one monospace line,
    # so a dot-leader's column math only has to count characters.
    body = "".join(f'<tspan fill="{fill}" font-weight="{w}">{html.escape(t)}</tspan>' for t, fill, w in parts)
    return f'<text x="{x}" y="{y}" font-size="{size}" xml:space="preserve">{body}</text>'


def comment(x, y, title, p):
    return spans(x, y, [(f"# {title}", p["comment"], "600")], size=12.5)


def kv(x, y, key, value, width, p):
    value = fit_chars(fmt(value), max(width - len(key) - 4, 4))
    dots = "." * max(width - len(key) - len(value) - 3, 1)
    return spans(x, y, [
        (f"{key} ", p["label"], "600"),
        (dots + " ", p["dim"], "400"),
        (value, p["val"], "400"),
    ])


def tile(x, y, w, h, value, label, p, accent=None):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{p["tile"]}" stroke="{p["border"]}"/>']
    out.append(text(x + w / 2, y + h * 0.42, fmt(value), accent or p["name"], size=17, anchor="middle", weight="700"))
    out.append(text(x + w / 2, y + h * 0.74, label, p["dim"], size=9.5, anchor="middle", weight="600"))
    return "".join(out)


def loc_tile(x, y, w, h, added, removed, p):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{p["tile"]}" stroke="{p["border"]}"/>']
    cx = x + w / 2
    out.append(text(cx, y + h * 0.32, f"{fmt(added)}++", p["ok"], size=12, anchor="middle", weight="700"))
    out.append(text(cx, y + h * 0.58, f"{fmt(removed)}--", p["bad"], size=12, anchor="middle", weight="700"))
    out.append(text(cx, y + h * 0.85, "LINES OF CODE", p["dim"], size=8.5, anchor="middle", weight="600"))
    return "".join(out)


def render(mode, s):
    p = PALETTES[mode]
    y, m, d = years_months_days(BORN, date.today())
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_W}" height="{CARD_H}" '
        f'viewBox="0 0 {CARD_W} {CARD_H}" font-family="Menlo, Consolas, monospace">',
        f'<rect x="0.5" y="0.5" width="{CARD_W - 1}" height="{CARD_H - 1}" rx="12" '
        f'fill="{p["bg"]}" stroke="{p["border"]}"/>',
    ]

    # title bar chrome
    out.append(f'<rect x="0.5" y="0.5" width="{CARD_W - 1}" height="30" rx="12" fill="{p["chrome"]}"/>')
    out.append(f'<rect x="0.5" y="18.5" width="{CARD_W - 1}" height="12" fill="{p["chrome"]}"/>')
    for i, color in enumerate(TRAFFIC_LIGHTS):
        out.append(f'<circle cx="{22 + i * 18}" cy="15" r="6" fill="{color}"/>')
    out.append(text(CARD_W / 2, 19.5, TITLE, p["dim"], size=12, anchor="middle"))
    out.append(f'<line x1="0" y1="31" x2="{CARD_W}" y2="31" stroke="{p["border"]}"/>')

    margin, cy = 28, 56
    out.append(spans(margin, cy, [("$ ", p["prompt"], "700"), ("whoami", p["name"], "400")]))
    cy += 20
    out.append(text(margin + 14, cy, "Aditya Keshri — Lead Software Engineer, building agentic systems", p["val"], size=12.5))
    cy += 18
    out.append(f'<line x1="{margin}" y1="{cy}" x2="{CARD_W - margin}" y2="{cy}" stroke="{p["border"]}"/>')

    body_top = cy + 30
    art_x = margin
    for i, line in enumerate(ART):
        out.append(text(art_x, body_top + i * 16, line, p["art"], size=13, weight="600"))

    info_x, info_edge = art_x + 220, CARD_W - margin
    info_w = int((info_edge - info_x) / 7.6)  # ~character budget at 13px monospace
    cy = body_top
    sections = [
        ("about", [
            ("ROLE", "Lead Software Engineer"),
            ("COMPANY", "Asper.ai · Bangalore, India"),
            ("AGE", f"{y}y {m}m {d}d"),
        ]),
        ("stack", [
            ("LANGUAGES", "Python, C#, Java, JavaScript/TypeScript, Rust"),
            ("SPOKEN", "English, Hindi"),
        ]),
        ("building", [
            ("TACHYON", "a full-text search engine in Rust, ~8MB binary"),
            ("VALYRIA", "a local-first, offline coding agent runtime"),
        ]),
        ("contact", [
            ("EMAIL", "adikeshri10@gmail.com"),
            ("LINKEDIN", "in/adikeshri"),
            ("SITE", "adityakeshri.com"),
        ]),
    ]
    for title, rows in sections:
        out.append(comment(info_x, cy, title, p))
        cy += 20
        for k, v in rows:
            out.append(kv(info_x, cy, k, v, info_w, p))
            cy += 20
        cy += 10
    body_end = max(cy - 10, body_top + len(ART) * 16)

    footer_y = body_end + 14
    out.append(f'<line x1="{margin}" y1="{footer_y}" x2="{CARD_W - margin}" y2="{footer_y}" stroke="{p["border"]}"/>')
    footer_y += 24
    out.append(spans(margin, footer_y, [("$ ", p["prompt"], "700"), ("gh stats --live", p["name"], "400")]))

    tiles = [
        ("REPOS", s["repos"]),
        ("STARS", s["stars"]),
        ("COMMITS", s["commits"]),
        ("CONTRIBUTIONS", s["contributions"]),
        ("FOLLOWERS", s["followers"]),
    ]
    tw, gap = 122, 14
    tx, ty = margin, footer_y + 14
    for label, value in tiles:
        out.append(tile(tx, ty, tw, 64, value, label, p))
        tx += tw + gap
    out.append(loc_tile(tx, ty, tw, 64, s["loc_add"], s["loc_del"], p))

    out.append("</svg>")
    return "\n".join(out)


def selfcheck():
    assert years_months_days(date(1998, 6, 26), date(2026, 6, 26)) == (28, 0, 0)
    assert years_months_days(date(1998, 6, 26), date(2026, 6, 25)) == (27, 11, 30)


def bust_readme_cache():
    # GitHub's CDN and browsers cache profile-card-*.svg by URL, so a same-name
    # overwrite can serve a stale image until this query param changes.
    stamp = int(datetime.now(timezone.utc).timestamp())
    with open("README.md", encoding="utf-8") as f:
        readme = f.read()
    updated = re.sub(r"(profile-card-(?:dark|light)\.svg)\?v=\d+", rf"\1?v={stamp}", readme)
    if updated != readme:
        with open("README.md", "w", encoding="utf-8") as f:
            f.write(updated)


if __name__ == "__main__":
    selfcheck()
    stats = fetch_stats()
    print("stats:", stats)
    for mode in PALETTES:
        with open(f"profile-card-{mode}.svg", "w", encoding="utf-8") as f:
            f.write(render(mode, stats))
    bust_readme_cache()
    print("wrote profile-card-dark.svg, profile-card-light.svg, bumped README cache-bust")
