import yaml

from aethel.safety.permissions import Permissions, default_manifest


def _perms(tmp_path, **overrides):
    manifest = default_manifest()
    manifest["filesystem"]["allowed_read_paths"] = [str(tmp_path / "read")]
    manifest["filesystem"]["allowed_write_paths"] = [str(tmp_path / "write")]
    manifest["filesystem"]["forbidden_paths"] = [str(tmp_path / "secret")]
    manifest.update(overrides)
    path = tmp_path / "permissions.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return Permissions(path)


def test_paths_allow_ask_deny(tmp_path):
    p = _perms(tmp_path)
    assert p.check_path(str(tmp_path / "read" / "a.txt"), "read").verdict == "allow"
    assert p.check_path(str(tmp_path / "write" / "b.txt"), "write").verdict == "allow"
    assert p.check_path(str(tmp_path / "write" / "b.txt"), "read").verdict == "ask"  # not in read list
    assert p.check_path(str(tmp_path / "elsewhere.txt"), "write").verdict == "ask"
    assert p.check_path(str(tmp_path / "secret" / "k"), "read").verdict == "deny"
    assert p.check_path(str(tmp_path / "write" / ".." / "secret" / "k"), "write").verdict == "deny"


def test_prefix_is_not_containment(tmp_path):
    p = _perms(tmp_path)
    assert p.check_path(str(tmp_path / "write-evil" / "x"), "write").verdict == "ask"


def test_commands(tmp_path):
    p = _perms(tmp_path)
    assert p.check_command("git status").verdict == "allow"
    assert p.check_command("python -c \"print(1)\"").verdict == "deny"
    assert p.check_command("python -cprint(1)").verdict == "deny"  # glued flag
    assert p.check_command("python \"-c\" x").verdict == "deny"     # quoted flag
    assert p.check_command("powershell -ec ZQBjAGgAbwA=").verdict == "deny"  # PowerShell -ec shorthand
    assert p.check_command("python -\"c\"print(1)").verdict == "deny"  # quotes around flag
    assert p.check_command("python -^c print(1)").verdict == "deny"  # cmd escape char
    assert p.check_command("python '-'c x").verdict == "deny"  # quotes around dash
    assert p.check_command("dir | findstr x").verdict == "deny"
    assert p.check_command("shutdown /s").verdict == "deny"
    assert p.check_command("winget install foo").verdict == "ask"
    assert p.check_command("winget install --exact foo").verdict == "ask"  # --exact not caught by -e


def test_missing_file_is_created_with_defaults(tmp_path):
    path = tmp_path / "sub" / "permissions.yaml"
    Permissions(path)
    assert path.is_file()
    assert "filesystem" in yaml.safe_load(path.read_text(encoding="utf-8"))


def _wide_open(tmp_path):
    """A manifest that allows reading and writing everywhere, including the
    protected folders by name: the hard rules must still win."""
    from aethel.paths import PROJECT_ROOT, aethel_home
    home = aethel_home()
    fs = {"allowed_read_paths": ["*", str(home), str(PROJECT_ROOT), str(tmp_path)],
          "allowed_write_paths": ["*", str(home), str(PROJECT_ROOT), str(tmp_path)],
          "forbidden_paths": []}
    return _perms(tmp_path, filesystem=fs), home, PROJECT_ROOT


def test_protected_write_roots_are_denied_whatever_the_manifest_says(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    p, home, project = _wide_open(tmp_path)
    for target in [home / "permissions.yaml",
                   home / "scratch" / ".." / "permissions.yaml",
                   home / "aethel.db",
                   home / "logs" / "backend.log",
                   project / "backend" / "x.py",
                   tmp_path / "anywhere" / ".env",
                   tmp_path / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "a.bat"]:
        assert p.check_path(str(target), "write").verdict == "deny", target


def test_scratch_still_follows_the_manifest(tmp_path):
    p, home, _ = _wide_open(tmp_path)
    assert p.check_path(str(home / "scratch" / "a.txt"), "write").verdict == "allow"
    narrow = _perms(tmp_path)
    assert narrow.check_path(str(home / "scratch" / "a.txt"), "write").verdict == "ask"


def test_protected_reads_are_denied_whatever_the_manifest_says(tmp_path):
    p, home, project = _wide_open(tmp_path)
    assert p.check_path(str(home / "aethel.db"), "read").verdict == "deny"
    assert p.check_path(str(tmp_path / "proj" / ".env"), "read").verdict == "deny"
    assert p.check_path(str(home / "permissions.yaml"), "read").verdict == "allow"  # reading config is fine
    assert p.check_path(str(project / "README.md"), "read").verdict == "allow"


def _shell_perms(tmp_path, monkeypatch, allowed=None):
    """Default manifest, with the home folder (and so ~/.ssh and the shell's
    working directory) pointed at tmp."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    manifest = default_manifest()
    if allowed is not None:
        manifest["shell"]["allowed_commands"] = allowed
    path = tmp_path / "permissions.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return Permissions(path)


def test_allowed_command_path_arguments_are_checked(tmp_path, monkeypatch):
    p = _shell_perms(tmp_path, monkeypatch)
    assert p.check_command(r"type %USERPROFILE%\.ssh\id_rsa").verdict == "deny"
    assert p.check_command(r"type .ssh\id_rsa").verdict == "deny"
    assert p.check_command(r"type ~\.ssh\id_rsa").verdict == "deny"
    assert p.check_command(r'type "%USERPROFILE%\.ssh\id_rsa"').verdict == "deny"
    assert p.check_command(r"dir C:\Windows\System32\config").verdict == "deny"
    assert p.check_command(r"type C:\somewhere\else.txt").verdict == "ask"   # outside the read folders
    assert p.check_command(r"dir /s /b").verdict == "allow"                   # cmd switches aren't paths
    assert p.check_command("git status").verdict == "allow"
    assert p.check_command("echo hi").verdict == "allow"


def test_output_file_options_are_denied(tmp_path, monkeypatch):
    p = _shell_perms(tmp_path, monkeypatch)
    assert p.check_command(r"git log --output=C:\x").verdict == "deny"
    assert p.check_command("git diff --no-index NUL x --output=y").verdict == "deny"
    assert p.check_command("git diff --output y").verdict == "deny"
    assert p.check_command("git log -o y").verdict == "deny"
    assert p.check_command("git log -oy").verdict == "deny"
    assert p.check_command("pandoc a.md --output-directory x").verdict == "deny"
    assert p.check_command("echo hi >> x").verdict == "deny"


def test_interpreters_are_never_auto_allowed(tmp_path, monkeypatch):
    p = _shell_perms(tmp_path, monkeypatch, allowed=["python", "node", "powershell", "start", "*"])
    assert p.check_command("python script.py").verdict == "ask"
    assert p.check_command('"Python.EXE" script.py').verdict == "ask"
    assert p.check_command("node app.js").verdict == "ask"
    assert p.check_command("start notepad").verdict == "ask"
    assert p.check_command("pwsh x.ps1").verdict == "ask"          # allowed only via "*"
    assert p.check_command('python -c "print(1)"').verdict == "deny"  # forbidden arguments still deny first
    assert p.check_command("git status").verdict == "allow"
