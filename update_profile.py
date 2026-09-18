"""Rebuild profile-card-dark.svg / profile-card-light.svg from live GitHub data.

Runs on a schedule via GitHub Actions. Stdlib only, no third-party deps.
"""
import calendar
import json
import html
import os
import urllib.request
from datetime import date, datetime, timezone

USER = "adikeshri"
BORN = date(1998, 6, 26)
FIRST_YEAR = 2017  # account creation year, used to bound the contribution scan
CARD_W, CARD_H = 780, 440

TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""

MARK = [
    " ╭───╮",
    "│     │",
    " ╰──┬╯",
    "    ╰─",
]

PALETTES = {
    "dark": {
        "bg": "#0b0f19", "border": "#232a3d", "head": "#a78bfa", "name": "#f8fafc",
        "label": "#2dd4bf", "val": "#cbd5e1", "dim": "#3f4863", "tile": "#131a2a",
        "ok": "#4ade80", "bad": "#f87171",
    },
    "light": {
        "bg": "#ffffff", "border": "#e2e8f0", "head": "#7c3aed", "name": "#0f172a",
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


def commit_totals():
    aliases = "\n".join(
        f'y{yr}: contributionsCollection(from: "{yr}-01-01T00:00:00Z", to: "{yr + 1}-01-01T00:00:00Z") '
        "{ totalCommitContributions restrictedContributionsCount }"
        for yr in range(FIRST_YEAR, datetime.now(timezone.utc).year + 1)
    )
    data = gql(f'query {{ user(login: "{USER}") {{ {aliases} }} }}')["user"]
    return sum(v["totalCommitContributions"] + v["restrictedContributionsCount"] for v in data.values())


LOC_QUERY = """
query($name: String!, $id: ID!, $cursor: String) {
  repository(owner: "%s", name: $name) {
    defaultBranchRef { target { ... on Commit {
      history(first: 100, author: { id: $id }, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { additions deletions }
      }
    } } }
  }
}""" % USER


def loc_totals(repo_names, user_id):
    added = removed = 0
    for name in repo_names:
        cursor = None
        try:
            while True:
                ref = gql(LOC_QUERY, {"name": name, "id": user_id, "cursor": cursor})["repository"]["defaultBranchRef"]
                if ref is None:
                    break
                hist = ref["target"]["history"]
                added += sum(n["additions"] for n in hist["nodes"])
                removed += sum(n["deletions"] for n in hist["nodes"])
                if not hist["pageInfo"]["hasNextPage"]:
                    break
                cursor = hist["pageInfo"]["endCursor"]
        except Exception as exc:
            print(f"loc({name}) skipped: {exc}")
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
        repositoriesContributedTo(first: 1, contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) {{
          totalCount
        }}
      }}
    }}""")["user"]
    own_repos = [n["name"] for n in u["repositories"]["nodes"] if not n["isFork"]]
    added, removed = loc_totals(own_repos, u["id"])
    return {
        "repos": u["repositories"]["totalCount"],
        "stars": sum(n["stargazerCount"] for n in u["repositories"]["nodes"]),
        "followers": u["followers"]["totalCount"],
        "contributed": u["repositoriesContributedTo"]["totalCount"],
        "commits": commit_totals(),
        "loc_add": added,
        "loc_del": removed,
        "loc": added - removed,
    }


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


def text(x, y, s, fill, size=13, anchor="start", weight="400"):
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" '
            f'text-anchor="{anchor}" font-weight="{weight}" xml:space="preserve">{html.escape(s)}</text>')


def fit(s, max_px, size=12.5, char_w=0.62):
    max_chars = max(int(max_px / (size * char_w)), 1)
    return s if len(s) <= max_chars else s[: max_chars - 1].rstrip() + "…"


def row(x, y, label, value, p, col_right, label_w=112):
    v = fit(fmt(value), col_right - (x + label_w))
    return (text(x, y, label, p["label"], size=11.5, weight="600")
            + text(x + label_w, y, v, p["val"], size=12.5))


def section(x, y, title, width_chars, p):
    dash = "─" * max(width_chars - len(title) - 1, 1)
    return (text(x, y, title, p["head"], size=11.5, weight="700")
            + text(x + len(title) * 7.6 + 8, y, dash, p["dim"], size=11.5))


def tile(x, y, w, h, value, label, p, accent=None):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{p["tile"]}" stroke="{p["border"]}"/>']
    out.append(text(x + w / 2, y + h * 0.42, fmt(value), accent or p["name"], size=17, anchor="middle", weight="700"))
    out.append(text(x + w / 2, y + h * 0.74, label, p["dim"], size=9.5, anchor="middle", weight="600"))
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

    for i, line in enumerate(MARK):
        out.append(text(30, 30 + i * 13, line, p["head"], size=12.5))
    out.append(text(96, 40, "Aditya Keshri", p["name"], size=18, weight="700"))
    out.append(text(96, 60, "Lead Software Engineer, building agentic systems", p["val"], size=12))
    out.append(f'<line x1="30" y1="78" x2="{CARD_W - 30}" y2="78" stroke="{p["border"]}"/>')

    col1, mid, col2, edge = 34, 382, 404, CARD_W - 30
    yy = 104
    out.append(section(col1, yy, "ABOUT", 40, p))
    rows1 = [
        ("ROLE", "Lead Software Engineer"),
        ("COMPANY", "Asper.ai · Bangalore, India"),
        ("EXPERIENCE", "6+ yrs — UAVs, banking, now AI"),
        ("AGE", f"{y}y {m}m {d}d"),
    ]
    for i, (k, v) in enumerate(rows1):
        out.append(row(col1, yy + 24 + i * 20, k, v, p, mid))
    yy2 = yy + 24 + len(rows1) * 20 + 14
    out.append(section(col1, yy2, "STACK", 40, p))
    rows2 = [
        ("LANGUAGES", "Python, C#, Java, JS/TS, Rust"),
        ("SPOKEN", "English, Hindi"),
        ("INTERESTS", "Fitness, Music, Cricket"),
    ]
    for i, (k, v) in enumerate(rows2):
        out.append(row(col1, yy2 + 24 + i * 20, k, v, p, mid))
    left_end = yy2 + 24 + len(rows2) * 20

    yy = 104
    out.append(section(col2, yy, "BUILDING", 40, p))
    rows3 = [
        ("TACHYON", "Full-text search in Rust, ~8MB"),
        ("VALYRIA", "Offline coding agent runtime"),
    ]
    for i, (k, v) in enumerate(rows3):
        out.append(row(col2, yy + 24 + i * 20, k, v, p, edge))
    yy2 = yy + 24 + len(rows3) * 20 + 14
    out.append(section(col2, yy2, "CONTACT", 40, p))
    rows4 = [
        ("EMAIL", "adikeshri10@gmail.com"),
        ("LINKEDIN", "in/adikeshri"),
        ("SITE", "adityakeshri.com"),
        ("LEETCODE", "adikeshri10"),
    ]
    for i, (k, v) in enumerate(rows4):
        out.append(row(col2, yy2 + 24 + i * 20, k, v, p, edge))
    right_end = yy2 + 24 + len(rows4) * 20

    body_end = max(left_end, right_end) + 16
    out.append(f'<line x1="30" y1="{body_end}" x2="{CARD_W - 30}" y2="{body_end}" stroke="{p["border"]}"/>')

    tiles = [
        ("REPOS", s["repos"], None),
        ("STARS", s["stars"], None),
        ("COMMITS", s["commits"], None),
        ("FOLLOWERS", s["followers"], None),
        ("CONTRIBUTED TO", s["contributed"], None),
        ("LOC (NET)", s["loc"], p["ok"] if isinstance(s["loc"], int) and s["loc"] >= 0 else p["bad"]),
    ]
    tw, gap = 104, 12
    tx = col1
    ty = body_end + 16
    for label, value, accent in tiles:
        out.append(tile(tx, ty, tw, 64, value, label, p, accent))
        tx += tw + gap

    out.append("</svg>")
    return "\n".join(out)


def selfcheck():
    assert years_months_days(date(1998, 6, 26), date(2026, 6, 26)) == (28, 0, 0)
    assert years_months_days(date(1998, 6, 26), date(2026, 6, 25)) == (27, 11, 30)


if __name__ == "__main__":
    selfcheck()
    stats = fetch_stats()
    print("stats:", stats)
    for mode in PALETTES:
        with open(f"profile-card-{mode}.svg", "w", encoding="utf-8") as f:
            f.write(render(mode, stats))
    print("wrote profile-card-dark.svg, profile-card-light.svg")
