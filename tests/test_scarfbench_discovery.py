"""ScarfBench instance discovery and selection."""

from unittest.mock import patch

from acb.benchmarks.scarfbench import ScarfBench, _copy_source_to_container


def test_discovery_uses_limit_across_apps_and_preserves_explicit_selection(tmp_path):
    for layer, app in (("business_domain", "cart"), ("dependency_injection", "encoder")):
        for framework in ("jakarta", "quarkus", "spring"):
            (tmp_path / layer / app / framework).mkdir(parents=True)

    bench = ScarfBench({"benchmark_cache_dir": str(tmp_path)})
    all_ids = [item.instance_id for item in bench.load_instances()]
    assert len(all_ids) == 12
    assert all_ids[:2] == [
        "business_domain/cart/jakarta-to-quarkus",
        "dependency_injection/encoder/jakarta-to-quarkus",
    ]
    assert [item.instance_id for item in bench.load_instances(limit=2)] == all_ids[:2]
    assert [item.instance_id for item in bench.load_instances(
        subset=["dependency_injection/encoder/spring-to-quarkus"], limit=1
    )] == ["dependency_injection/encoder/spring-to-quarkus"]

    targeted = ScarfBench({
        "benchmark_cache_dir": str(tmp_path),
        "instances": [{
            "layer": "business_domain", "app": "cart",
            "source": "jakarta", "target": "quarkus",
        }],
    })
    assert [item.instance_id for item in targeted.load_instances(limit=5)] == [all_ids[0]]

    by_pair = ScarfBench({
        "benchmark_cache_dir": str(tmp_path),
        "source": "spring", "target": "jakarta",
    })
    assert [item.instance_id for item in by_pair.load_instances()] == [
        "business_domain/cart/spring-to-jakarta",
        "dependency_injection/encoder/spring-to-jakarta",
    ]
    assert "Spring MVC" in by_pair.load_instances(limit=1)[0].prompt
    assert "Open Liberty" in by_pair.load_instances(limit=1)[0].prompt

    for selection, expected in (
        ({"source": "jakarta"}, "configured together"),
        ({"source": "jakarta", "target": "jakarta"}, "must differ"),
    ):
        try:
            ScarfBench({"benchmark_cache_dir": str(tmp_path), **selection}).load_instances()
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"Expected invalid pair selection: {selection}")


def test_prompt_uses_app_task_text_when_present(tmp_path):
    app_dir = tmp_path / "business_domain" / "cart"
    (app_dir / "jakarta").mkdir(parents=True)
    (app_dir / "quarkus").mkdir()
    bench = ScarfBench({"benchmark_cache_dir": str(tmp_path)})

    (app_dir / "prompt.txt").write_text("Preserve the cart's session lifecycle.\n")
    prompt = bench.load_instances(limit=1)[0].prompt
    assert "Benchmark task description (prompt.txt):" in prompt
    assert "Preserve the cart's session lifecycle." in prompt
    assert "Package the application" in prompt

    (app_dir / "prompt.txt").unlink()
    (app_dir / "cart.feature").write_text("Scenario: add a book\n")
    prompt = bench.load_instances(limit=1)[0].prompt
    assert "Benchmark task description (cart.feature):" in prompt
    assert "Scenario: add a book" in prompt

    (app_dir / "cart.feature").unlink()
    prompt = bench.load_instances(limit=1)[0].prompt
    assert "Benchmark task description" not in prompt
    assert "source code and any README or test.sh" in prompt


def test_source_test_script_is_available_to_agent(tmp_path):
    source = tmp_path / "jakarta"
    source.mkdir()
    (source / "pom.xml").write_text("<project/>\n")
    (source / "test.sh").write_text("#!/bin/sh\necho source behavior\n")
    (source / "Dockerfile").write_text("FROM scratch\n")
    staged = {}

    def capture(_container, path, _container_path):
        staged.update({p.name: p.read_text() for p in path.iterdir()})

    with patch("acb.benchmarks.scarfbench.container_untar_in", side_effect=capture):
        _copy_source_to_container(source, "container", "/work")

    assert staged["test.sh"] == "#!/bin/sh\necho source behavior\n"
    assert "Dockerfile" not in staged
