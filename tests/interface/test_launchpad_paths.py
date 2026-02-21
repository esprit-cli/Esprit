import asyncio

from esprit.interface.launchpad import DirectorySuggester, LaunchpadApp


def test_directory_suggester_completes_relative_paths(tmp_path) -> None:
    (tmp_path / "ScanTarget").mkdir()

    suggester = DirectorySuggester(str(tmp_path))
    suggestion = asyncio.run(suggester.get_suggestion("sca"))

    assert suggestion == "ScanTarget/"


def test_directory_suggester_hides_hidden_directories_by_default(tmp_path) -> None:
    (tmp_path / ".secret").mkdir()
    (tmp_path / "service").mkdir()

    suggester = DirectorySuggester(str(tmp_path))

    assert asyncio.run(suggester.get_suggestion("s")) == "service/"
    assert asyncio.run(suggester.get_suggestion(".")) == ".secret/"


def test_directory_suggester_supports_tilde_paths(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / "workspace").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    suggester = DirectorySuggester(str(tmp_path))

    assert asyncio.run(suggester.get_suggestion("~/wo")) == "~/workspace/"
    assert asyncio.run(suggester.get_suggestion("~")) == "~/"


def test_resolve_scan_path_supports_relative_tilde_and_default(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / "repo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    project = tmp_path / "project"
    project.mkdir()

    app = LaunchpadApp()
    app._cwd = str(tmp_path)

    assert app._resolve_scan_path("project") == str(project.resolve())
    assert app._resolve_scan_path("~/repo") == str((home / "repo").resolve())
    assert app._resolve_scan_path("", use_cwd_if_empty=True) == str(tmp_path.resolve())
    assert app._resolve_scan_path("missing") is None


def test_scan_target_menu_hints_call_out_local_path_support() -> None:
    app = LaunchpadApp()
    entries = app._build_scan_target_entries()

    target = next(entry for entry in entries if entry.key == "scan_target_input")
    local = next(entry for entry in entries if entry.key == "scan_local_input")

    assert "local path" in target.hint.lower()
    assert "autocomplete" in local.hint.lower()
