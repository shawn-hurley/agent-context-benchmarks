from pathlib import Path

import pytest
import yaml

from acb.cli import main
from acb.config import RunConfig


@pytest.mark.parametrize("output", ["runs", "../results", "absolute"])
def test_clean_config_paths_and_preview(tmp_path, monkeypatch, capsys, output):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = tmp_path / "absolute" if output == "absolute" else elsewhere / output
    target = target.resolve()
    target.mkdir()
    (target / "trial").mkdir()
    (target / ".cache").mkdir()
    (target / ".cache" / "keep").write_text("cached")
    (target / ".gitkeep").touch()
    config = config_dir / "run.yaml"
    config.write_text(yaml.safe_dump(dict(run_id="test", benchmark="harbor", harness="pi",
                                         model="unconfigured", output_dir=str(target) if output == "absolute" else output)))
    (config_dir / "runs").mkdir()
    (config_dir / "runs" / "unrelated").touch()
    monkeypatch.chdir(elsewhere)
    assert RunConfig.from_file(config).output_path() == target
    main(["clean", "--config", str(config), "--dry-run"])
    assert str(target) in capsys.readouterr().out
    assert (target / "trial").exists()
    main(["clean", "--config", str(config), "--yes"])
    assert not (target / "trial").exists()
    assert (target / ".cache" / "keep").read_text() == "cached"
    assert (target / ".gitkeep").exists()
    assert (config_dir / "runs" / "unrelated").exists()


def test_clean_default_and_symlink(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").touch()
    (runs / "linked").symlink_to(outside, target_is_directory=True)
    main(["clean", "--yes"])
    assert not (runs / "linked").exists()
    assert (outside / "keep").exists()


def test_clean_rejects_ambiguous_target():
    with pytest.raises(SystemExit) as error:
        main(["clean", "--config", "run.yaml", "--output-dir", "runs"])
    assert error.value.code == 2
