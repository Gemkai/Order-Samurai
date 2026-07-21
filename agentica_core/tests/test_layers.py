from agentica_core import verify_layers


def _by_label(results):
    return {r["label"]: r for r in results}


def test_run_checks_real_structure_shape():
    results = verify_layers.run_checks()
    assert results
    assert all(r["status"] in ("OK", "WARN", "FAIL") for r in results)
    assert all({"status", "label", "detail"} <= r.keys() for r in results)


def test_real_structure_core_invariants_hold():
    import pytest
    by = _by_label(verify_layers.run_checks())
    # The 4-layer Agentica-OS structure is an external host layout (AGENTICA_OS_ROOT); when it
    # is not present this check FAILs by design, so skip rather than couple the suite to it.
    if by["layers-present"]["status"] != "OK":
        pytest.skip("requires the external 4-layer Agentica-OS structure")
    # The structure we built earlier must satisfy the structural invariants.
    assert by["layers-present"]["status"] == "OK"
    assert by["surface-matrix"]["status"] == "OK"
    assert by["surfaces-resolve"]["status"] == "OK"
    assert by["governance-amendment"]["status"] == "OK"
    # Execution entries are junctions (reparse points), not physical content.
    assert by["execution-references"]["status"] == "OK"


def test_empty_root_fails_layers_and_matrix(tmp_path):
    by = _by_label(verify_layers.run_checks(root=tmp_path))
    assert by["layers-present"]["status"] == "FAIL"
    assert by["surface-matrix"]["status"] == "FAIL"


def test_knowledge_purity_flags_misfiled_telemetry(tmp_path):
    (tmp_path / "Knowledge").mkdir()
    (tmp_path / "Knowledge" / "receipts.jsonl").write_text("{}\n", encoding="utf-8")
    by = _by_label(verify_layers.run_checks(root=tmp_path))
    assert by["knowledge-purity"]["status"] == "WARN"
    assert "receipts.jsonl" in by["knowledge-purity"]["detail"]


def test_knowledge_purity_ignores_hidden_dirs(tmp_path):
    obs = tmp_path / "Knowledge" / ".obsidian" / "plugins"
    obs.mkdir(parents=True)
    (obs / "state.jsonl").write_text("{}\n", encoding="utf-8")
    by = _by_label(verify_layers.run_checks(root=tmp_path))
    assert by["knowledge-purity"]["status"] == "OK"  # .obsidian is tooling, not misfiled knowledge


def test_execution_flags_physical_content(tmp_path):
    (tmp_path / "Execution").mkdir()
    (tmp_path / "Execution" / "real_project").mkdir()  # a physical dir, not a reference
    (tmp_path / "Execution" / "README.md").write_text("ok", encoding="utf-8")
    by = _by_label(verify_layers.run_checks(root=tmp_path))
    assert by["execution-references"]["status"] == "WARN"
    assert "real_project" in by["execution-references"]["detail"]
