#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

NEW_FIELDS = [
    "body",
    "has_linked_issue",
    "linked_issue_count",
    "issue_is_self_filed",
    "linked_issue_authors",
    "author_max_prs_same_day",
    "author_median_interval_hours",
    "author_prs_per_day",
    "author_unique_repos",
    "author_account_age_days",
    "author_followers",
    "author_public_repos",
]


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def get_login(pr: dict[str, Any]) -> str:
    user = pr.get("user")
    if isinstance(user, dict):
        return str(user.get("login") or "")
    if isinstance(user, str):
        return user
    return ""


def is_enriched(pr: dict[str, Any]) -> bool:
    return all(field in pr for field in NEW_FIELDS)


def gh_api_json(args: list[str], retries: int = 3, sleep_seconds: int = 60) -> dict[str, Any]:
    last_err = ""
    for attempt in range(1, retries + 1):
        proc = subprocess.run(
            ["gh", "api", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError as e:
                last_err = f"JSON decode failed: {e}"
        else:
            last_err = (proc.stderr or proc.stdout or "").strip() or f"gh exited {proc.returncode}"

        if attempt < retries:
            print(f"gh api failed (attempt {attempt}/{retries}): {last_err}; sleeping {sleep_seconds}s")
            time.sleep(sleep_seconds)

    raise RuntimeError(f"gh api failed after {retries} retries: {last_err}")


def infer_owner_repo() -> tuple[str, str]:
    proc = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        check=False,
    )
    url = (proc.stdout or "").strip()
    if not url:
        raise RuntimeError("Could not infer repo from git remote.origin.url; provide --owner and --repo")

    cleaned = url
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if cleaned.startswith("git@github.com:"):
        cleaned = cleaned.split("git@github.com:", 1)[1]
    elif "github.com/" in cleaned:
        cleaned = cleaned.split("github.com/", 1)[1]
    else:
        raise RuntimeError(f"Unrecognized GitHub remote URL format: {url}")

    parts = cleaned.strip("/").split("/")
    if len(parts) < 2:
        raise RuntimeError(f"Could not parse owner/repo from remote URL: {url}")
    return parts[-2], parts[-1]


def build_prs_graphql_query(owner: str, repo: str, numbers: list[int]) -> str:
    fields = []
    for idx, number in enumerate(numbers):
        fields.append(
            f"""
      pr{idx}: pullRequest(number: {number}) {{
        body
        closingIssuesReferences(first: 100) {{
          totalCount
          nodes {{
            author {{
              login
            }}
          }}
        }}
      }}"""
        )
    return f"""
query {{
  repository(owner: "{owner}", name: "{repo}") {{{"".join(fields)}
  }}
}}"""


def fetch_pr_batch_graphql(owner: str, repo: str, numbers: list[int]) -> dict[int, dict[str, Any]]:
    query = build_prs_graphql_query(owner, repo, numbers)
    payload = gh_api_json(["graphql", "-f", f"query={query}"])
    repo_data = payload.get("data", {}).get("repository")
    if not isinstance(repo_data, dict):
        raise RuntimeError("GraphQL response missing data.repository")

    out: dict[int, dict[str, Any]] = {}
    for idx, number in enumerate(numbers):
        pr_data = repo_data.get(f"pr{idx}")
        if pr_data is None:
            out[number] = {}
        elif isinstance(pr_data, dict):
            out[number] = pr_data
        else:
            out[number] = {}
    return out


def fetch_user_metadata(login: str) -> dict[str, Any]:
    if not login:
        return {"account_age_days": None, "followers": None, "public_repos": None}
    try:
        payload = gh_api_json([f"users/{login}"])
    except RuntimeError:
        return {"account_age_days": None, "followers": None, "public_repos": None}
    created_at = parse_iso(payload.get("created_at"))
    now = datetime.now(timezone.utc)
    age_days = None if created_at is None else max(0, (now - created_at).days)
    return {
        "account_age_days": age_days,
        "followers": payload.get("followers"),
        "public_repos": payload.get("public_repos"),
    }


def fetch_user_events_unique_repos(login: str) -> int | None:
    if not login:
        return None
    try:
        payload = gh_api_json([f"users/{login}/events"])
    except RuntimeError:
        return None
    if not isinstance(payload, list):
        return None

    repos = set()
    for event in payload:
        if not isinstance(event, dict):
            continue
        repo = event.get("repo")
        if isinstance(repo, dict):
            name = repo.get("name")
            if isinstance(name, str) and name:
                repos.add(name)
    return len(repos)


def compute_author_velocity(prs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_author: dict[str, list[datetime]] = defaultdict(list)
    day_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for pr in prs:
        login = get_login(pr)
        created_dt = parse_iso(pr.get("created_at"))
        if not login or created_dt is None:
            continue
        by_author[login].append(created_dt)
        day_counts[login][created_dt.date().isoformat()] += 1

    velocity: dict[str, dict[str, Any]] = {}
    for login, times in by_author.items():
        sorted_times = sorted(times)
        intervals = []
        for i in range(1, len(sorted_times)):
            delta_hours = (sorted_times[i] - sorted_times[i - 1]).total_seconds() / 3600.0
            intervals.append(delta_hours)

        unique_days = len(day_counts[login]) or 1
        velocity[login] = {
            "author_max_prs_same_day": max(day_counts[login].values()) if day_counts[login] else 0,
            "author_median_interval_hours": float(median(intervals)) if intervals else None,
            "author_prs_per_day": len(sorted_times) / unique_days,
        }
    return velocity


def load_json_array(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise RuntimeError(f"Expected JSON array in {path}")
    return data


def save_output(path: Path, prs: list[dict[str, Any]]) -> None:
    with path.open("w") as f:
        json.dump(prs, f, indent=2)


def apply_graphql_issue_fields(pr: dict[str, Any], pr_gql: dict[str, Any]) -> None:
    body = pr_gql.get("body")
    if not isinstance(body, str):
        body = ""

    issue_data = pr_gql.get("closingIssuesReferences")
    if not isinstance(issue_data, dict):
        issue_data = {}
    total_count = issue_data.get("totalCount")
    if not isinstance(total_count, int):
        total_count = 0

    authors: list[str] = []
    nodes = issue_data.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            author = node.get("author")
            if isinstance(author, dict):
                login = author.get("login")
                if isinstance(login, str) and login:
                    authors.append(login)

    pr_author = get_login(pr)
    unique_authors = sorted(set(authors))
    pr["body"] = body
    pr["has_linked_issue"] = total_count > 0
    pr["linked_issue_count"] = total_count
    pr["linked_issue_authors"] = unique_authors
    pr["issue_is_self_filed"] = bool(pr_author and pr_author in unique_authors)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DATA / "all_historical_prs_enriched.json")
    ap.add_argument("--output", type=Path, default=DATA / "all_historical_prs_enriched_v2.json")
    ap.add_argument("--owner", type=str, default=None)
    ap.add_argument("--repo", type=str, default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base_prs = load_json_array(args.input)
    if args.dry_run:
        base_prs = base_prs[:10]

    if args.owner and args.repo:
        owner, repo = args.owner, args.repo
    else:
        owner, repo = infer_owner_repo()

    print(f"Using repository: {owner}/{repo}")
    print("Precomputing author velocity stats from dataset...")
    author_velocity = compute_author_velocity(base_prs)

    working_prs = [dict(pr) for pr in base_prs]

    if args.resume and args.output.exists():
        existing = load_json_array(args.output)
        existing_by_number = {int(pr.get("number")): pr for pr in existing if "number" in pr}
        replaced = 0
        for idx, pr in enumerate(working_prs):
            number = int(pr["number"])
            prior = existing_by_number.get(number)
            if isinstance(prior, dict) and is_enriched(prior):
                working_prs[idx] = prior
                replaced += 1
        print(f"Resume enabled: reusing {replaced} enriched PRs from {args.output}")

    user_meta_cache: dict[str, dict[str, Any]] = {}
    user_repo_spread_cache: dict[str, int | None] = {}

    processed = 0
    to_process_numbers = [int(pr["number"]) for pr in working_prs if not is_enriched(pr)]

    total_to_process = len(to_process_numbers)
    print(f"PRs requiring enrichment: {total_to_process} / {len(working_prs)}")

    number_to_index = {int(pr["number"]): idx for idx, pr in enumerate(working_prs)}
    for start in range(0, len(to_process_numbers), 25):
        batch_numbers = to_process_numbers[start : start + 25]
        gql_data = fetch_pr_batch_graphql(owner, repo, batch_numbers)

        for number in batch_numbers:
            pr = working_prs[number_to_index[number]]
            pr_gql = gql_data.get(number, {})
            apply_graphql_issue_fields(pr, pr_gql if isinstance(pr_gql, dict) else {})

            login = get_login(pr)
            if login not in user_meta_cache:
                user_meta_cache[login] = fetch_user_metadata(login)
            if login not in user_repo_spread_cache:
                user_repo_spread_cache[login] = fetch_user_events_unique_repos(login)

            velocity = author_velocity.get(
                login,
                {
                    "author_max_prs_same_day": 0,
                    "author_median_interval_hours": None,
                    "author_prs_per_day": 0.0,
                },
            )
            pr["author_max_prs_same_day"] = velocity["author_max_prs_same_day"]
            pr["author_median_interval_hours"] = velocity["author_median_interval_hours"]
            pr["author_prs_per_day"] = velocity["author_prs_per_day"]

            meta = user_meta_cache[login]
            pr["author_unique_repos"] = user_repo_spread_cache[login]
            pr["author_account_age_days"] = meta["account_age_days"]
            pr["author_followers"] = meta["followers"]
            pr["author_public_repos"] = meta["public_repos"]

            processed += 1
            if processed % 50 == 0:
                print(f"Processed {processed}/{total_to_process}")
            if processed % 100 == 0:
                save_output(args.output, working_prs)
                print(f"Checkpoint saved at {processed} PRs -> {args.output}")

    save_output(args.output, working_prs)
    print(f"Wrote {args.output} ({len(working_prs)} PRs)")


if __name__ == "__main__":
    main()
