from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


README_PATH = Path("README.md")
START_MARKER = "<!-- MERGED-PRS:START -->"
END_MARKER = "<!-- MERGED-PRS:END -->"
API_ROOT = "https://api.github.com"


def request_json(url: str, token: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "profile-merged-pr-updater",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def repository_from_url(url: str) -> str:
    match = re.search(r"/repos/([^/]+/[^/]+)$", url)
    if not match:
        return ""
    return match.group(1)


def format_stars(stars: int | None) -> str:
    if stars is None:
        return ""
    if stars < 100:
        return ""
    if stars >= 1_000_000:
        value = f"{stars / 1_000_000:.1f}m"
    elif stars >= 1_000:
        value = f"{stars / 1_000:.1f}k"
    else:
        value = str(stars)
    return value.replace(".0k", "k").replace(".0m", "m")


def clean_pr_title(title: str) -> str:
    clean = re.sub(
        r"^(feat|fix|test|tests|docs|doc|refactor|perf|ci|chore|build)(\([^)]+\))?!?:\s*",
        "",
        title.strip(),
        flags=re.IGNORECASE,
    )
    return clean[:1].upper() + clean[1:] if clean else title.strip()


def improvement_summary(title: str) -> str:
    lower = title.lower()
    clean = clean_pr_title(title)
    if "frontmatter" in lower or "metadata" in lower:
        aspect = "Metadata extraction"
    elif "proxy" in lower:
        aspect = "Network config robustness"
    elif "timeout" in lower:
        aspect = "Configuration resilience"
    elif "paginate" in lower or "pagination" in lower:
        aspect = "API pagination"
    elif "fallback" in lower or "retry" in lower:
        aspect = "Fallback reliability"
    elif "non-finite" in lower or "strict json" in lower:
        aspect = "Data serialization"
    elif "end date" in lower:
        aspect = "Data loading correctness"
    elif "drawdown" in lower:
        aspect = "Risk metric correctness"
    elif "symbol market" in lower or "market" in lower:
        aspect = "Market-aware routing"
    elif "stt" in lower or "frame task" in lower:
        aspect = "Streaming task cleanup"
    elif re.match(r"^test(s)?(\([^)]+\))?:", lower):
        aspect = "Test coverage"
    elif re.match(r"^feat(\([^)]+\))?:", lower):
        aspect = "Feature work"
    elif re.match(r"^fix(\([^)]+\))?:", lower) or lower.startswith("fix "):
        aspect = "Bug fix"
    elif re.match(r"^docs?(\([^)]+\))?:", lower):
        aspect = "Documentation"
    elif re.match(r"^refactor(\([^)]+\))?:", lower):
        aspect = "Refactor"
    else:
        aspect = "Project improvement"
    return f"{aspect}: {clean}"


def fetch_repo_meta(repo: str, token: str, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if repo not in cache:
        cache[repo] = request_json(f"{API_ROOT}/repos/{repo}", token)
    return cache[repo]


def fetch_merged_prs(username: str, token: str, limit: int) -> list[dict[str, str]]:
    query = f"is:pr is:merged author:{username} archived:false"
    params = urlencode({"q": query, "sort": "updated", "order": "desc", "per_page": min(max(limit * 2, 30), 100)})
    data = request_json(f"{API_ROOT}/search/issues?{params}", token)

    rows: list[dict[str, str]] = []
    repo_cache: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []):
        repo = repository_from_url(str(item.get("repository_url", "")))
        number = item.get("number")
        if not repo or not number:
            continue
        pr = request_json(f"{API_ROOT}/repos/{repo}/pulls/{number}", token)
        merged_at = str(pr.get("merged_at") or item.get("closed_at") or "")[:10]
        if not merged_at:
            continue
        meta = fetch_repo_meta(repo, token, repo_cache)
        if meta.get("private"):
            continue
        rows.append(
            {
                "repo": repo,
                "repo_url": f"https://github.com/{repo}",
                "stars": format_stars(meta.get("stargazers_count")),
                "number": str(number),
                "title": str(item.get("title", "")),
                "improvement": improvement_summary(str(item.get("title", ""))),
                "url": str(item.get("html_url", "")),
                "merged_at": merged_at,
            }
        )

    rows.sort(key=lambda row: (row["merged_at"], row["repo"], row["number"]), reverse=True)
    return rows[:limit]


def render_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "_No merged pull requests found yet._"

    lines = [
        "| Project | Stars | PR | Improvement | Merged |",
        "|---|---:|---|---|---|",
    ]
    for row in rows:
        repo = markdown_escape(row["repo"])
        improvement = markdown_escape(row["improvement"])
        lines.append(
            f"| [`{repo}`]({row['repo_url']}) | {row['stars']} | [#{row['number']}]({row['url']}) | {improvement} | {row['merged_at']} |"
        )
    return "\n".join(lines)


def replace_section(readme: str, replacement: str) -> str:
    if START_MARKER not in readme or END_MARKER not in readme:
        readme = readme.rstrip() + f"\n\n## Merged Pull Requests\n\n{START_MARKER}\n{END_MARKER}\n"
    start = readme.index(START_MARKER) + len(START_MARKER)
    end = readme.index(END_MARKER)
    return readme[:start] + "\n" + replacement.strip() + "\n" + readme[end:]


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    username = os.environ.get("GITHUB_USERNAME", os.environ.get("GITHUB_REPOSITORY_OWNER", "")).strip()
    limit = int(os.environ.get("MAX_MERGED_PRS", "30"))
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    if not username:
        print("GITHUB_USERNAME or GITHUB_REPOSITORY_OWNER is required", file=sys.stderr)
        return 2

    readme = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else ""
    rows = fetch_merged_prs(username=username, token=token, limit=limit)
    updated = replace_section(readme, render_table(rows))
    README_PATH.write_text(updated, encoding="utf-8", newline="\n")
    print(f"Updated {README_PATH} with {len(rows)} merged PRs for {username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
