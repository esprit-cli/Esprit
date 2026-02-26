from pathlib import Path


def test_installers_include_start_command_hint() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    sh_script = (repo_root / "scripts" / "install.sh").read_text(encoding="utf-8")
    ps1_script = (repo_root / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "start Esprit" in sh_script
    assert "start Esprit" in ps1_script
