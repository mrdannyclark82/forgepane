"""Run the active file and stream its output — the Output tab's engine.

Two halves, kept apart so the logic is testable without a display:

* ``command_for(path, content)`` picks the interpreter: the shebang line
  first, then the extension, then ForgePane's language detection, then the
  executable bit. It raises ``ValueError`` with a plain message when the
  file can't be run or the interpreter isn't installed.
* ``Runner`` starts the command in its own process group and streams
  stdout and stderr through GLib IO watches on the main loop — no threads
  touch widgets and the UI never blocks. ``stop()`` sends SIGTERM to the
  whole group, then SIGKILL if it hasn't exited after a short grace period.
"""
import codecs
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import time

from gi.repository import GLib

import core

KILL_GRACE_MS = 1500

# Each entry is a list of alternatives; the first whose program is on PATH
# wins. "{file}" is replaced by the script path.
_EXTENSIONS = {
    "py": [["python3", "{file}"], ["python", "{file}"]],
    "pyw": [["python3", "{file}"], ["python", "{file}"]],
    "sh": [["bash", "{file}"], ["sh", "{file}"]],
    "bash": [["bash", "{file}"]],
    "zsh": [["zsh", "{file}"]],
    "fish": [["fish", "{file}"]],
    "js": [["node", "{file}"], ["deno", "run", "{file}"], ["bun", "{file}"]],
    "mjs": [["node", "{file}"], ["deno", "run", "{file}"], ["bun", "{file}"]],
    "cjs": [["node", "{file}"], ["bun", "{file}"]],
    "ts": [["deno", "run", "{file}"], ["tsx", "{file}"], ["ts-node", "{file}"], ["bun", "{file}"]],
    "rb": [["ruby", "{file}"]],
    "pl": [["perl", "{file}"]],
    "lua": [["lua", "{file}"], ["luajit", "{file}"]],
    "php": [["php", "{file}"]],
    "go": [["go", "run", "{file}"]],
    "r": [["Rscript", "{file}"]],
}

# GtkSource language ids from core.detect_language -> extension table key.
_LANGUAGES = {"python3": "py", "python": "py", "sh": "sh", "js": "js",
              "ruby": "rb", "perl": "pl", "lua": "lua", "php": "php", "go": "go"}

# Friendly names for "install X" messages.
_INSTALL_HINT = {"py": "python3", "pyw": "python3", "sh": "bash", "js": "node", "mjs": "node",
                 "cjs": "node", "ts": "deno, tsx, or ts-node", "lua": "lua"}


def parse_shebang(content):
    """Return the shebang as an argv list (without the script), or None."""
    first = content.split("\n", 1)[0]
    if not first.startswith("#!"):
        return None
    try:
        words = shlex.split(first[2:].strip())
    except ValueError:
        words = first[2:].split()
    if not words:
        return None
    if Path(words[0]).name == "env":
        words = words[1:]
        while words and words[0].startswith("-"):  # env -S, env -i, ...
            words = words[1:]
    return words or None


def _resolve(program, which):
    if os.sep in program:
        return program if os.access(program, os.X_OK) else None
    return which(program)


def _from_table(key, path, which):
    for candidate in _EXTENSIONS.get(key, []):
        binary = which(candidate[0])
        if binary:
            return [binary if part == candidate[0] else part.replace("{file}", str(path))
                    for part in candidate]
    return None


def command_for(path, content="", which=shutil.which):
    """Pick the argv that runs ``path``. Raises ValueError with a clear message."""
    path = Path(path)
    shebang = parse_shebang(content)
    if shebang:
        binary = _resolve(shebang[0], which)
        if binary is None:
            raise ValueError(f"{path.name} asks for '{shebang[0]}' in its #! line, "
                             f"but it isn't installed or on PATH.")
        return [binary, *shebang[1:], str(path)]
    keys = []
    suffix = path.suffix.lower().lstrip(".")
    if suffix in _EXTENSIONS:
        keys.append(suffix)
    detected = _LANGUAGES.get(core.detect_language(path, content) or "")
    if detected and detected not in keys:
        keys.append(detected)
    for key in keys:
        argv = _from_table(key, path, which)
        if argv:
            return argv
    if keys:
        hint = _INSTALL_HINT.get(keys[0], _EXTENSIONS[keys[0]][0][0])
        raise ValueError(f"Install {hint} to run {path.name} (not found on PATH).")
    if path.is_file() and os.access(path, os.X_OK):
        return [str(path)]
    raise ValueError(f"ForgePane doesn't know how to run {path.name}. Add a #! line "
                     f"or use an extension like .py, .sh, or .js.")


