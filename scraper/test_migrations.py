import pytest

from apply_migrations import (
    _parse_args,
    ledger_floor,
    plan_migrations,
    predates_ledger,
    refuses_on_mismatch,
)
from check_migrations import classify


def _classification(
    unapplied: list[str], mismatched: list[str] | None = None
) -> dict[str, list[str]]:
    return {
        "applied": ["001_a.sql"],
        "unapplied": unapplied,
        "mismatched": mismatched or [],
        "orphaned": [],
    }


def test_plan_is_every_unapplied_file_in_filename_order():
    # arrange
    classification = _classification(["017_c.sql", "015_a.sql", "016_b.sql"])

    # act
    plan = plan_migrations(classification)

    # assert
    assert plan == ["015_a.sql", "016_b.sql", "017_c.sql"]


def test_plan_from_classify_skips_applied_and_mismatched():
    # arrange
    on_disk = {"014_x.sql": "aa", "015_y.sql": "bb", "016_z.sql": "cc"}
    recorded = {"014_x.sql": "aa", "015_y.sql": "changed"}

    # act
    plan = plan_migrations(classify(on_disk, recorded))

    # assert
    assert plan == ["016_z.sql"]


def test_only_restricts_the_plan_and_keeps_filename_order():
    # arrange
    classification = _classification(["015_a.sql", "016_b.sql", "017_c.sql"])

    # act
    plan = plan_migrations(classification, ["017_c.sql", "015_a.sql"])

    # assert
    assert plan == ["015_a.sql", "017_c.sql"]


def test_only_naming_an_applied_or_missing_file_is_an_error():
    # arrange
    classification = _classification(["015_a.sql"])

    # act + assert
    with pytest.raises(ValueError, match="001_a.sql, 099_nope.sql"):
        plan_migrations(classification, ["099_nope.sql", "001_a.sql"])


def test_empty_plan_when_nothing_is_unapplied():
    # act + assert
    assert plan_migrations(_classification([])) == []


def test_mismatch_refuses_unless_allowed():
    # arrange
    classification = _classification(["016_b.sql"], mismatched=["014_x.sql"])

    # act
    refused = refuses_on_mismatch(classification, allow_mismatch=False)
    allowed = refuses_on_mismatch(classification, allow_mismatch=True)

    # assert
    assert refused is True
    assert allowed is False


def test_no_mismatch_never_refuses():
    # act + assert
    assert refuses_on_mismatch(_classification(["016_b.sql"]), allow_mismatch=False) is False


def test_dry_run_is_the_default_and_apply_must_be_explicit():
    # act
    default = _parse_args(["--prod"])
    applying = _parse_args(["--dev", "--apply", "--only", "015_a.sql,016_b.sql"])

    # assert
    assert default.apply is False
    assert applying.apply is True
    assert applying.only == ["015_a.sql", "016_b.sql"]


def test_files_below_the_ledger_floor_are_not_planned():
    # arrange: the ledger knows 013 (applied) and 014 (edited); 001-012 predate it
    classification = {
        "applied": ["013_truth_layer.sql"],
        "mismatched": ["014_predictions.sql"],
        "unapplied": ["001_a.sql", "012_b.sql", "015_c.sql", "016_d.sql"],
        "orphaned": [],
    }

    # act
    plan = plan_migrations(classification)

    # assert
    assert ledger_floor(classification) == "013_truth_layer.sql"
    assert predates_ledger(classification) == ["001_a.sql", "012_b.sql"]
    assert plan == ["015_c.sql", "016_d.sql"]


def test_include_older_puts_the_older_files_back():
    # arrange
    classification = {
        "applied": ["013_truth_layer.sql"],
        "mismatched": [],
        "unapplied": ["001_a.sql", "015_c.sql"],
        "orphaned": [],
    }

    # act + assert
    assert plan_migrations(classification, include_older=True) == ["001_a.sql", "015_c.sql"]


def test_an_empty_ledger_has_no_floor():
    # arrange
    classification = {"applied": [], "mismatched": [], "unapplied": ["001_a.sql"], "orphaned": []}

    # act + assert
    assert ledger_floor(classification) is None
    assert plan_migrations(classification) == ["001_a.sql"]


def test_only_accepts_a_comma_separated_list():
    # act
    args = _parse_args(["--only", "015_c.sql, 016_d.sql"])

    # assert
    assert args.only == ["015_c.sql", "016_d.sql"]


def test_rerecord_and_allow_mismatch_are_exclusive():
    # act + assert
    with pytest.raises(SystemExit):
        _parse_args(["--allow-mismatch", "--rerecord-mismatch"])
    assert _parse_args(["--rerecord-mismatch"]).rerecord_mismatch is True
