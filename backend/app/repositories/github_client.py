import base64
import re
from dataclasses import dataclass
from urllib.parse import quote

from app.services.http import JsonHttpClient, UpstreamError

SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class RepositoryRef:
    """A configured repository; owner/repo come only from validated configuration."""

    web_url: str
    owner: str
    name: str
    branch: str

    @property
    def api_url(self) -> str:
        host = self.web_url.split("/")[2]
        # GitHub Enterprise Server exposes the REST API under /api/v3 on the same host.
        base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        return f"{base}/repos/{quote(self.owner)}/{quote(self.name)}"

    @property
    def label(self) -> str:
        return f"{self.owner}/{self.name}"

    def file_url(self, commit: str, path: str) -> str:
        return f"{self.web_url}/blob/{commit}/{quote(path)}"

    def blob_url(self, commit: str, path: str, start: int, end: int) -> str:
        return f"{self.file_url(commit, path)}#L{start}-L{end}"


@dataclass
class TreeEntry:
    path: str
    size: int | None


@dataclass
class Tree:
    entries: list[TreeEntry]
    # GitHub omits entries when a recursive tree exceeds its own limits.
    truncated: bool


class GitHubClient:
    def __init__(self, http: JsonHttpClient, token: str | None, max_bytes: int):
        self.http = http
        self.max_bytes = max_bytes
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    async def _get(self, url: str, tool: str, params: dict[str, str] | None = None):
        return await self.http.request(
            "GET", url, tool=tool, params=params, headers=self.headers, max_bytes=self.max_bytes
        )

    async def get_branch_commit(self, repo: RepositoryRef) -> str:
        # The ref endpoint is compact; /commits/{branch} would also embed the diff.
        data = await self._get(
            f"{repo.api_url}/git/ref/heads/{quote(repo.branch, safe='/')}", "github.ref"
        )
        target = data.get("object") if isinstance(data, dict) else None
        sha = target.get("sha") if isinstance(target, dict) else None
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise UpstreamError("invalid_commit")
        return sha

    async def get_tree(self, repo: RepositoryRef, commit: str) -> Tree:
        data = await self._get(
            f"{repo.api_url}/git/trees/{commit}", "github.tree", params={"recursive": "1"}
        )
        if not isinstance(data, dict) or not isinstance(data.get("tree"), list):
            raise UpstreamError("invalid_tree")
        entries = [
            TreeEntry(item["path"], item.get("size") if isinstance(item.get("size"), int) else None)
            for item in data["tree"]
            if isinstance(item, dict)
            and item.get("type") == "blob"
            and isinstance(item.get("path"), str)
        ]
        return Tree(entries, data.get("truncated") is True)

    async def get_file(self, repo: RepositoryRef, commit: str, path: str) -> str:
        data = await self._get(
            f"{repo.api_url}/contents/{quote(path)}", "github.contents", params={"ref": commit}
        )
        if (
            not isinstance(data, dict)
            or data.get("type") != "file"
            or data.get("encoding") != "base64"
            or not isinstance(data.get("content"), str)
        ):
            # Files above 1 MB come back without inline content.
            raise UpstreamError("invalid_file")
        try:
            return base64.b64decode(data["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise UpstreamError("invalid_file") from None
