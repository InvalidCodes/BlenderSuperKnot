r"""The ASCII knot library.

**To add a knot, add one entry to** :data:`DIAGRAMS` **below.**  Nothing else
changes: the new name is immediately available to ``python -m superknot`` for
inspection, Blender build, MJCF export and MuJoCo tightening.

Drawing rules
-------------

``-`` and ``|`` are straight runs, ``/`` and ``\`` are corners.  At a crossing,
draw the character of the strand that passes **over**; the other strand is the
one that appears interrupted::

    ---|---     the vertical strand passes over
       |

    ---+---     ambiguous, avoid
       |

Every rope in this pipeline is *open*: it needs two free ends.  Start a strand
with ``>``, ``<``, ``^`` or ``V`` pointing along the rope, and terminate the
other end with ``.``::

    >-----\
          |
          .

``expected_crossings`` and ``expected_components`` are asserted against the
trace, so a mis-drawn diagram fails at build time instead of quietly producing
a different knot.  Leave them ``None`` only while prototyping.
"""

from __future__ import annotations

from . import register
from .base import AsciiAsset

__all__ = ["DIAGRAMS", "add_diagram"]


# ---------------------------------------------------------------------------
# Diagram definitions.  Keys become asset names.
# ---------------------------------------------------------------------------

DIAGRAMS = {
    # -- Reidemeister-I curl: a single crossing that pulls straight out. ----
    "curl_r1": dict(
        description="Single Reidemeister-I curl; pulls out to a straight rope",
        expected_crossings=1,
        diagram=r"""
   /---\
   |   |
>--|---/
   |
   .
""",
    ),
    # -- Same shadow as `trefoil`, one crossing switched: the closure is now
    # -- the unknot.  ASCII counterpart of the `three_crossing_unknot`
    # -- parametric control, for separating topology from appearance.
    "unknot_3x": dict(
        description="Trefoil shadow with one crossing switched; closure is the unknot",
        expected_crossings=3,
        diagram=r"""
>--------\
         |
   /-----|--\
   |     |  |
   |  /-----/
   |  |  |
   \--|--/
      |
      .
""",
    ),
    # -- Trefoil: the smallest non-trivial knot, alternating 3 crossings. ---
    "trefoil": dict(
        description="Overhand knot; closure is the trefoil 3_1 (alternating)",
        expected_crossings=3,
        diagram=r"""
>--------\
         |
   /-----|--\
   |     |  |
   |  /-----/
   |  |  |
   \-----/
      |
      .
""",
    ),
    # -- Figure-eight: alternating 4 crossings. ----------------------------
    "figure_eight": dict(
        description="Closure is the figure-eight knot 4_1 (alternating)",
        expected_crossings=4,
        diagram=r"""
>-----------\
            |
   /--------|-----\
   |        |     |
   |  /--------\  |
   |  |     |  |  |
   \--------|--/  |
      |     |     |
      |     \-----/
      |
      .
""",
    ),
    # -- The original stress test: one lead, fifteen crossings. ------------
    # The blank rows and columns are intentional; the spacing gives the
    # exported capsules room to bend and collide.
    "complex15x": dict(
        description="Dense 15-crossing single lead; stress test for the exporter",
        expected_crossings=15,
        segment_length=0.025,
        max_segments=200,
        diagram=r"""          /-----\
          |     |
  /-------------|---------\
  |       |     |         |
  |       |     | /---------\
  |       |     | |       | |
  |   /-\ |     | |       | |
  |   | | |     | |       | |
/-|-----|-----\ | |       | |
| |   | | |   | | |       | |
| |   | | |   | | |       | |
| |   | | |   | | |       | |
| |   | | |   | | |       \-/
| |   | | |   | | |
| |   | \-|-----|-------<
| |   |   |   | | |
| |   |   |   | | |
| |   |   |   | | |
\-|-------|-----/ |
  |   |   |   |   |
  \---/   |   |   |
          |   |   |
          .   |   |
              |   |
              |   |
              |   |
              \---/""",
    ),
}


# ---------------------------------------------------------------------------
# Registration.  Defaults below apply to every diagram unless it overrides one.
# ---------------------------------------------------------------------------

DEFAULTS = dict(
    family="ascii",
    scale=0.02,
    z_depth=1.25,
    z_bias=0.0,
    rope_radius=0.008,
    segment_length=0.020,
    max_segments=140,
    expected_components=1,
)


def add_diagram(name: str, **spec) -> AsciiAsset:
    """Build an :class:`AsciiAsset` from ``spec`` and register it."""
    params = {**DEFAULTS, **spec}
    diagram = params.pop("diagram").strip("\n")
    return register(AsciiAsset(name=name, diagram=diagram, **params))


for _name, _spec in DIAGRAMS.items():
    add_diagram(_name, **_spec)
