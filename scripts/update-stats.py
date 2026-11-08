"""Build profile cards from GitHub's public repository and release statistics."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


USERNAME = "JXZ666"
API_URL = "https://api.github.com"
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
PAGE_SIZE = 100


class RejectRedirects(HTTPRedirectHandler):
    """Keep authenticated requests on the explicitly constructed API URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Unexpected GitHub API redirect")


def github_json(path, parameters=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "JXZ666-profile-stats",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token

    url = API_URL + path
    if parameters:
        url += "?" + urlencode(parameters)
    request = Request(url, headers=headers)
    with build_opener(RejectRedirects()).open(request, timeout=30) as response:
        return json.load(response)


def paginate(path, parameters=None):
    page = 1
    while True:
        query = {**(parameters or {}), "per_page": PAGE_SIZE, "page": page}
        items = github_json(path, query)
        if not isinstance(items, list):
            raise ValueError("Expected a GitHub API list")
        yield from items
        if len(items) < PAGE_SIZE:
            return
        page += 1


def nonnegative_integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative GitHub count or identifier")
    return value


def collect_stats():
    repositories = []
    for repository in paginate(f"/users/{USERNAME}/repos", {"type": "owner"}):
        if type(repository["private"]) is not bool or type(repository["fork"]) is not bool:
            raise ValueError("Invalid repository visibility or fork flag")
        if (
            repository["owner"]["login"].casefold() == USERNAME.casefold()
            and not repository["private"]
            and not repository["fork"]
        ):
            repositories.append(repository)

    stars = sum(nonnegative_integer(repo["stargazers_count"]) for repo in repositories)
    commit_search = github_json(
        "/search/commits", {"q": f"author:{USERNAME} is:public", "per_page": 1}
    )
    if commit_search["incomplete_results"] is not False:
        raise ValueError("GitHub commit search returned incomplete results")
    commits = nonnegative_integer(commit_search["total_count"])

    downloads = 0
    for repository in repositories:
        repository_path = f"/repos/{USERNAME}/{quote(repository['name'], safe='')}"
        for release in paginate(repository_path + "/releases"):
            if type(release["draft"]) is not bool:
                raise ValueError("Invalid release draft flag")
            if release["draft"]:
                continue
            release_id = nonnegative_integer(release["id"])
            asset_path = f"{repository_path}/releases/{release_id}/assets"
            for asset in paginate(asset_path):
                downloads += nonnegative_integer(asset["download_count"])

    return {"stars": stars, "commits": commits, "downloads": downloads}


def render_card(stats, theme):
    palettes = {
        "light": {
            "background": "#FCF8FF", "border": "#DACDE7", "title": "#8B1459",
            "text": "#3F2C53", "number": "#432056", "icon": "#067873",
        },
        "dark": {
            "background": "#131020", "border": "#3D3454", "title": "#F9B9E4",
            "text": "#E9E0F6", "number": "#FFFFFF", "icon": "#64E3DE",
        },
    }
    colors = palettes[theme]
    rows = [
        ("获标星数", stats["stars"], '<path d="m8 1.5 2 4.1 4.5.7-3.3 3.2.8 4.5-4-2.1L4 14l.8-4.5-3.3-3.2L6 5.6Z"/>'),
        ("累计公开提交", stats["commits"], '<path d="M1 8h3m8 0h3"/><circle cx="8" cy="8" r="4"/>'),
        ("发布附件下载", stats["downloads"], '<path d="M8 1v9M5 7l3 3 3-3M2 11v4h12v-4"/>'),
    ]
    description = "，".join(f"{label} {value:,}" for label, value, _ in rows)
    rendered_rows = []
    for index, (label, value, icon) in enumerate(rows):
        baseline = 73 + index * 36
        rendered_rows.append(f'''  <g>
    <g transform="translate(24 {baseline - 13})" fill="none" stroke="{colors['icon']}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{icon}</g>
    <text x="52" y="{baseline}" fill="{colors['text']}" font-size="15">{label}</text>
    <text x="324" y="{baseline}" fill="{colors['number']}" font-size="17" font-weight="600" text-anchor="end" font-variant-numeric="tabular-nums">{value:,}</text>
  </g>''')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="350" height="168" viewBox="0 0 350 168" role="img" aria-labelledby="stats-title stats-desc">
  <title id="stats-title">JXZ666 的 GitHub 公开统计</title>
  <desc id="stats-desc">{description}</desc>
  <rect x=".5" y=".5" width="349" height="167" rx="8" fill="{colors['background']}" stroke="{colors['border']}"/>
  <g font-family="'Microsoft YaHei', 'PingFang SC', 'Noto Sans CJK SC', sans-serif">
    <text x="24" y="31" fill="{colors['title']}" font-size="17" font-weight="600">JXZ666 · GitHub 统计</text>
    <path d="M24 45h302" stroke="{colors['border']}"/>
{chr(10).join(rendered_rows)}
  </g>
</svg>
'''


def write_cards(stats, assets_dir):
    cards = {f"stats-{theme}.svg": render_card(stats, theme) for theme in ("light", "dark")}
    assets_dir.mkdir(parents=True, exist_ok=True)
    # Stage both renders before replacing the committed cards.
    with TemporaryDirectory(prefix=".stats-", dir=assets_dir) as temporary_dir:
        for filename, contents in cards.items():
            Path(temporary_dir, filename).write_text(contents, encoding="utf-8", newline="\n")
        for filename in cards:
            Path(temporary_dir, filename).replace(assets_dir / filename)


def main():
    try:
        stats = collect_stats()
        write_cards(stats, ASSETS_DIR)
    except Exception as error:
        # Keep response bodies and authentication headers out of workflow logs.
        reason = f"HTTP {error.code}" if isinstance(error, HTTPError) else type(error).__name__
        print(f"GitHub statistics update failed ({reason}).", file=sys.stderr)
        return 1
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
