import sys
import time

from ptt import command


def test_run_command_captures_stdout_exit0(tmp_path):
    out, err = tmp_path / "o.log", tmp_path / "e.log"
    rc, timed_out = command.run_command(
        [sys.executable, "-c", "print('digest body')"], tmp_path, out, err, 30
    )
    assert (rc, timed_out) == (0, False)
    assert "digest body" in out.read_text()


def test_run_command_nonzero_exit_captures_stderr(tmp_path):
    out, err = tmp_path / "o.log", tmp_path / "e.log"
    rc, timed_out = command.run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        tmp_path,
        out,
        err,
        30,
    )
    assert (rc, timed_out) == (3, False)
    assert "boom" in err.read_text()


def test_run_command_runs_in_cwd(tmp_path):
    out, err = tmp_path / "o.log", tmp_path / "e.log"
    command.run_command(
        [sys.executable, "-c", "import os; print(os.getcwd())"], tmp_path, out, err, 30
    )
    assert str(tmp_path) in out.read_text()


def test_run_command_timeout_kills_whole_group(tmp_path):
    # The command spawns a grandchild that would touch `marker` after 1.5s, then
    # blocks. We time out at 0.5s; a group-wide kill must reap the grandchild too,
    # so `marker` never appears. If only the direct child were killed, the surviving
    # grandchild would create it and the assertion below would fail.
    marker = tmp_path / "grandchild.touched"
    script = tmp_path / "spawn.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c',\n"
        f"    \"import time; time.sleep(1.5); open(r'{marker}', 'w').close()\"])\n"
        "time.sleep(30)\n"
    )
    out, err = tmp_path / "o.log", tmp_path / "e.log"
    rc, timed_out = command.run_command(
        [sys.executable, str(script)], tmp_path, out, err, 0.5
    )
    assert (rc, timed_out) == (124, True)
    time.sleep(2.0)  # well past the grandchild's 1.5s delay
    assert not marker.exists()
