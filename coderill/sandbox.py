"""Docker-backed command boundary, independent of tool approval policy."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DOMAINS = ("registry.npmjs.org:443", "pypi.org:443", "files.pythonhosted.org:443")
GATEWAY_IMAGE = "coderill-sandbox-gateway:local"
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}:443$")
_ACTIVE: contextvars.ContextVar[SandboxPolicy | None] = contextvars.ContextVar("sandbox_policy", default=None)
_NETWORK_GRANT: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("sandbox_network_grant", default=())


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxPolicy:
    workspace: Path
    backend: str = "off"
    profile: str = "workspace-write"
    network: str = "restricted"
    image: str = "coderill-sandbox:local"
    ports: tuple[tuple[int, int], ...] = ()
    deny_read_paths: tuple[str, ...] = ()
    cpus: float = 2.0
    memory: str = "2g"
    pids: int = 256
    _denied: tuple[Path, ...] = field(default=(), init=False, repr=False)

    def __post_init__(self) -> None:
        requested_root = self.workspace.expanduser()
        root = requested_root.resolve()
        object.__setattr__(self, "workspace", root)
        if self.backend not in ("off", "docker") or self.profile not in ("read-only", "workspace-write"):
            raise ValueError("invalid sandbox backend or profile")
        if self.network not in ("restricted", "none") or not self.image.strip():
            raise ValueError("invalid sandbox network or image")
        if self.cpus <= 0 or self.pids < 1 or not re.fullmatch(r"[1-9][0-9]*[kmg]", self.memory):
            raise ValueError("invalid sandbox resource limits")
        if len({host for host, _ in self.ports}) != len(self.ports):
            raise ValueError("sandbox host ports must be unique")
        if any(not 1 <= host <= 65535 or not 1 <= guest <= 65535 for host, guest in self.ports):
            raise ValueError("sandbox ports must be between 1 and 65535")
        if self.backend == "off":
            return
        home = Path.home().resolve()
        if root == Path(root.anchor) or root == home or root in home.parents or root.is_relative_to(home / ".coderill"):
            raise ValueError("sandbox workspace cannot be a system root or home directory")
        if requested_root.is_symlink() or not root.is_dir():
            raise ValueError("sandbox workspace must be an existing directory without a symlink root")
        if _has_nested_mount(root):
            raise ValueError("nested mounts are not supported by the sandbox")
        denied: list[Path] = []
        for name in self.deny_read_paths:
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.exists():
                raise ValueError(f"sandbox denied path must exist inside workspace: {name}")
            if _has_hardlinks(path):
                raise ValueError(f"sandbox denied path contains hard links: {name}")
            denied.append(path)
        object.__setattr__(self, "_denied", tuple(denied))

    @property
    def enabled(self) -> bool:
        return self.backend == "docker"

    @property
    def key(self) -> str:
        values = (1, str(self.workspace), self.backend, self.profile, self.network, self.image,
                  self.ports, self.deny_read_paths, self.cpus, self.memory, self.pids, DEFAULT_DOMAINS)
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def check_path(self, path: Path) -> None:
        target = path.resolve()
        lexical = Path(os.path.abspath(path))
        for name, denied in zip(self.deny_read_paths, self._denied):
            protected = Path(os.path.abspath(self.workspace / name))
            if (target == denied or denied in target.parents or lexical == protected
                    or protected in lexical.parents):
                raise SandboxError(f"sandbox policy denies reading {name}")

    def revalidate(self) -> None:
        if self.workspace.resolve() != self.workspace:
            raise SandboxError("sandbox workspace path changed")
        for name, denied in zip(self.deny_read_paths, self._denied):
            current = (self.workspace / name).resolve()
            if current != denied or not denied.exists() or _has_hardlinks(denied):
                raise SandboxError(f"sandbox denied path changed: {name}")

    def check_mutation(self, path: Path) -> None:
        self.check_path(path)
        target = path.resolve()
        protected = [Path(os.path.abspath(self.workspace / name)) for name in self.deny_read_paths]
        if any(target in denied.parents for denied in (*self._denied, *protected)):
            raise SandboxError("sandbox policy denies changing a parent of a protected path")

    def domains(self, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
        if self.network == "none" and extra:
            raise SandboxError("sandbox network is disabled")
        for domain in extra:
            if not isinstance(domain, str) or not _DOMAIN.fullmatch(domain):
                raise SandboxError(f"invalid sandbox domain: {domain!r}; expected hostname:443")
            if domain.startswith(("localhost.", "local.")):
                raise SandboxError("local domains are not allowed")
        return tuple(dict.fromkeys((DEFAULT_DOMAINS if self.network == "restricted" else ()) + extra))


@contextlib.contextmanager
def policy_scope(policy: SandboxPolicy | None):
    if policy and policy.enabled:
        policy.revalidate()
    token = _ACTIVE.set(policy if policy and policy.enabled else None)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def check_active_path(path: Path) -> None:
    policy = _ACTIVE.get()
    if policy is not None:
        policy.check_path(path)


def check_active_mutation(path: Path) -> None:
    policy = _ACTIVE.get()
    if policy is not None:
        policy.check_mutation(path)


def set_network_grant(domains: tuple[str, ...]) -> contextvars.Token:
    return _NETWORK_GRANT.set(domains)


def reset_network_grant(token: contextvars.Token) -> None:
    _NETWORK_GRANT.reset(token)


def parse_ports(values: list[str] | tuple[str, ...]) -> tuple[tuple[int, int], ...]:
    ports = []
    for value in values:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            raise ValueError(f"invalid sandbox port {value!r}; expected HOST:CONTAINER")
        ports.append((int(parts[0]), int(parts[1])))
    return tuple(ports)


def _has_hardlinks(path: Path) -> bool:
    files = (item for item in path.rglob("*") if item.is_file() and not item.is_symlink()) if path.is_dir() else (path,)
    return any(item.is_file() and item.stat().st_nlink != 1 for item in files)


def _is_junction(path: Path) -> bool:
    if hasattr(path, "is_junction"):
        return path.is_junction()
    return os.name == "nt" and bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _has_nested_mount(root: Path) -> bool:
    return any(path.is_mount() or _is_junction(path)
               for path in root.rglob("*") if path.is_dir() and not path.is_symlink())


def docker(*args: str, timeout: float = 30, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    if shutil.which("docker") is None:
        raise SandboxError("Docker CLI is unavailable")
    try:
        result = subprocess.run(["docker", *args], input=input, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(f"Docker operation failed: {exc}") from exc
    if result.returncode:
        raise SandboxError(result.stderr.decode(errors="replace").strip() or "Docker operation failed")
    return result


def _squid_config(domains: tuple[str, ...]) -> str:
    lines = [
        "http_port 127.0.0.1:3128", "pid_filename none", "cache deny all", "access_log stdio:/dev/stdout",
        "acl safe_port port 443", "acl CONNECT method CONNECT",
        "http_access deny !safe_port", "http_access deny !CONNECT",
        "acl private dst 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 224.0.0.0/4 240.0.0.0/4 ::/128 ::1/128 2001:db8::/32 fc00::/7 fe80::/10 fec0::/10 ff00::/8",
    ]
    if domains:
        lines.extend(("acl allowed dstdomain -n " + " ".join(domain[:-4] for domain in domains),
                      "http_access deny !allowed", "http_access deny private", "http_access allow allowed"))
    return "\n".join([*lines, "http_access deny all", "via off", "forwarded_for delete", ""])


class DockerJob:
    """Own exactly one command container, proxy container, and IPC volume."""

    def __init__(self, policy: SandboxPolicy, *, extra_domains: tuple[str, ...] = (),
                 ports: tuple[int, ...] = (), session_id: str = "") -> None:
        self.policy = policy
        if any(domain not in _NETWORK_GRANT.get() for domain in extra_domains if domain not in DEFAULT_DOMAINS):
            raise SandboxError("network domain requires a granted sandbox review")
        self.domains = policy.domains(extra_domains)
        self.ports = ports
        allowed_ports = {guest: host for host, guest in policy.ports}
        if any(port not in allowed_ports for port in ports) or len(set(ports)) != len(ports):
            raise SandboxError("requested preview port is not configured")
        self.id = uuid.uuid4().hex
        self.worker = f"coderill-sbox-{self.id[:20]}"
        self.gateway = f"coderill-gw-{self.id[:20]}"
        self.volume = f"coderill-ipc-{self.id[:20]}"
        self.network_name = f"coderill-net-{self.id[:20]}"
        self.session_id = session_id
        self._config_dir: tempfile.TemporaryDirectory[str] | None = None
        self._created = False
        self._closed = threading.Event()
        self._cleanup_lock = threading.Lock()
        self.cleanup_pending = False
        self._record_path = Path.home() / ".coderill" / "sandbox-jobs" / f"{self.id}.json"

    def start(self, argv: list[str], *, background: bool = False) -> None:
        policy = self.policy
        if not policy.enabled:
            raise SandboxError("Docker job requires Docker sandbox mode")
        policy.revalidate()
        if _has_nested_mount(policy.workspace):
            raise SandboxError("nested mounts are not supported by the sandbox")
        for image in (policy.image, GATEWAY_IMAGE):
            docker("image", "inspect", image)
        if self._closed.is_set():
            raise SandboxError("sandbox command was cancelled")
        self._record_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"pid": os.getpid(), "session_id": self.session_id, "job_id": self.id,
                  "container": self.worker, "gateway": self.gateway, "volume": self.volume,
                  "network": self.network_name}
        temporary = self._record_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(self._record_path)
        self._created = True
        try:
            self._config_dir = tempfile.TemporaryDirectory(prefix="coderill-sandbox-")
            cfg = Path(self._config_dir.name) / "squid.conf"
            cfg.write_text(_squid_config(self.domains), encoding="utf-8")
            docker("volume", "create", "--label", "ai.coderill.sandbox=1",
                   "--label", f"ai.coderill.job={self.id}",
                   "--label", f"ai.coderill.session={self.session_id}", self.volume)
            docker("network", "create", "--label", "ai.coderill.sandbox=1",
                   "--label", f"ai.coderill.job={self.id}",
                   "--label", f"ai.coderill.session={self.session_id}", self.network_name)
            docker("run", "--rm", "--network", "none", "--user", "0", "--mount",
                   f"type=volume,src={self.volume},dst=/ipc", "--entrypoint", "chmod", policy.image, "1777", "/ipc")
        except Exception as exc:
            if not self.close():
                raise SandboxError(f"sandbox startup failed; cleanup pending for job {self.id}: {exc}") from exc
            raise
        if self._closed.is_set():
            self.close()
            raise SandboxError("sandbox command was cancelled")
        labels = ["--label", "ai.coderill.sandbox=1", "--label", f"ai.coderill.job={self.id}"]
        labels += ["--label", f"ai.coderill.session={self.session_id}"]
        try:
            gateway_args = ["run", "-d", "--name", self.gateway, *labels, "--network", self.network_name,
                            "--cpus", "0.5", "--memory", "256m",
                            "--pids-limit", "64", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                            "--log-driver", "local", "--log-opt", "max-size=10m", "--log-opt", "max-file=2",
                            "--mount", f"type=volume,src={self.volume},dst=/ipc",
                            "--mount", f"type=bind,src={cfg},dst=/etc/squid/squid.conf,readonly"]
            allowed_ports = {guest: host for host, guest in policy.ports}
            for guest in self.ports:
                gateway_args += ["-p", f"127.0.0.1:{allowed_ports[guest]}:{guest}"]
            docker(*gateway_args, GATEWAY_IMAGE, *(str(port) for port in self.ports))
            for _ in range(100):
                if self._closed.is_set():
                    raise SandboxError("sandbox command was cancelled")
                ready = subprocess.run(["docker", "exec", self.gateway, "test", "-S", "/ipc/proxy.sock"],
                                       capture_output=True, timeout=5, check=False)
                if ready.returncode == 0:
                    break
                if docker("inspect", "-f", "{{.State.Running}}", self.gateway).stdout.strip() != b"true":
                    raise SandboxError("sandbox network gateway exited during startup")
                time.sleep(0.1)
            else:
                raise SandboxError("sandbox network gateway did not become ready")
            root = policy.workspace
            worker_args = ["create", "-i", "--name", self.worker, *labels, "--network", "none", "--read-only",
                           "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                           "--log-driver", "local", "--log-opt", "max-size=10m", "--log-opt", "max-file=2",
                           "--cpus", str(policy.cpus), "--memory", policy.memory, "--pids-limit", str(policy.pids),
                           "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
                           "--tmpfs", "/home/coderill:rw,nosuid,nodev,size=64m,mode=1777",
                           "--workdir", "/workspace", "--mount",
                           f"type=bind,src={root},dst=/workspace" + (",readonly" if policy.profile == "read-only" else ""),
                           "--mount", f"type=volume,src={self.volume},dst=/ipc",
                           "--env", "HOME=/home/coderill", "--env", "HTTP_PROXY=http://127.0.0.1:3128",
                           "--env", "HTTPS_PROXY=http://127.0.0.1:3128", "--env", "ALL_PROXY=http://127.0.0.1:3128",
                           "--env", "http_proxy=http://127.0.0.1:3128", "--env", "https_proxy=http://127.0.0.1:3128",
                           "--env", "all_proxy=http://127.0.0.1:3128",
                           "--env", "NO_PROXY=", "--env", "no_proxy=",
                           "--env", "CODERILL_PREVIEW_PORTS=" + ",".join(str(port) for port in self.ports)]
            if os.name != "nt":
                worker_args += ["--user", f"{os.getuid()}:{os.getgid()}"]
            mask_mounts = []
            file_targets = []
            for denied in policy._denied:
                relative = denied.relative_to(root).as_posix()
                target = "/workspace/" + relative
                mask_mounts += ["--mount", f"type=bind,src={self._mask(denied)},dst={target},readonly"]
                if denied.is_file():
                    file_targets.append(target)
            worker_args += mask_mounts
            if file_targets:
                probe = ["run", "--rm", "--network", "none", "--read-only", "--mount",
                         f"type=bind,src={root},dst=/workspace,readonly", *mask_mounts]
                if os.name != "nt":
                    probe += ["--user", f"{os.getuid()}:{os.getgid()}"]
                try:
                    docker(*probe, "--entrypoint", "/bin/sh", policy.image,
                           "-c", 'for path do [ ! -r "$path" ] || exit 1; done', "mask-check", *file_targets)
                except SandboxError as exc:
                    raise SandboxError(f"sandbox sensitive-file mask could not be verified: {exc}") from exc
            worker_args += [policy.image, *argv]
            docker(*worker_args)
            if self._closed.is_set():
                raise SandboxError("sandbox command was cancelled")
            if background:
                docker("start", self.worker)
        except Exception as exc:
            if not self.close():
                raise SandboxError(f"sandbox startup failed; cleanup pending for job {self.id}: {exc}") from exc
            raise

    def _mask(self, denied: Path) -> Path:
        assert self._config_dir is not None
        path = Path(self._config_dir.name) / ("mask-" + str(len(list(Path(self._config_dir.name).glob("mask-*")))))
        if denied.is_dir():
            path.mkdir()
        else:
            path.touch(mode=0o000)
        if os.name != "nt":
            path.chmod(0o000)
        return path

    def run(self, argv: list[str], *, input: bytes | None = None, timeout: float = 120) -> subprocess.CompletedProcess[bytes]:
        self.start(argv)
        try:
            command = ["docker", "start", "-a", *(["-i"] if input is not None else []), self.worker]
            process = subprocess.Popen(command, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = bytearray(), bytearray()

            def collect(stream, target: bytearray) -> None:
                for chunk in iter(lambda: stream.read(4096), b""):
                    if len(target) < 64_000:
                        target.extend(chunk[:64_000 - len(target)])

            readers = [threading.Thread(target=collect, args=(process.stdout, stdout), daemon=True),
                       threading.Thread(target=collect, args=(process.stderr, stderr), daemon=True)]
            for reader in readers:
                reader.start()
            try:
                if input is not None:
                    process.stdin.write(input)
                    process.stdin.close()
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise SandboxError(f"sandbox command timed out after {timeout}s") from exc
            except BaseException:
                process.kill()
                process.wait()
                raise
            finally:
                for reader in readers:
                    reader.join(timeout=5)
            code = int(docker("inspect", "-f", "{{.State.ExitCode}}", self.worker).stdout.strip())
            if docker("inspect", "-f", "{{.State.Running}}", self.gateway).stdout.strip() != b"true":
                raise SandboxError("sandbox network gateway failed during the command")
            gateway_log = docker("logs", "--tail", "100", self.gateway).stdout
            if b"TCP_DENIED" in gateway_log:
                stderr = bytearray(b"sandbox network denied by gateway policy\n") + stderr
            return subprocess.CompletedProcess(argv, code, bytes(stdout), bytes(stderr))
        finally:
            if not self.close():
                raise SandboxError(f"sandbox cleanup pending for job {self.id}")

    def close(self) -> bool:
        with self._cleanup_lock:
            return self._close()

    def _close(self) -> bool:
        self._closed.set()
        clean = True
        if self._created:
            for args, absent in ((["rm", "-f", self.worker], b"No such container"),
                                 (["rm", "-f", self.gateway], b"No such container"),
                                 (["volume", "rm", "-f", self.volume], b"No such volume"),
                                 (["network", "rm", self.network_name], b"not found")):
                try:
                    result = subprocess.run(["docker", *args], capture_output=True, timeout=5, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    clean = False
                else:
                    clean &= result.returncode == 0 or absent in result.stderr
        self.cleanup_pending = not clean
        if clean and self._config_dir is not None:
            self._config_dir.cleanup()
            self._config_dir = None
        if clean:
            self._record_path.unlink(missing_ok=True)
        return clean

    @staticmethod
    def recover(record: dict, session_id: str) -> bool:
        """Remove only resources whose recorded names and Docker labels still agree."""
        job_id = record.get("job_id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            return False
        names = (record.get("container"), record.get("gateway"), record.get("volume"), record.get("network"))
        if names != (f"coderill-sbox-{job_id[:20]}", f"coderill-gw-{job_id[:20]}",
                     f"coderill-ipc-{job_id[:20]}", f"coderill-net-{job_id[:20]}"):
            return False
        for index, name in enumerate(names):
            category = ("volume", "inspect") if index == 2 else ("network", "inspect") if index == 3 else ("inspect",)
            command = ["docker", *category, name, "--format",
                       "{{json .Labels}}" if index in (2, 3) else "{{json .Config.Labels}}"]
            try:
                result = subprocess.run(command, capture_output=True, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired):
                return False
            if result.returncode:
                if b"no such" in result.stderr.lower() or b"not found" in result.stderr.lower():
                    continue
                return False
            try:
                labels = json.loads(result.stdout)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return False
            if (not isinstance(labels, dict) or labels.get("ai.coderill.job") != job_id
                    or labels.get("ai.coderill.session") != session_id):
                return False
        clean = True
        for command in (("rm", "-f", names[0]), ("rm", "-f", names[1]),
                        ("volume", "rm", "-f", names[2]), ("network", "rm", names[3])):
            try:
                result = subprocess.run(["docker", *command], capture_output=True, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired):
                clean = False
            else:
                clean &= result.returncode == 0 or b"no such" in result.stderr.lower() or b"not found" in result.stderr.lower()
        return clean


_RECOVERED = False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def recover_orphan_jobs() -> None:
    global _RECOVERED
    if _RECOVERED:
        return
    _RECOVERED = True
    directory = Path.home() / ".coderill" / "sandbox-jobs"
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            pid = record["pid"]
            if not isinstance(pid, int) or pid <= 0 or pid == os.getpid() or _pid_alive(pid):
                continue
            # shortcut: PID reuse can defer cleanup; a later launch can retry the recorded job.
            if DockerJob.recover(record, record.get("session_id", "")):
                path.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError):
            continue
