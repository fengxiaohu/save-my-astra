from eval.suites import load_suite, read_id_file


def test_tb_full_set_is_66_and_smoke_avoids_gpu():
    suite = load_suite("terminal-bench-4")
    ids = read_id_file(suite.path / "all_ids.txt")
    assert len(ids) == 66
    blocked = set(suite.raw["gpu_or_multicontainer"])
    assert not (set(suite.smoke_ids()) & blocked)


def test_calibration_defaults_optional():
    for name in ("math-500", "aime-2025", "frontiermath-public12"):
        assert load_suite(name).default_delegation_expected == "optional"
