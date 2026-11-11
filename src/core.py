"""Local document and integration operations."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from urllib.request import Request, build_opener, ProxyHandler

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "forgepane"


def atomic_write(path, data):
    path = path.resolve()
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if path.exists():
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_document(path):
    if not path.is_file():
        raise ValueError("Choose a regular text file.")
    with path.open("rb") as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024 or b"\0" in data:
        raise ValueError("Open a UTF-8 text file up to 4 MiB.")
    return data.decode("utf-8"), digest(data)


def save_document(path, text, expected):
    _, current = read_document(path)
    if current != expected:
        raise ValueError("The file changed on disk. Copy your edits before reopening it.")
    data = text.encode("utf-8")
    if len(data) > 4 * 1024 * 1024 or b"\0" in data:
        raise ValueError("The edited file must be UTF-8 text up to 4 MiB.")
    atomic_write(path, data)
    return digest(data)


def run(args, cwd):
    result = subprocess.run(args, cwd=cwd, text=True, errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    output = result.stdout
    if result.returncode:
        raise RuntimeError(output[-12000:] or f"Exit status {result.returncode}")
    return output[-12000:]


def git_args(action):
    return {
        "status": ["git", "status", "--short", "--branch"],
        "diff": ["git", "--no-pager", "diff", "HEAD", "--"],
        "fetch": ["git", "fetch"],
        "pull": ["git", "pull", "--ff-only"],
        "push": ["git", "push"],
    }[action]


def sync_args(root, remote, first, preview):
    if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_ .-]*:[^\r\n]+", remote):
        raise ValueError("Enter a configured rclone remote and dedicated folder, e.g. work:forge-project.")
    return ["rclone", "bisync", str(root), remote, "--verbose", "--check-access",
            "--exclude", "/.git/**", "--max-delete", "10",
            *(["--resync"] if first else []), *(["--dry-run"] if preview else [])]


def local_json(port, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(f"http://127.0.0.1:{port}{path}", data=body,
                      headers={"Content-Type": "application/json"})
    # Explicitly disable proxying and redirects for local code and review content.
    from urllib.request import HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=90) as response:
        return json.load(response)


def open_ide(which, root, path):
    names = ("code", "codium") if which == "VS Code" else ("idea", "idea.sh", "intellij-idea-community-edition")
    binary = next((resolved for name in names if (resolved := shutil.which(name))), None)
    if binary is None:
        raise ValueError(f"Install {which} and make its launcher available on PATH.")
    subprocess.Popen([binary, str(root), *([str(path)] if path else [])], cwd=root,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
