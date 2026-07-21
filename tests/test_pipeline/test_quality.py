"""Quality vocabulary — ranking and worst-wins escalation."""

from omotion.pipeline.quality import QUALITY_RANK, worse_of


def test_rank_order():
    assert (QUALITY_RANK["ok"]
            < QUALITY_RANK["ts_corrected"]
            < QUALITY_RANK["nan_filled"]
            < QUALITY_RANK["wide_interval"])


def test_wide_interval_outranks_nan_filled():
    """A nan_filled camera emits NaN and drops out of the side average on its
    own; a wide-interval camera contributes real-looking numbers built on a
    stretched baseline. The flag that silently biases the aggregate must win,
    or the side-average row would hide it."""
    assert worse_of("nan_filled", "wide_interval") == "wide_interval"
    assert worse_of("wide_interval", "nan_filled") == "wide_interval"


def test_worse_of_picks_the_higher_rank():
    assert worse_of("ok", "ts_corrected") == "ts_corrected"
    assert worse_of("nan_filled", "ok") == "nan_filled"


def test_worse_of_is_stable_for_equal_values():
    assert worse_of("ok", "ok") == "ok"
    assert worse_of("wide_interval", "wide_interval") == "wide_interval"


def test_unknown_values_rank_as_ok():
    """Documented hazard: an unrecognised string ranks 0. Any consumer holding
    its own copy of the rank map would treat a newer value as the BEST quality.
    This is why the rank lives here and is imported, never re-declared."""
    assert worse_of("something_new", "ok") == "ok"
