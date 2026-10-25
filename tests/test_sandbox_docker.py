"""Real Docker boundary smoke tests; CI opts in with CODERILL_DOCKER_TEST=1."""

import os
import socket
import time
from urllib.request import urlopen

import pytest

from coderill.sandbox import DockerJob, SandboxError, SandboxPolicy, docker

pytestmark = pytest.mark.skipif(os.getenv("CODERILL_DOCKER_TEST") != "1", reason="requires built Docker images")


def test_worker_has_no_direct_network_or_host_secret(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("never-return-this-text")
    policy = SandboxPolicy(tmp_path, backend="docker", deny_read_paths=("secret.txt",))
    command = (
        "id -u; "
        "cat secret.txt; "
        "env | grep -E 'CODERILL_API_KEY|OPENAI_API_KEY' && exit 91; "
        "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy; "
        "python -c 'import socket; s=socket.socket(); s.settimeout(2); s.connect((\"1.1.1.1\",443))'"
    )
    result = DockerJob(policy).run(["bash", "-lc", command], timeout=20)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert result.stdout.splitlines()[0] != b"0"
    assert b"never-return-this-text" not in output
    assert not docker("ps", "-q", "--filter", "label=ai.coderill.sandbox=1").stdout.strip()


def test_workspace_write_and_read_only_modes(tmp_path):
    workspace = tmp_path / "中文 空格"
    workspace.mkdir()
    write_policy = SandboxPolicy(workspace, backend="docker")
    result = DockerJob(write_policy).run(["bash", "-lc", "printf changed > result.txt && git --version"], timeout=20)
    assert result.returncode == 0, result.stderr
    assert (workspace / "result.txt").read_text() == "changed"
    read_policy = SandboxPolicy(workspace, backend="docker", profile="read-only")
    result = DockerJob(read_policy).run(["bash", "-lc", "printf bad > result.txt"], timeout=20)
    assert result.returncode != 0
    assert (workspace / "result.txt").read_text() == "changed"


def test_gateway_allows_default_domain_and_denies_unlisted_target(tmp_path):
    policy = SandboxPolicy(tmp_path, backend="docker")
    allowed = DockerJob(policy).run(["python", "-c",
                                     "import urllib.request; print(urllib.request.urlopen('https://pypi.org/simple/', timeout=15).status)"],
                                    timeout=20)
    assert allowed.returncode == 0, allowed.stderr
    denied = DockerJob(policy).run(["python", "-c",
                                    "import urllib.request; urllib.request.urlopen('https://example.com/', timeout=10)"],
                                   timeout=15)
    assert denied.returncode != 0
    assert b"sandbox network denied" in denied.stderr


def test_python_and_npm_install_into_workspace(tmp_path):
    (tmp_path / "package.json").write_text('{"private":true,"dependencies":{"is-number":"7.0.0"}}')
    policy = SandboxPolicy(tmp_path, backend="docker")
    result = DockerJob(policy).run(["bash", "-lc",
                                    ("python -m pip install --disable-pip-version-check --target ./vendor packaging "
                                     "&& npm install --ignore-scripts --no-audit --no-fund")], timeout=120)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "vendor" / "packaging").is_dir()
    assert (tmp_path / "node_modules" / "is-number").is_dir()


def test_local_preview_port_and_crash_recovery(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        host_port = sock.getsockname()[1]
    policy = SandboxPolicy(tmp_path, backend="docker", ports=((host_port, 8123),))
    job = DockerJob(policy, ports=(8123,), session_id="integration")
    job.start(["python", "-m", "http.server", "8123", "--bind", "127.0.0.1"], background=True)
    try:
        for _ in range(50):
            try:
                with urlopen(f"http://127.0.0.1:{host_port}/", timeout=1) as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("preview port did not become reachable")
        with pytest.raises(SandboxError):
            DockerJob(policy, ports=(8123,), session_id="integration").start(
                ["python", "-m", "http.server", "8123"], background=True)
        with urlopen(f"http://127.0.0.1:{host_port}/", timeout=2) as response:
            assert response.status == 200
        record = {"job_id": job.id, "container": job.worker, "gateway": job.gateway,
                  "volume": job.volume, "network": job.network_name}
        assert DockerJob.recover(record, "integration")
        assert not docker("volume", "ls", "-q", "--filter", f"label=ai.coderill.job={job.id}").stdout.strip()
        assert not docker("network", "ls", "-q", "--filter", f"label=ai.coderill.job={job.id}").stdout.strip()
    finally:
        job.close()
