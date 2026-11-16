"""Interpreter selection and the streaming runner (no display needed)."""
import os
import signal
import sys
import time

import pytest
from gi.repository import GLib

import runner


def fake_which(*available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


@pytest.mark.parametrize("line, expected", [
    ("#!/usr/bin/env python3", ["python3"]),
    ("#!/usr/bin/env -S node --no-warnings", ["node", "--no-warnings"]),
    ("#!/bin/bash -e", ["/bin/bash", "-e"]),
    ("#! /usr/bin/perl", ["/usr/bin/perl"]),
    ("print('no shebang')", None),
    ("#!", None),
])
def test_parse_shebang(line, expected):
    assert runner.parse_shebang(line + "\nrest\n") == expected


@pytest.mark.parametrize("name, available, expected", [
    ("a.py", ("python3",), ["/usr/bin/python3", "{f}"]),
    ("a.py", ("python",), ["/usr/bin/python", "{f}"]),
    ("a.sh", ("bash", "sh"), ["/usr/bin/bash", "{f}"]),
    ("a.sh", ("sh",), ["/usr/bin/sh", "{f}"]),
    ("a.js", ("node",), ["/usr/bin/node", "{f}"]),
    ("a.ts", ("ts-node",), ["/usr/bin/ts-node", "{f}"]),
    ("a.ts", ("deno", "ts-node"), ["/usr/bin/deno", "run", "{f}"]),
    ("a.rb", ("ruby",), ["/usr/bin/ruby", "{f}"]),
    ("a.pl", ("perl",), ["/usr/bin/perl", "{f}"]),
    ("a.lua", ("lua",), ["/usr/bin/lua", "{f}"]),
    ("main.go", ("go",), ["/usr/bin/go", "run", "{f}"]),
    ("A.PY", ("python3",), ["/usr/bin/python3", "{f}"]),
])
def test_extension_picks_interpreter(tmp_path, name, available, expected):
    path = tmp_path / name
    argv = runner.command_for(path, "", fake_which(*available))
    assert argv == [part.replace("{f}", str(path)) for part in expected]


def test_shebang_beats_extension(tmp_path):
    path = tmp_path / "tool.py"
    argv = runner.command_for(path, "#!/usr/bin/env bash\necho hi\n", fake_which("bash", "python3"))
    assert argv == ["/usr/bin/bash", str(path)]


def test_extensionless_file_uses_content_detection(tmp_path):
    path = tmp_path / "script"
    argv = runner.command_for(path, "import os\nprint(os.getcwd())\n", fake_which("python3"))
    assert argv == ["/usr/bin/python3", str(path)]


def test_missing_interpreter_says_what_to_install(tmp_path):
    with pytest.raises(ValueError, match="Install node"):
        runner.command_for(tmp_path / "app.js", "", fake_which())


def test_missing_shebang_interpreter(tmp_path):
    with pytest.raises(ValueError, match="'ruby'"):
        runner.command_for(tmp_path / "x", "#!/usr/bin/env ruby\n", fake_which())


def test_unknown_file_explains(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello\n")
    with pytest.raises(ValueError, match="doesn't know how to run"):
        runner.command_for(path, "hello\n", fake_which("python3"))


def test_executable_without_known_type_runs_directly(tmp_path):
    path = tmp_path / "thing"
    path.write_text("binary-ish\n")
    path.chmod(0o755)
    assert runner.command_for(path, "binary-ish\n", fake_which()) == [str(path)]


def test_describe_exit_and_duration():
    assert runner.describe_exit(0) == "exited with code 0"
    assert runner.describe_exit(3) == "exited with code 3"
    assert runner.describe_exit(-signal.SIGTERM) == "stopped by SIGTERM"
    assert runner.format_duration(1.234) == "1.23 s"
    assert runner.format_duration(75) == "1 min 15.0 s"


def live_members(pgid):
    # Zombies (state Z) are dead and only wait for their parent to reap them.
    members = []
    for stat in os.listdir("/proc"):
        if stat.isdigit():
            try:
                fields = open(f"/proc/{stat}/stat").read().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if int(fields[2]) == pgid and fields[0] != "Z":
                members.append(int(stat))
    return members


def run_loop(start, timeout=10, during=None):
    loop = GLib.MainLoop()
    events = {"chunks": [], "exit": None, "ticks": 0}

    def on_output(text, stream):
        events["chunks"].append((stream, text, time.monotonic()))

    def on_exit(code, seconds):
        events["exit"] = (code, seconds)
        loop.quit()

    def tick():  # proves the loop keeps turning while the child runs
        events["ticks"] += 1
        return True

    proc = runner.Runner(on_output, on_exit)
    start(proc)
    GLib.timeout_add(20, tick)
    if during:
        during(proc)
    GLib.timeout_add_seconds(timeout, loop.quit)
    loop.run()
    return proc, events


def test_streams_stdout_and_stderr_and_exit_code(tmp_path):
    script = tmp_path / "demo.py"
    script.write_text("import sys, time, os\n"
                      "print('out 1'); time.sleep(0.3)\n"
                      "print('err 1', file=sys.stderr); time.sleep(0.3)\n"
                      "print('cwd', os.getcwd()); print('unbuffered', os.environ.get('PYTHONUNBUFFERED'))\n"
                      "sys.exit(3)\n")
    _, events = run_loop(lambda proc: proc.start([sys.executable, str(script)], str(tmp_path)))
    code, seconds = events["exit"]
    assert code == 3 and seconds >= 0.5
    out = "".join(text for stream, text, _ in events["chunks"] if stream == "stdout")
    err = "".join(text for stream, text, _ in events["chunks"] if stream == "stderr")
    assert "out 1" in out and f"cwd {tmp_path}" in out and "unbuffered 1" in out
    assert err == "err 1\n"
    first, last = events["chunks"][0][2], events["chunks"][-1][2]
    assert last - first >= 0.5, "output should arrive as it is printed, not at exit"
    assert events["ticks"] > 10


def test_stop_kills_process_group(tmp_path):
    script = tmp_path / "loop.sh"
    script.write_text("sleep 300 &\nwhile true; do echo tick; sleep 0.1; done\n")
    pids = {}

    def during(proc):
        pids["pid"] = proc.process.pid
        GLib.timeout_add(400, lambda: proc.stop() and False)

    _, events = run_loop(lambda proc: proc.start(["bash", str(script)], str(tmp_path)), during=during)
    code, seconds = events["exit"]
    assert code == -signal.SIGTERM
    assert seconds < 3
    time.sleep(0.2)
    assert live_members(pids["pid"]) == [], "the background sleep should die with the group"


def test_stop_escalates_to_sigkill(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "KILL_GRACE_MS", 300)
    script = tmp_path / "stubborn.py"
    script.write_text("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                      "print('ready', flush=True)\nwhile True: time.sleep(0.05)\n")

    def during(proc):
        GLib.timeout_add(400, lambda: proc.stop() and False)

    _, events = run_loop(lambda proc: proc.start([sys.executable, str(script)], str(tmp_path)),
                         during=during)
    assert events["exit"][0] == -signal.SIGKILL


def test_stdin_line_reaches_program(tmp_path):
    script = tmp_path / "echo.py"
    script.write_text("name = input()\nprint('hello', name)\n")

    def during(proc):
        GLib.timeout_add(200, lambda: proc.send("Danny\n") and False)

    _, events = run_loop(lambda proc: proc.start([sys.executable, str(script)], str(tmp_path)),
                         during=during)
    assert events["exit"][0] == 0
    assert "hello Danny" in "".join(text for _, text, _ in events["chunks"])


def test_second_start_is_refused(tmp_path):
    proc = runner.Runner(lambda *_: None, lambda *_: None)
    proc.start(["sleep", "5"], str(tmp_path))
    try:
        with pytest.raises(RuntimeError):
            proc.start(["true"], str(tmp_path))
    finally:
        proc.kill_now()
