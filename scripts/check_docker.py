"""Check the local Compose demo without calling a model.

--recreate replaces this demo service to prove that its named volume survives.
Run it when the demo has no Agent task in progress.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:18765"
MARKER = "Docker deployment check: this session must survive container replacement."
OPENER = build_opener(ProxyHandler({}))


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def request(path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    headers = {} if body is None else {"Content-Type": "application/json"}
    with OPENER.open(Request(BASE + path, data=data, headers=headers), timeout=5) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recreate", action="store_true", help="Replace the demo container and check persistence")
    args = parser.parse_args()

    docker("compose", "config", "--quiet")
    container = docker("compose", "ps", "-q", "coderill")
    if not container:
        raise RuntimeError("Start the demo first: docker compose up -d --build --wait")
    assert request("/healthz")["status"] == "ok", "Service is not ready"
    assert request("/api/meta")["workspace"] == "/workspace", "Wrong workspace"
    with OPENER.open(BASE + "/", timeout=5) as response:
        assert b"<" in response.read(100), "The built web UI was not served"
    print("[OK] health endpoint, web UI and workspace metadata")

    info = json.loads(docker("inspect", container))[0]
    assert info["HostConfig"]["PortBindings"]["18765/tcp"] == [{"HostIp": "127.0.0.1", "HostPort": "18765"}]
    assert info["HostConfig"]["Memory"] == 1024**3
    assert info["HostConfig"]["NanoCpus"] == 10**9
    mounts = {item["Destination"]: item for item in info["Mounts"]}
    assert mounts["/workspace"]["Type"] == "bind"
    assert mounts["/home/coderill/.coderill"]["Type"] == "volume"
    assert docker("compose", "exec", "-T", "coderill", "id", "-u") != "0"
    print("[OK] loopback port, resource limits, bind mount, data volume and non-root user")

    temporary = docker(
        "compose", "exec", "-T", "coderill", "python", "-c",
        "import pathlib,tempfile; "
        "f=tempfile.NamedTemporaryFile(dir='/workspace',prefix='coderill-deploy-',suffix='.txt',delete=False); "
        "f.write(b'bind mount check'); f.close(); print(pathlib.Path(f.name).name)",
    )
    assert Path(temporary).name == temporary and temporary.startswith("coderill-deploy-"), "Unexpected check path"
    host_file = ROOT / "examples" / "docker-workspace" / temporary
    try:
        assert host_file.read_text(encoding="utf-8") == "bind mount check"
    finally:
        host_file.unlink(missing_ok=True)
    print("[OK] container write is visible in the host demo workspace; temporary file removed")

    session_id = request("/api/sessions", {})["id"]
    docker(
        "compose", "exec", "-T", "coderill", "python", "-c",
        "import sys; from coderill.session import Session; "
        "Session.load(sys.argv[1]).record({'role':'user','content':sys.argv[2]})",
        session_id, MARKER,
    )
    assert request(f"/api/sessions/{session_id}")["messages"][-1]["content"] == MARKER
    print(f"[OK] durable session created: {session_id}")

    if args.recreate:
        docker("compose", "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60", "coderill")
        replacement = docker("compose", "ps", "-q", "coderill")
        assert replacement != container, "The container was not replaced"
        assert request(f"/api/sessions/{session_id}")["messages"][-1]["content"] == MARKER
        print("[OK] a new container recovered the same session and message from the named volume")
    print(f"All requested checks passed. Open {BASE}/ . No model request was sent.")


if __name__ == "__main__":
    main()
