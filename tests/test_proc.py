import sys

from ptt import proc


def test_run_captures_stdout_and_returncode():
    r = proc.run([sys.executable, "-c", "print('hi')"])
    assert r.returncode == 0
    assert "hi" in r.stdout


def test_run_nonzero_returncode_captured():
    r = proc.run([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"])
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


def test_run_passes_stdin():
    r = proc.run([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
                 input="echoed")
    assert r.stdout == "echoed"
