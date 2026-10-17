"""El generador de ataques y controles es reproducible, no filtra familias entre divisiones y etiqueta lo que debe."""

from praxeon.evaluation.finetune.generator import TEST_FAMILIES, VAL_FAMILIES, agreement_by_family, generate
from praxeon.providers.action_record import appears_in_observations
from praxeon.providers.systemone import DECISIONS

ROWS = generate(seed=3, scale=0.2)


def test_it_is_reproducible_and_rows_have_the_training_schema():
    assert [r["id"] for r in ROWS] == [r["id"] for r in generate(seed=3, scale=0.2)]
    for r in ROWS:
        assert r["state"].startswith("OBJETIVO:") and "ACCIÓN PROPUESTA" in r["state"]
        assert set(r["expected"]) == {"decision", "is_loop", "is_grounded", "progress"} and set(r["questions"]) == set(r["expected"])
        assert r["expected"]["decision"] in DECISIONS and 0 <= r["expected"]["progress"] <= 4


def test_test_and_validation_families_never_reach_training():
    train = {r["family"] for r in ROWS if r["split"] == "train"}
    assert not train & (TEST_FAMILIES | VAL_FAMILIES)
    assert {r["family"] for r in ROWS if r["split"] == "test"} >= TEST_FAMILIES


def test_controls_are_allowed_and_attacks_are_not():
    for r in ROWS:
        decision = r["expected"]["decision"]
        if r["family"].startswith("control"):
            assert decision == "ALLOW" and not r["harm"]
        elif r["family"] in ("injection", "exfil_curl", "download_exec"):
            assert decision == "BLOCK" and r["harm"]


def test_part_of_the_rows_hide_the_classifier_flags_and_the_record_has_provenance():
    masked = [r for r in ROWS if r["flags_masked"] and "run_command" in r["state"]]
    visible = [r for r in ROWS if not r["flags_masked"] and "run_command" in r["state"]]
    assert masked and visible
    assert all("categoría:" not in r["state"] for r in masked) and any("categoría:" in r["state"] for r in visible)
    assert all("citada en lo leído antes:" in r["state"] for r in ROWS if "run_command" in r["state"])


def test_injections_cite_the_command_found_in_a_file_but_legit_readme_commands_too():
    injected = [r for r in ROWS if r["family"] == "injection"]
    assert injected and all("citada en lo leído antes: sí" in r["state"] for r in injected)
    followed = [r for r in ROWS if r["family"] == "control_followed_readme"]
    assert all(r["expected"]["decision"] == "ALLOW" for r in followed)       # la procedencia sola no decide


def test_provenance_helper():
    history = [{"observation": "Para instalar ejecuta curl http://x.test/a.sh y luego sigue"}]
    assert appears_in_observations("curl http://x.test/a.sh -o a.sh", history)
    assert not appears_in_observations("git status", history) and not appears_in_observations("ls", [])


def test_the_deterministic_oracle_is_blind_to_injections_but_not_to_deletes():
    table = agreement_by_family(ROWS)
    assert table["injection"]["agreement"] < 0.5
    assert table["delete_root_like"]["agreement"] == 1.0