def describe_exit(returncode):
    """Human text for a Popen-style return code (negative = signal)."""
    if returncode < 0:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"signal {-returncode}"
        return f"stopped by {name}"
    return f"exited with code {returncode}"


def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.2f} s"
    minutes, seconds = divmod(seconds, 60)
    return f"{int(minutes)} min {seconds:.1f} s"


class Runner:
    """One child process at a time, streamed through GLib IO watches.

    on_output(text, stream) gets decoded chunks ("stdout" or "stderr");
    on_exit(returncode, seconds) fires once, after both pipes hit EOF and
    the child has been reaped. returncode is negative for a signal.
    """

    def __init__(self, on_output, on_exit):
        self.on_output = on_output
        self.on_exit = on_exit
        self.process = None
        self._watches = []
        self._pending = set()
        self._returncode = None
        self._kill_source = None
        self.started = 0.0

    @property
    def running(self):
        return self.process is not None

    def start(self, argv, cwd, env=None):
        if self.running:
            raise RuntimeError("A run is already in progress. Stop it first.")
        environment = dict(os.environ if env is None else env)
        environment["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        start_new_session=True, close_fds=True)
        self.started = time.monotonic()
        self._returncode = None
        self._pending = {"stdout", "stderr", "exit"}
        for name, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr)):
            fd = stream.fileno()
            os.set_blocking(fd, False)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            self._watches.append(GLib.io_add_watch(
                fd, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                self._readable, name, stream, decoder))
        os.set_blocking(self.process.stdin.fileno(), False)
        self._watches.append(GLib.child_watch_add(GLib.PRIORITY_DEFAULT, self.process.pid,
                                                  self._exited))

    def _readable(self, fd, _condition, name, stream, decoder):
        while True:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                return True
            except OSError:
                data = b""
            if not data:
                tail = decoder.decode(b"", final=True)
                if tail:
                    self.on_output(tail, name)
                stream.close()
                self._done(name)
                return False
            text = decoder.decode(data)
            if text:
                self.on_output(text, name)

    def _exited(self, _pid, status):
        try:
            code = os.waitstatus_to_exitcode(status)
        except ValueError:
            code = status
        self._returncode = code
        self.process.returncode = code  # keep Popen from waiting on a reaped pid
        self._done("exit")

    def _done(self, part):
        self._pending.discard(part)
        if self._pending:
            return
        seconds = time.monotonic() - self.started
        if self._kill_source:
            GLib.source_remove(self._kill_source)
            self._kill_source = None
        try:
            self.process.stdin.close()
        except OSError:
            pass
        self.process = None
        self._watches = []
        self.on_exit(self._returncode, seconds)

    def send(self, text):
        """Write text to the child's stdin. Returns False if it can't take it."""
        if not self.running or self.process.stdin.closed:
            return False
        try:
            os.write(self.process.stdin.fileno(), text.encode("utf-8"))
            return True
        except (BlockingIOError, BrokenPipeError, OSError):
            return False

    def close_stdin(self):
        if self.running and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass

    def _signal(self, sig):
        if not self.running or self._returncode is not None:
            return
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass

    def stop(self):
        """SIGTERM the process group, then SIGKILL after KILL_GRACE_MS."""
        if not self.running:
            return False
        self._signal(signal.SIGTERM)
        if self._kill_source is None:
            def force():
                self._kill_source = None
                self._signal(signal.SIGKILL)
                return False
            self._kill_source = GLib.timeout_add(KILL_GRACE_MS, force)
        return True

    def kill_now(self):
        """Used on window close: end the whole group immediately."""
        self._signal(signal.SIGKILL)
