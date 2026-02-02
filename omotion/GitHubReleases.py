import requests
from pathlib import Path
from typing import List, Dict, Optional


class GitHubReleases:
    def __init__(self, owner: str, repo: str, timeout: int = 10):
        self.owner = owner
        self.repo = repo
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"
        self.timeout = timeout

    def _get(self, endpoint: str) -> Dict:
        url = f"{self.base_url}{endpoint}"
        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_all_releases(self, include_prerelease: bool = True) -> List[Dict]:
        """
        Returns all releases (optionally excluding prereleases)
        """
        releases = self._get("/releases")

        if include_prerelease:
            return releases

        # exclude prereleases when requested
        def is_prerelease(r: Dict) -> bool:
            # GitHub sets the 'prerelease' flag for prereleases; some projects
            # also use tag names like 'pre-1.4.0' without setting the flag.
            if r.get("prerelease"):
                return True
            tag = (r.get("tag_name") or "").lower()
            if tag.startswith("pre-"):
                return True
            return False

        return [r for r in releases if not is_prerelease(r)]

    def get_latest_release(self, include_prerelease: bool = False) -> Dict:
        """
        Returns the latest release.
        By default, excludes prereleases.
        """
        if include_prerelease:
            return self._get("/releases/latest")

        # GitHub /latest may include prereleases in edge cases,
        # so we filter explicitly if prereleases are not desired.
        releases = self.get_all_releases(include_prerelease=False)
        if not releases:
            raise RuntimeError("No releases found")

        return releases[0]

    def get_release_by_tag(self, tag: str) -> Dict:
        """
        Get a specific release by tag name
        """
        return self._get(f"/releases/tags/{tag}")

    def get_release_notes(self, tag: str) -> Optional[str]:
        """
        Return the release notes (body) for a given tag.

        Returns the release `body` string, or None if not present.
        Raises HTTPError if the tag does not exist or request fails.
        """
        release = self.get_release_by_tag(tag)
        return release.get("body")

    def get_asset_list(self, *, release: Optional[Dict] = None, tag: Optional[str] = None, extension: Optional[str] = None) -> List[Dict]:
        """
        Return a list of asset dicts for a release.

        Provide either `release` (the release dict) or `tag` (tag name).
        If `extension` is provided, only assets with that file extension are returned
        (comparison is case-insensitive; the leading dot is optional).
        """
        if release is None:
            if not tag:
                raise ValueError("either release or tag must be provided")
            release = self.get_release_by_tag(tag)

        assets = list(release.get("assets", []))

        if not extension:
            return assets

        # Normalize extension (allow 'bin' or '.bin')
        ext = extension.lower()
        if not ext.startswith('.'):
            ext = '.' + ext

        def has_ext(a: Dict) -> bool:
            name = a.get("name", "")
            return name.lower().endswith(ext)

        return [a for a in assets if has_ext(a)]

    def download_asset(
        self,
        release: Dict,
        asset_name: str,
        output_dir: Path = Path("."),
    ) -> Path:
        """
        Download a specific asset from a release
        """
        for asset in release.get("assets", []):
            if asset["name"] == asset_name:
                url = asset["browser_download_url"]
                output_dir.mkdir(parents=True, exist_ok=True)
                out_path = output_dir / asset_name

                with requests.get(url, stream=True, timeout=self.timeout) as r:
                    r.raise_for_status()
                    with open(out_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)

                return out_path

        raise ValueError(f"Asset '{asset_name}' not found")

    def download_source_archive(
        self,
        release: Dict,
        archive_format: str = "zip",
        output_dir: Path = Path("."),
    ) -> Path:
        """
        Download source code archive (zip or tar.gz)
        """
        if archive_format not in ("zip", "tar.gz"):
            raise ValueError("archive_format must be 'zip' or 'tar.gz'")

        url_key = "zipball_url" if archive_format == "zip" else "tarball_url"
        url = release[url_key]

        filename = f"{self.repo}-{release['tag_name']}.{archive_format}"
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / filename

        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()

        with open(out_path, "wb") as f:
            f.write(r.content)

        return out_path
