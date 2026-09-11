"""Guard the HDP remote Lake wrapper's host/remote contract."""

import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from leanflow_cli.formalization import remote_warm_probe
from tools.environments.ssh import SSHEnvironment


def test_remote_lake_wrapper_uses_passwordless_target_and_data_mount():
    """Keep all Lake invocations on the designated remote checkout."""
    wrapper = (Path(__file__).parents[2] / "remote-bin" / "lake").read_text(encoding="utf-8")

    assert "host_root=/Users/blackbox/m2f/fate-x-work" in wrapper
    assert "remote_root=/data/hrm/fate-x-work" in wrapper
    assert "-p 49322" in wrapper
    assert "hrm@140.143.244.199" in wrapper
    assert "-i /Users/blackbox/.ssh" not in wrapper
    assert "/usr/bin/timeout --signal=TERM --kill-after=1s" in wrapper
    assert "${remote_timeout}s ${remote_lake_argv}" in wrapper
    assert "LEANFLOW_REMOTE_LEAN_TIMEOUT_S" in wrapper
    assert "remote_timeout_max=3600" in wrapper
    assert "/usr/bin/timeout for process cleanup" in wrapper


def test_remote_lake_wrapper_has_a_server_side_no_residue_policy():
    """Keep remote descendants bounded after local SSH interruption."""
    wrapper = (Path(__file__).parents[2] / "remote-bin" / "lake").read_text(encoding="utf-8")

    assert "remote_timeout_default=120" in wrapper
    assert "--signal=TERM" in wrapper
    assert "--kill-after=1s" in wrapper
    assert "--foreground" not in wrapper
    assert "process group" in wrapper
    assert "Invalid values never disable the guard" in wrapper
    assert "trap cleanup_on_exit EXIT" in wrapper
    assert "trap on_signal HUP INT TERM" in wrapper
    assert "-name 'StatementCandidate_*.lean'" in wrapper
    assert "-name 'ZeroCostCandidate_*.lean'" in wrapper
    assert "-name 'Main.lean'" not in wrapper


def test_remote_lake_wrapper_cleanup_allowlist_is_candidate_only():
    """Pin cleanup to exact invocation candidates, never the shared checkout."""
    wrapper = (Path(__file__).parents[2] / "remote-bin" / "lake").read_text(encoding="utf-8")

    cleanup_lines = [line for line in wrapper.splitlines() if "-name " in line]
    assert cleanup_lines
    assert all(
        "StatementCandidate_*.lean" in line or "ZeroCostCandidate_*.lean" in line
        for line in cleanup_lines
    )
    assert all("Main.lean" not in line for line in cleanup_lines)
    assert "candidate_remote_paths=()" in wrapper
    assert 'for arg in "${mapped_args[@]}"; do' in wrapper
    assert 'candidate_remote_paths+=("$candidate_path")' in wrapper
    assert "find -- $quoted_candidate -maxdepth 0 -type f" in wrapper
    assert "find '$remote_root' -type f" not in wrapper


def test_remote_lake_wrapper_rejects_cleanup_path_traversal():
    """Keep direct wrapper arguments from escaping the remote project root."""
    wrapper = (Path(__file__).parents[2] / "remote-bin" / "lake").read_text(encoding="utf-8")

    assert '*"/../"*' in wrapper
    assert '*"/.."' in wrapper
    assert '*"/./"*' in wrapper


