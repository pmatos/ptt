import subprocess
import sys
import time

from ptt import proc


def test_run_captures_stdout_and_returncode():
    r = proc.run([sys.executable, "-c", "print('hi')"])
    assert r.returncode == 0
    assert "hi" in r.stdout


def test_run_nonzero_returncode_captured():
    r = proc.run(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"]
    )
    assert r.returncode == 2
    assert "boom" in r.stderr


def test_run_appends_to_log(tmp_path):
    log = tmp_path / "git.log"
    proc.run([sys.executable, "-c", "print('first')"], log_path=log)
    proc.run([sys.executable, "-c", "print('second')"], log_path=log)
    text = log.read_text()
    assert "first" in text and "second" in text
    # both commands recorded (log grew, not overwritten)
    assert text.count("$ ") >= 2


def test_run_timeout_returns_124():
    r = proc.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
    assert r.returncode == 124
    assert "timed out after" in r.stderr


def test_run_passes_stdin():
    r = proc.run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        input="echoed",
    )
    assert r.stdout == "echoed"


def test_terminate_group_noop_when_already_exited():
    p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    p.wait()
    # The whole group is already gone; terminate_group must swallow the lookup miss.
    proc.terminate_group(p)


def test_terminate_group_kills_child_that_survives_parent(tmp_path):
    # A child that ignores SIGTERM must still be SIGKILLed even when the parent
    # exits promptly on SIGTERM — otherwise the group-kill is skipped (the parent
    # wait returns within the grace period) and the child leaks past the timeout.
    child_py = tmp_path / "child.py"
    child_py.write_text(
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "open(sys.argv[1], 'w').close()\n"  # child_ready (SIG_IGN installed)
        "time.sleep(1.0)\n"
        "open(sys.argv[2], 'w').close()\n"  # marker (only if it survived)
    )
    parent_py = tmp_path / "parent.py"
    parent_py.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]])\n"
        "time.sleep(30)\n"  # parent stays alive; exits on SIGTERM (default disposition)
    )
    child_ready = tmp_path / "child_ready"
    marker = tmp_path / "child_survived"
    p = subprocess.Popen(
        [sys.executable, str(parent_py), str(child_py), str(child_ready), str(marker)],
        start_new_session=True,
    )
    for _ in range(100):  # wait until the child has installed SIG_IGN
        if child_ready.exists():
            break
        time.sleep(0.05)
    assert child_ready.exists()
    proc.terminate_group(p)
    time.sleep(1.6)  # past the child's 1.0s marker delay
    assert not marker.exists()
