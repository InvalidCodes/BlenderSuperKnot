"""Tests for the Blender-free layer: diagram tracing, Gauss codes, registry.

Run with ``pytest`` (or ``python -m pytest tests``) on a bare interpreter —
neither Blender nor MuJoCo is required.
"""

import math

import pytest

from superknot import knots, presets
from superknot.ascii_diagram import Knot, KnotException
from superknot.knots.base import AsciiAsset
from superknot.topology import coloring_dimension, is_colorable


def _alternating(sequence):
    return all(
        (sequence[i] > 0) != (sequence[i + 1] > 0) for i in range(len(sequence) - 1)
    )


# -- diagram tracing ------------------------------------------------------


@pytest.mark.parametrize("name", [n for n in knots.names()])
def test_every_asset_builds(name):
    asset = knots.get(name)
    polylines = asset.build()
    assert polylines, f"{name} produced no centerline"
    for line in polylines:
        assert len(line) >= 2
        # No duplicated consecutive points: they become degenerate capsules.
        for a, b in zip(line, line[1:]):
            assert math.dist(a, b) > 1e-9


@pytest.mark.parametrize(
    "name", [n for n in knots.names() if isinstance(knots.get(n), AsciiAsset)]
)
def test_ascii_diagram_matches_declared_topology(name):
    asset = knots.get(name)
    gauss = asset.trace().gauss_code()
    assert gauss.num_crossings == asset.expected_crossings
    assert gauss.num_components == asset.expected_components
    # Each crossing must be visited exactly twice: once over, once under.
    flat = [v for seq in gauss.sequences for v in seq]
    for crossing_id in range(1, gauss.num_crossings + 1):
        assert flat.count(crossing_id) == 1
        assert flat.count(-crossing_id) == 1


@pytest.mark.parametrize(
    "name,alternating",
    [
        ("curl_r1", True),
        ("trefoil", True),
        ("figure_eight", True),
        # Same shadow as the trefoil with one crossing switched, so the
        # over/under pattern must *not* alternate.
        ("unknot_3x", False),
    ],
)
def test_alternation(name, alternating):
    gauss = knots.get(name).trace().gauss_code()
    assert _alternating(gauss.sequences[0]) is alternating


def test_trefoil_and_unknot_share_a_shadow():
    """The control pair must differ only in crossing heights, not in layout."""
    trefoil = knots.get("trefoil")
    unknot = knots.get("unknot_3x")
    xy = lambda asset: [  # noqa: E731
        (round(x, 9), round(y, 9)) for x, y, _ in asset.build()[0]
    ]
    assert xy(trefoil) == xy(unknot)
    assert trefoil.trace().gauss_code().num_crossings == 3
    assert unknot.trace().gauss_code().num_crossings == 3


def test_malformed_diagram_raises():
    with pytest.raises(KnotException):
        Knot(">--@--.")


def test_crossing_vertices_index_into_the_centerline():
    asset = knots.get("trefoil")
    points = asset.build()[0]
    gauss = asset.trace().gauss_code()
    for _crossing_id, vertices in gauss.crossing_vertices.items():
        assert len(vertices) == 2
        for index in vertices:
            assert 0 <= index < len(points)
        # The two strands of a crossing sit above and below each other.
        a, b = (points[i] for i in vertices)
        assert math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-9
        assert abs(a[2] - b[2]) > 1e-9


# -- knot identity --------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        # (p=3, p=5) colouring dimensions. 1 means only trivial colourings.
        ("curl_r1", (1, 1)),  # unknot
        ("unknot_3x", (1, 1)),  # unknot, despite three crossings
        ("trefoil", (2, 1)),  # 3-colourable, not 5-colourable: 3_1
        ("figure_eight", (1, 2)),  # 5-colourable, not 3-colourable: 4_1
    ],
)
def test_fox_coloring_identifies_the_knot(name, expected):
    """Fox colouring must confirm the knot type the library advertises."""
    sequence = knots.get(name).trace().gauss_code().sequences[0]
    assert (
        coloring_dimension(sequence, 3),
        coloring_dimension(sequence, 5),
    ) == expected


def test_switching_a_crossing_unknots_the_trefoil():
    """The paired control differs from the trefoil only in one crossing."""
    trefoil = knots.get("trefoil").trace().gauss_code().sequences[0]
    unknot = knots.get("unknot_3x").trace().gauss_code().sequences[0]
    assert [abs(v) for v in trefoil] == [abs(v) for v in unknot]
    assert is_colorable(trefoil, 3)
    assert not is_colorable(unknot, 3)


# -- registry and presets -------------------------------------------------


def test_registry_lookup_errors_are_helpful():
    with pytest.raises(KeyError, match="Available"):
        knots.get("no_such_knot")
    with pytest.raises(KeyError, match="Available"):
        presets.get("no_such_preset")


def test_every_family_has_a_default_preset():
    for family in knots.families():
        asset = knots.family(family)[0]
        assert presets.for_asset(asset).name in presets.names()


def test_asset_base_height_is_applied():
    asset = knots.get("right_trefoil")
    assert min(p[2] for p in asset.build()[0]) == pytest.approx(asset.base_height)


def test_preset_override_leaves_the_original_untouched():
    base = presets.get("tighten")
    tweaked = presets.override(base, steps=42)
    assert tweaked.sim.steps == 42
    assert base.sim.steps != 42