def test_remote_lake_wrapper_allows_empty_candidate_allowlist_under_nounset():
    """Do not crash before remote Lake when no candidate path was supplied."""
    wrapper = (Path(__file__).parents[2] / "remote-bin" / "lake").read_text(encoding="utf-8")
    preamble = wrapper.split("# The local checkout is authoritative", maxsplit=1)[0]

    completed = subprocess.run(
        ["bash", "-u", "-c", preamble, "remote-bin/lake", "Main.lean"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert wrapper.count("if ((${#mapped_args[@]} > 0)); then") == 2


def test_remote_lake_wrapper_executes_embedded_argv_and_writes_receipt(tmp_path):
    """Execute the generated remote shell with fake SSH/Lake and verify its receipt."""
    repo = Path(__file__).parents[2]
    wrapper = (repo / "remote-bin/lake").read_text(encoding="utf-8")
    fake = tmp_path / "fake-command"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, subprocess, sys\n"
        "mode = sys.argv[1]\n"
        "if mode == 'rsync':\n"
        "    raise SystemExit(0)\n"
        "script = sys.argv[-1]\n"
        f"script = script.replace('/data/hrm/fate-x-work', {str(tmp_path)!r})\n"
        f"script = script.replace('/home/hrm/.elan/bin/lake', {str(tmp_path / 'fake-lake')!r})\n"
        "result = subprocess.run(['/bin/bash', '-c', script], text=True, capture_output=True, env=os.environ)\n"
        "sys.stdout.write(result.stdout)\n"
        "sys.stderr.write(result.stderr)\n"
        "raise SystemExit(result.returncode)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    fake_lake = tmp_path / "fake-lake"
    fake_lake.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "pathlib.Path(os.environ['LAKE_ARGV_LOG']).write_text(repr(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    fake_lake.chmod(0o755)
    fake_timeout = tmp_path / "fake-timeout"
    fake_timeout.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "while args and args[0].startswith('--'):\n"
        "    args = args[1:]\n"
        "args = args[1:]\n"
        "raise SystemExit(subprocess.run(args, check=False).returncode)\n",
        encoding="utf-8",
    )
    fake_timeout.chmod(0o755)
    target = tmp_path / "HDP/StatementCandidate_invocation.lean"
    target.parent.mkdir(parents=True)
    target.write_text("candidate\n", encoding="utf-8")
    target_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    receipts = tmp_path / ".leanflow/remote-compile-receipts"
    receipts.mkdir(parents=True)
    instrumented = tmp_path / "lake"
    instrumented.write_text(
        wrapper.replace("/usr/bin/ssh", f"{fake} ssh")
        .replace("/usr/bin/rsync", f"{fake} rsync")
        .replace("/usr/bin/timeout", str(fake_timeout)),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "bash",
            str(instrumented),
            "env",
            "lean",
            "/Users/blackbox/m2f/fate-x-work/HDP/StatementCandidate_invocation.lean",
        ],
        env={**os.environ, "LAKE_ARGV_LOG": str(tmp_path / "lake-argv")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "lake-argv").read_text(encoding="utf-8") == repr(
        ["env", "lean", str(target)]
    )
    receipt_files = sorted(receipts.glob("remote-compile-*.json"))
    assert len(receipt_files) == 1
    receipt = json.loads(receipt_files[0].read_text(encoding="utf-8"))
    assert receipt["exit_code"] == 0
    assert receipt["target_path"].endswith("/HDP/StatementCandidate_invocation.lean")
    assert receipt["target_sha256"] == target_sha
    assert (
        base64.b64decode(receipt["command_base64"])
        .decode()
        .endswith("/HDP/StatementCandidate_invocation.lean")
    )


def _project_sync_argv(entrypoint, tmp_path, monkeypatch):
    """Capture production sync arguments without sending SSH or running Lean."""
    if entrypoint == "warm":
        return remote_warm_probe._sync_command(tmp_path), []
    if entrypoint == "terminal":
        environment = object.__new__(SSHEnvironment)
        environment.remote_root = "/data/hrm/fate-x-work"
        environment.host = "140.143.244.199"
        environment.user = "hrm"
        environment.port = 49322
        environment.key_path = ""
        environment.control_socket = tmp_path / "ssh.sock"
        calls = []
        with monkeypatch.context() as patches:
            patches.setenv("TERMINAL_SSH_SYNC_PROJECT", "true")
            patches.setenv("LEANFLOW_PROJECT_ROOT", str(tmp_path))
            patches.setattr(
                subprocess,
                "run",
                lambda command, **_kwargs: calls.append(command)
                or subprocess.CompletedProcess(command, 0, "", ""),
            )
            assert environment._sync_project_to_remote() == ""
        return calls[0], []

    repo = Path(__file__).parents[2]
    wrapper_dir = tmp_path / "remote-bin"
    wrapper_dir.mkdir()
    rules_dir = tmp_path / "core"
    rules_dir.mkdir()
    shutil.copyfile(
        repo / "core/remote-project-sync.exclude", rules_dir / "remote-project-sync.exclude"
    )
    recorder = tmp_path / "record-command"
    recorder.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['SYNC_ARGV_LOG'], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    recorder.chmod(0o755)
    wrapper = (repo / "remote-bin/lake").read_text(encoding="utf-8")
    for executable in ("ssh", "rsync"):
        wrapper = wrapper.replace(
            f"/usr/bin/{executable}", f"{shlex.quote(str(recorder))} {executable}"
        )
    instrumented = wrapper_dir / "lake"
    instrumented.write_text(wrapper, encoding="utf-8")
    command_log = tmp_path / "argv.jsonl"
    completed = subprocess.run(
        [
            "bash",
            str(instrumented),
            "env",
            "lean",
            "/Users/blackbox/m2f/fate-x-work/HDP/StatementCandidate_invocation.lean",
        ],
        env={**os.environ, "SYNC_ARGV_LOG": str(command_log)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    calls = [json.loads(line) for line in command_log.read_text(encoding="utf-8").splitlines()]
    return next(command for command in calls if command[0] == "rsync"), calls


@pytest.mark.parametrize("entrypoint", ["lake", "warm", "terminal"])
def test_compile_sync_preserves_remote_runtime_and_campaign(entrypoint, tmp_path, monkeypatch):
    """Replay actual sync filters locally to prove state is protected and sources transfer."""
    rsync = shutil.which("rsync")
    if not rsync:
        pytest.skip("local rsync is required to exercise its real filter semantics")
    command, calls = _project_sync_argv(entrypoint, tmp_path, monkeypatch)
    protected = (
        ".leanflow-home/config.yaml",
        ".leanflow-home/.env",
        ".leanflow/workflow-state/events.jsonl",
        ".env.remote-lean",
        ".env",
        ".codex-home/config.toml",
        ".elan/bin/lean",
        ".lake/build/cache.olean",
        ".git/config",
        ".venv/bin/python",
        "bin/leanflow-remote",
        "remote-bin/lake",
        "HDP/Environments/campaign.json",
        "HDP/Environments/campaign.json.lock",
        "HDP/Environments/campaign.json.bak",
        "HDP/Environments/campaign.pre-fresh.110817.json",
        "HDP/Environments/campaign.canary-repartition-backup-20260909T1207.json",
        "HDP/Environments/.campaign_123.tmp",
        "HDP/Environments/campaign-state/worker.json",
        "HDP/Environments/campaign.lock",
        "FateXWork/Questions/campaign.json",
    )
    sources = (
        "HDP.lean",
        "HDP/Main.lean",
        "HDP/StatementCandidate_invocation.lean",
        "HDP/ZeroCostCandidate_invocation.lean",
        "HDP/StatementCandidate_other_worker.lean",
        "HDP/Blueprint.md",
        "HDP/Environments/source.json",
        "HDP/Environments/campaignLemma.lean",
        "lakefile.lean",
        "lakefile.toml",
        "lake-manifest.json",
        "lean-toolchain",
    )
    source_root = tmp_path / "source"
    remote_root = tmp_path / "destination"
    for relative in (*protected, *sources):
        for root, content in ((source_root, "local-new"), (remote_root, "remote-owned")):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    args = command[1:-2]
    ssh_index = args.index("-e")
    del args[ssh_index : ssh_index + 2]
    completed = subprocess.run(
        [rsync, *args, f"{source_root}/", f"{remote_root}/"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for relative in protected:
        assert (remote_root / relative).read_text(encoding="utf-8") == "remote-owned", relative
    for relative in sources:
        assert (remote_root / relative).read_text(encoding="utf-8") == "local-new", relative
    if entrypoint == "lake":
        remote_compile = next(command for command in calls if command[0] == "ssh")
        remote_script = remote_compile[-1]
        assert remote_compile[-2] == "hrm@140.143.244.199"
        assert (
            "/home/hrm/.elan/bin/lake env lean /data/hrm/fate-x-work/HDP/StatementCandidate_invocation.lean"
            in remote_script
        )
        assert "trap cleanup EXIT" in remote_script
        assert "StatementCandidate_other_worker.lean" not in remote_script
