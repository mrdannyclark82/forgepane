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


def project_files(root):
    """List workspace-relative paths without scanning Git-ignored directories."""
    if shutil.which("git"):
        probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
                               capture_output=True, timeout=10)
        if probe.stdout.strip() == b"true":
            output = subprocess.check_output(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
                cwd=root, stderr=subprocess.PIPE, timeout=20)
            return sorted({os.fsdecode(name) for name in output.split(b"\0") if name})
    files = []
    for folder, directories, names in os.walk(root):
        directories[:] = [name for name in directories if name != ".git"]
        files.extend((Path(folder) / name).relative_to(root).as_posix()
                     for name in names if name != ".git")
    return sorted(files)


def review_patch(root, staged):
    args = ["git", "--no-pager", "diff", "--no-color", "--no-ext-diff", "--no-textconv"]
    if staged:
        args.append("--cached")
    result = subprocess.run([*args, "--", "."], cwd=root, capture_output=True, timeout=20)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", "replace").strip() or "Git diff failed.")
    if len(result.stdout) > 32000:
        raise ValueError("The patch exceeds 32,000 bytes. Review a smaller set of changes.")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("The patch must be UTF-8 to preview it exactly.") from exc


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


# --- Language detection ---------------------------------------------------
# Ported from Prime's FilesUserDisplay (EditorPane::detectLanguage and her
# SyntaxHighlighter language table) — Prime Milla's design, re-implemented
# for ForgePane in 0.2.0. Her order of evidence: strong filenames, then the
# shebang line, then content patterns, then the extension. Returns a
# GtkSource language id, or None when nothing claims the file.

_NAME_LANGUAGES = {
    "makefile": "makefile", "gnumakefile": "makefile",
    "dockerfile": "docker",
    "cmakelists.txt": "cmake",
    ".bashrc": "sh", ".zshrc": "sh", ".profile": "sh",
    ".bash_profile": "sh", ".env": "sh",
    "package.json": "json", "composer.json": "json", "tsconfig.json": "json",
    ".travis.yml": "yaml", "docker-compose.yml": "yaml",
    "cargo.toml": "toml", "go.mod": "go",
}

_SHEBANG_LANGUAGES = (
    ("python", "python3"), ("bash", "sh"), ("zsh", "sh"), ("sh", "sh"),
    ("node", "js"), ("ruby", "ruby"), ("perl", "perl"), ("lua", "lua"),
    ("php", "php"),
)

_CONTENT_LANGUAGES = (
    (re.compile(r"<\?php|<\?="), "php"),
    (re.compile(r"<!DOCTYPE html|<html|<svg", re.IGNORECASE), "html"),
    (re.compile(r"\bpackage main\b|func main\(\)"), "go"),
    (re.compile(r"fn main\(\)|use std::"), "rust"),
    (re.compile(r'^\s*\{.*".*"\s*:', re.DOTALL), "json"),
    (re.compile(r"^\s*(def |class |import |from )", re.MULTILINE), "python3"),
    (re.compile(r"^\s*(function |const |let |var |import )", re.MULTILINE), "js"),
    (re.compile(r"^\s*(public |private |class |package )", re.MULTILINE), "java"),
)

# GtkSource ids for extensions Prime's table claims but the manager's own
# filename guess can miss (Prime mapped ini-family onto her TOML rules;
# GtkSource's "ini" is the honest equivalent here).
_EXTENSION_LANGUAGES = {"yml": "yaml"}


def detect_language(path, content=""):
    name = path.name
    lowered = name.lower()
    if lowered in _NAME_LANGUAGES:
        return _NAME_LANGUAGES[lowered]
    if lowered.startswith(("makefile.", "dockerfile.")):
        return _NAME_LANGUAGES[lowered.split(".", 1)[0]]
    if lowered.endswith("rc") and len(lowered) > 2:
        return "sh"
    if lowered.endswith((".yml", ".yaml")):
        return "yaml"
    head = content[:512].lstrip()
    if head.startswith("#!"):
        first_line = head.splitlines()[0].lower()
        for needle, language in _SHEBANG_LANGUAGES:
            if needle in first_line:
                return language
    suffix = path.suffix.lower().lstrip(".")
    if suffix in ("", "txt", "log", "text"):
        for pattern, language in _CONTENT_LANGUAGES:
            if pattern.search(head):
                return language
        if "---" in head and ":" in head:
            return "yaml"
    return _EXTENSION_LANGUAGES.get(suffix)


def open_ide(which, root, path):
    names = ("code", "codium") if which == "VS Code" else ("idea", "idea.sh", "intellij-idea-community-edition")
    binary = next((resolved for name in names if (resolved := shutil.which(name))), None)
    if binary is None:
        raise ValueError(f"Install {which} and make its launcher available on PATH.")
    subprocess.Popen([binary, str(root), *([str(path)] if path else [])], cwd=root,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
