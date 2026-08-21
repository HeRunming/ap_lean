"""Test deterministic local Lean toolchain discovery."""

from leanflow_cli.runtime.toolchain_env import add_lean_toolchain_env, discover_lean_bin


def test_discovers_workspace_ancestor_elan_home(tmp_path):
    project = tmp_path / "workspace" / "project"
    lean_bin = tmp_path / "workspace" / ".elan-home" / "bin"
    project.mkdir(parents=True)
    lean_bin.mkdir(parents=True)
    for name in ("lake", "lean"):
        executable = lean_bin / name
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)

    discovered = discover_lean_bin(project, environ={"PATH": ""})
    child_env = add_lean_toolchain_env(
        {"PATH": "/usr/bin"}, project_root=project, environ={"PATH": ""}
    )

    assert discovered == lean_bin
    assert child_env["PATH"].split(":")[0] == str(lean_bin)
    assert child_env["ELAN_HOME"] == str(lean_bin.parent)
