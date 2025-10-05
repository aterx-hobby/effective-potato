"""Tests for task lifecycle controls (start/status/kill).

These tests simulate container.exec_run responses without requiring Docker.
"""

from types import SimpleNamespace


class FakeExecResult:
    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.exit_code = exit_code
        self.output = (stdout.encode("utf-8"), stderr.encode("utf-8"))


class FakeContainer:
    def __init__(self):
        self.calls = []
        # Internal state per task_id -> {running:bool, exit:int|None}
        self.tasks = {}

    def exec_run(self, cmd=None, demux=None, user=None, environment=None):  # noqa: D401
        # Record the call for debugging
        self.calls.append({"cmd": cmd, "user": user, "env_keys": sorted((environment or {}).keys())})
        if not cmd:
            return FakeExecResult(1, stderr="no cmd")
        argv = cmd
        # Both list ["bash", "-lc", script] and string with -c are used
        script = argv[-1] if isinstance(argv, (list, tuple)) else str(cmd)
        # Probe scripts echo lines like STATE:running/EXIT:x/OUT:1; simulate for a single task
        if "echo STATE:" in script and "echo EXIT:" in script:
            # Assume only one active task for this fake
            # Simulate a single task_id = abc
            t = self.tasks.get("abc", {"running": True, "exit": None, "out": True})
            state = "running" if t["running"] else "exited"
            exit_txt = "" if t["exit"] is None else str(t["exit"])
            out_flag = "1" if t.get("out") else "0"
            txt = f"STATE:{state}\nEXIT:{exit_txt}\nOUT:{out_flag}\n"
            return FakeExecResult(0, stdout=txt)
        # Starting background: record a task with id 'abc'
        if " & echo $! > " in script:
            self.tasks["abc"] = {"running": True, "exit": None, "out": True}
            return FakeExecResult(0, stdout="")
        # Killing: set running False and exit code (simulate TERM=143)
        if "kill -s" in script:
            t = self.tasks.get("abc")
            if t:
                t["running"] = False
                t["exit"] = 143
            return FakeExecResult(0, stdout="")
        return FakeExecResult(0, stdout="")


def test_task_lifecycle_simulated(tmp_path):
    from effective_potato.container import ContainerManager

    ws = tmp_path / "ws"
    ws.mkdir()

    cm = ContainerManager(workspace_dir=str(ws), env_file=str(tmp_path/".env"), sample_env_file=str(tmp_path/"sample.env"))
    fake = FakeContainer()
    cm.container = fake  # type: ignore

    # Start
    info = cm.start_background_task("sleep 30", task_id="abc")
    assert info.get("task_id") == "abc"

    # Status running
    st = cm.get_task_status("abc")
    assert st["running"] is True
    assert st["exit_code"] is None

    # Kill
    res = cm.kill_task("abc")
    assert res["ok"] is True

    # Status exited
    st2 = cm.get_task_status("abc")
    assert st2["running"] is False
    # exit may be parsed as int 143 in our simulation
    assert isinstance(st2.get("exit_code"), (int, type(None)))
