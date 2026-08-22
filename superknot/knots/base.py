"""The ``Asset`` interface shared by every knot the pipeline can build."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Sequence, Tuple

from ..ascii_diagram import Knot

Point = Tuple[float, float, float]

# Metadata keys written onto the Blender object and read back by the exporter.
TOPOLOGY_KEYS = (
    "gauss_code",
    "gauss_num_crossings",
    "gauss_num_components",
    "gauss_crossings",
    "gauss_crossing_verts",
)


@dataclass(frozen=True)
class Asset:
    """A named rope centerline plus the topology labels that describe it.

    Subclasses implement :meth:`centerlines`.  Everything else — the physical
    scale of the rope, the metadata stamped onto the Blender object — lives
    here so that the build/export/simulate stages stay asset-agnostic.
    """

    name: str
    description: str
    family: str = "misc"
    #: Physical rope radius in metres; the exporter uses it for capsule size.
    rope_radius: float = 0.008
    #: Target MuJoCo capsule length in metres.
    segment_length: float = 0.022
    #: Upper bound on exported capsules; keeps the simulation tractable.
    max_segments: int = 140
    #: Lift the whole centerline so its lowest point sits at this height.
    #: ``None`` leaves the asset where it was authored.
    base_height: float | None = 0.31
    #: Free-form topology labels merged into the Blender object properties.
    labels: Dict[str, object] = field(default_factory=dict)

    def centerlines(self) -> List[List[Point]]:
        """One polyline per rope component, in metres, world space."""
        raise NotImplementedError

    # -- derived ---------------------------------------------------------

    def build(self) -> List[List[Point]]:
        """Centerlines with :attr:`base_height` applied."""
        polylines = self.centerlines()
        if not polylines:
            raise ValueError(f"Asset {self.name!r} produced no centerline")
        if self.base_height is None:
            return polylines
        min_z = min(p[2] for line in polylines for p in line)
        dz = self.base_height - min_z
        return [[(x, y, z + dz) for x, y, z in line] for line in polylines]

    @property
    def topology(self) -> Dict[str, object]:
        """Topology metadata stamped onto the generated Blender object."""
        data: Dict[str, object] = {
            "superknot_asset": self.name,
            "superknot_family": self.family,
            "physical_curve": "open",
        }
        data.update(self.labels)
        return data

    def summary(self) -> str:
        crossings = self.topology.get("gauss_num_crossings", "?")
        components = self.topology.get("gauss_num_components", "?")
        return (
            f"{self.name:<26} {self.family:<20} "
            f"crossings={crossings!s:<3} components={components!s:<3} "
            f"{self.description}"
        )


@dataclass(frozen=True)
class AsciiAsset(Asset):
    """A knot drawn as an ASCII diagram.

    The diagram is traced by :mod:`superknot.ascii_diagram`, so the Gauss code
    and the crossing count are *derived* from the drawing rather than declared.
    ``expected_crossings``/``expected_components``, when given, are asserted
    against the trace — a typo in the diagram then fails loudly at build time
    instead of silently producing a different knot.
    """

    diagram: str = ""
    #: Vertical separation at crossings, in grid units before ``scale``.
    z_depth: float = 1.25
    #: -1 raises the over-strand, 0 splits evenly, +1 lowers the under-strand.
    z_bias: float = 0.0
    #: Metres per ASCII grid cell.
    scale: float = 0.02
    expected_crossings: int | None = None
    expected_components: int | None = 1

    def __post_init__(self):
        if not self.diagram.strip():
            raise ValueError(f"Asset {self.name!r} has an empty diagram")

    def trace(self) -> Knot:
        return Knot(self.diagram)

    def centerlines(self) -> List[List[Point]]:
        knot = self.trace()
        self._check(knot)
        return knot.centerlines(
            z_depth=self.z_depth, z_bias=self.z_bias, scale=self.scale
        )

    def _check(self, knot: Knot) -> None:
        gauss = knot.gauss_code()
        if (
            self.expected_crossings is not None
            and gauss.num_crossings != self.expected_crossings
        ):
            raise ValueError(
                f"{self.name}: diagram traces {gauss.num_crossings} crossings, "
                f"expected {self.expected_crossings}"
            )
        if (
            self.expected_components is not None
            and gauss.num_components != self.expected_components
        ):
            raise ValueError(
                f"{self.name}: diagram traces {gauss.num_components} components, "
                f"expected {self.expected_components}"
            )

    @property
    def topology(self) -> Dict[str, object]:
        gauss = self.trace().gauss_code()
        data = super().topology
        data.update(
            {
                "gauss_code": gauss.to_string(),
                "gauss_num_crossings": gauss.num_crossings,
                "gauss_num_components": gauss.num_components,
                "gauss_crossings": gauss.crossings_to_string(),
                "gauss_crossing_verts": gauss.crossing_vertices_to_string(),
                "source": "ascii_diagram",
            }
        )
        return data


@dataclass(frozen=True)
class ParametricAsset(Asset):
    """A knot defined by a Python callable returning its centerline.

    Used for shapes where a character grid is the wrong tool: smooth
    Catmull-Rom splines through hand-placed control points, closed-form
    parametrisations, or rigid rotations of an existing curve.  Topology labels
    are declared in :attr:`labels` because they cannot be derived from a
    drawing.
    """

    sampler: Callable[[], List[List[Point]]] | None = None

    def __post_init__(self):
        if self.sampler is None:
            raise ValueError(f"Asset {self.name!r} has no sampler")

    def centerlines(self) -> List[List[Point]]:
        return self.sampler()

    @property
    def topology(self) -> Dict[str, object]:
        data = super().topology
        data.setdefault("source", "parametric")
        return data


# -- geometry helpers shared by parametric assets -------------------------


def catmull_rom(points: Sequence[Point], samples_per_span: int = 8) -> List[Point]:
    """Interpolating Catmull-Rom spline through ``points``, no dependencies."""
    if samples_per_span < 2:
        raise ValueError("samples_per_span must be >= 2")
    result: List[Point] = []
    padded = [points[0], *points, points[-1]]
    for i in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[i - 1 : i + 3]
        for j in range(samples_per_span):
            t = j / samples_per_span
            t2 = t * t
            t3 = t2 * t
            xyz = tuple(
                0.5
                * (
                    2.0 * p1[a]
                    + (-p0[a] + p2[a]) * t
                    + (2.0 * p0[a] - 5.0 * p1[a] + 4.0 * p2[a] - p3[a]) * t2
                    + (-p0[a] + 3.0 * p1[a] - 3.0 * p2[a] + p3[a]) * t3
                )
                for a in range(3)
            )
            if not result or math.dist(result[-1], xyz) > 1e-8:
                result.append(xyz)
    result.append(tuple(points[-1]))
    return result


def rotate_xyz(point: Point, rotation: Tuple[float, float, float]) -> Point:
    """Apply an intrinsic X-then-Y-then-Z rotation (radians) to ``point``."""
    rx, ry, rz = rotation
    x, y, z = point
    # X
    cy, sy = math.cos(rx), math.sin(rx)
    y, z = y * cy - z * sy, y * sy + z * cy
    # Y
    cy, sy = math.cos(ry), math.sin(ry)
    x, z = x * cy + z * sy, -x * sy + z * cy
    # Z
    cy, sy = math.cos(rz), math.sin(rz)
    x, y = x * cy - y * sy, x * sy + y * cy
    return (x, y, z)
