"""In-memory GitHub REST API used by tests; never touches the network."""

import base64
import json

import httpx

COMMIT = "c0ffee" + "0" * 34


class FakeGitHub:
    def __init__(self, files: dict[str, str], owner="org", repo="contracts", branch="main"):
        self.files = files
        self.prefix = f"/repos/{owner}/{repo}"
        self.branch = branch
        self.calls: list[httpx.Request] = []
        self.status: int | None = None

    def handles(self, request: httpx.Request) -> bool:
        return request.url.host == "api.github.com"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status:
            return httpx.Response(self.status)
        path = request.url.path
        if path == f"{self.prefix}/git/ref/heads/{self.branch}":
            return httpx.Response(200, json={"object": {"sha": COMMIT, "type": "commit"}})
        if path == f"{self.prefix}/git/trees/{COMMIT}":
            tree = [
                {"path": name, "type": "blob", "size": len(text)}
                for name, text in self.files.items()
            ]
            return httpx.Response(200, json={"tree": tree, "truncated": False})
        prefix = f"{self.prefix}/contents/"
        if path.startswith(prefix) and request.url.params.get("ref") == COMMIT:
            name = path[len(prefix) :]
            if name in self.files:
                content = base64.b64encode(self.files[name].encode()).decode()
                return httpx.Response(
                    200, json={"type": "file", "encoding": "base64", "content": content}
                )
        return httpx.Response(404)

    def count(self, kind: str) -> int:
        return sum(1 for call in self.calls if f"/{kind}" in call.url.path)


def artifact(abi: list[dict]) -> str:
    return json.dumps({"abi": abi, "bytecode": "0x"})
