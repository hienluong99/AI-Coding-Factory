"""Lightweight GitHub API client for automated Pull Request creation."""

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


class GitHubAPIError(Exception):
    """Raised when GitHub API operations fail."""
    pass


def parse_github_remote(remote_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract (owner, repo_name, token) from git remote URL.
    Supports:
      - https://user:token@github.com/owner/repo.git
      - https://token@github.com/owner/repo.git
      - https://github.com/owner/repo.git
      - git@github.com:owner/repo.git
    """
    if not remote_url:
        return None, None, None

    clean_url = remote_url.strip()
    token: Optional[str] = None

    # Check for embedded credentials: https://user:token@... or https://token@...
    cred_match = re.search(r"https?://(?:([^:@]+):)?([^@]+)@github\.com/([^/]+)/([^/\.]+)(?:\.git)?", clean_url)
    if cred_match:
        user, pwd, owner, repo = cred_match.groups()
        token = pwd if pwd else user
        return owner, repo, token

    # Check standard HTTPS: https://github.com/owner/repo.git
    https_match = re.search(r"https?://github\.com/([^/]+)/([^/\.]+)(?:\.git)?", clean_url)
    if https_match:
        owner, repo = https_match.groups()
        return owner, repo, None

    # Check SSH: git@github.com:owner/repo.git
    ssh_match = re.search(r"git@github\.com:([^/]+)/([^/\.]+)(?:\.git)?", clean_url)
    if ssh_match:
        owner, repo = ssh_match.groups()
        return owner, repo, None

    return None, None, None


class GitHubClient:
    """Client for creating and querying GitHub Pull Requests via REST API."""

    def __init__(self, token: Optional[str] = None) -> None:
        self.token = token

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new Pull Request on GitHub or find existing one if already created.
        """
        auth_token = token or self.token
        if not auth_token:
            raise GitHubAPIError("Không có GitHub Token xác thực để tạo Pull Request qua API.")

        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AI-Coding-Factory",
            "Content-Type": "application/json",
        }

        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "url": data.get("html_url"),
                    "number": data.get("number"),
                    "id": data.get("id"),
                    "state": data.get("state"),
                    "created": True,
                }
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            # If 422: PR already exists for this branch, attempt to fetch existing
            if exc.code == 422 and "A pull request already exists" in err_body:
                existing = self._find_existing_pr(owner, repo, head, base, auth_token)
                if existing:
                    existing["created"] = False
                    return existing

            raise GitHubAPIError(f"GitHub API Error ({exc.code}): {err_body}")
        except Exception as exc:
            raise GitHubAPIError(f"Không thể kết nối đến GitHub API: {exc}")

    def _find_existing_pr(
        self,
        owner: str,
        repo: str,
        head: str,
        base: str,
        token: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch open PR for the given head branch if one already exists."""
        # Head parameter format: owner:branch or branch
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=open&head={owner}:{head}&base={base}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "AI-Coding-Factory",
        }

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                prs = json.loads(resp.read().decode("utf-8"))
                if prs and isinstance(prs, list):
                    first = prs[0]
                    return {
                        "url": first.get("html_url"),
                        "number": first.get("number"),
                        "id": first.get("id"),
                        "state": first.get("state"),
                    }
        except Exception:
            pass
        return None
