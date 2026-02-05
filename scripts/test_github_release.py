#!/usr/bin/env python3
"""
Simple CLI to exercise the `GitHubReleases` helper.

Usage examples:
  python scripts/test_github_release.py OpenwaterHealth OpenMOTION-Pylib
  python scripts/test_github_release.py OpenwaterHealth OpenMOTION-Pylib --tag v1.0.0
  python scripts/test_github_release.py OpenwaterHealth OpenMOTION-Pylib --asset testcustom_agg.bit

This script prints release info, lists assets, and can download a chosen asset.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from omotion.GitHubReleases import GitHubReleases

# Run this script with:
# set PYTHONPATH=%cd%;%PYTHONPATH%
# python scripts/test_github_release.py OpenwaterHealth motion-console-fw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test GitHubReleases class")
    parser.add_argument("owner", help="GitHub owner/org")
    parser.add_argument("repo", help="Repository name")
    parser.add_argument("--tag", help="Release tag to inspect (default: latest)")
    parser.add_argument("--asset", help="Asset name to download")
    parser.add_argument("--ext", help="Filter assets by extension (e.g. zip, bin)")
    parser.add_argument("--out", help="Output directory for downloads", default="downloads")
    parser.add_argument("--timeout", type=int, default=10, help="HTTP timeout seconds")

    args = parser.parse_args(argv)

    gh = GitHubReleases(args.owner, args.repo, timeout=args.timeout)

    try:
        if args.tag:
            release = gh.get_release_by_tag(args.tag)
        else:
            release = gh.get_latest_release()
    except Exception as exc:  # pragma: no cover - network/remote
        print(f"Error fetching release: {exc}")
        return 2

    tag_name = release.get("tag_name", "<unknown>")
    print(f"Release: {tag_name} - {release.get('name') or ''}")
    print(f"Published at: {release.get('published_at')}")
    print("--- Release notes ---")
    print(release.get("body") or "<no body>")
    print("---------------------\n")

    assets = gh.get_asset_list(release=release, extension=args.ext)

    if not assets:
        print("No assets found for this release.")
    else:
        print("Assets:")
        for i, a in enumerate(assets, start=1):
            name = a.get("name")
            size = a.get("size")
            url = a.get("browser_download_url")
            print(f"{i}. {name} ({size} bytes) - {url}")

    if args.asset:
        out_dir = Path(args.out)
        try:
            out_path = gh.download_asset(release, args.asset, output_dir=out_dir)
            print(f"Downloaded asset to: {out_path}")
        except Exception as exc:  # pragma: no cover - network/remote/file
            print(f"Error downloading asset: {exc}")
            return 3

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted")
        raise SystemExit(130)
