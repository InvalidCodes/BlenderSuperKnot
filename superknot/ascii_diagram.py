r"""Parse ASCII knot diagrams into traced strands, Gauss codes and 3D centerlines.

This module is deliberately free of any Blender dependency so that diagrams can
be validated, and their Gauss codes computed, with a plain Python interpreter.

An ASCII diagram is a grid of characters describing the shadow of a knot:

    ``-`` ``|``      straight runs
    ``/`` ``\``      corners
    ``<`` ``>`` ``^`` ``V``   free ends (the arrow points along the strand)
    ``.``            a terminating free end
    ``+``            an explicit junction

Where two runs cross, the strand that is *interrupted* in the drawing passes
under the other one.  The tracer walks each strand from a free end, following a
transition table keyed by ``(character, incoming direction)``, and marks the
under-strand at every crossing.

Original tracer by the ASCII knot add-on; extracted here and extended with
:meth:`Knot.gauss_code` and :func:`centerlines`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterator, List, Sequence, Tuple

__all__ = [
    "KnotException",
    "Knot",
    "GaussCode",
    "centerlines",
]

Point = Tuple[float, float, float]

# Direction characters and their (dx, dy) grid steps.  ``O`` means "no
# direction yet", used when tracing starts at an ambiguous cell.
CHAR_DIRS = {"^": (0, -1), "V": (0, 1), ">": (1, 0), "<": (-1, 0), "O": (0, 0)}
INV_DIRS = {v: k for k, v in CHAR_DIRS.items()}

# Transition table: rows are the character being entered, columns are the
# incoming direction (O ^ V > <).  Cell values are the action to take:
#   ^ V > <   leave in this direction
#   U         pass *under* (this cell is a crossing, we are the under-strand)
#   C         choose the single unambiguous neighbour
#   L         the cell is part of a [label]
#   .         the strand ends here
#   #         illegal, the diagram is malformed
_TRANSITION_TABLE = r"""
* O ^ V > <
O   C # # # #
.   # . . . .
^   ^ ^ # U U
V   V # V U U
v   V # V U U
>   > U U > #
<   < U U # <
-   # U U > <
|   # ^ V U U
/   # > < ^ V
\   # < > V ^
+   # C C C C
L   # L L L L
"""


def _build_follow_map() -> Dict[Tuple[str, str], str]:
    follow_map: Dict[Tuple[str, str], str] = {}
    base_dirs = "O^V><"
    for line in _TRANSITION_TABLE.splitlines():
        if len(line) <= 1:
            continue
        chars = line.split()
        in_char, out_chars = chars[0], chars[1:]
        for i, _ in enumerate(base_dirs):
            follow_map[(in_char, base_dirs[i])] = out_chars[i]
    return follow_map


FOLLOW_MAP = _build_follow_map()


def _nonempty(kmap, x: int, y: int):
    return [
        (x_off, y_off)
        for x_off, y_off in CHAR_DIRS.values()
        if (x + x_off, y + y_off) in kmap
    ]


class KnotException(Exception):
    """Raised when a diagram cannot be traced unambiguously."""


@dataclass(frozen=True)
class GaussCode:
    """Gauss code of a traced diagram.

    ``sequences`` holds one signed-integer sequence per strand: ``+n`` when the
    strand passes over crossing ``n`` and ``-n`` when it passes under.
    Crossings are numbered from 1 in row-major order of the diagram.
    """

    sequences: Tuple[Tuple[int, ...], ...]
    crossing_ids: Dict[Tuple[int, int], int]
    crossing_vertices: Dict[int, Tuple[int, ...]]

    @property
    def num_crossings(self) -> int:
        return len(self.crossing_ids)

    @property
    def num_components(self) -> int:
        return len(self.sequences)

    def to_string(self) -> str:
        """Serialise as ``+1,-2,+3;...`` — strands separated by ``;``."""
        return ";".join(
            ",".join(f"{v:+d}" for v in seq) for seq in self.sequences
        )

    def crossings_to_string(self) -> str:
        return ";".join(
            f"{cid}:{pos[0]},{pos[1]}" for pos, cid in self.crossing_ids.items()
        )

    def crossing_vertices_to_string(self) -> str:
        return ";".join(
            f"{cid}:" + ",".join(str(ix) for ix in verts)
            for cid, verts in sorted(self.crossing_vertices.items())
        )


class Knot:
    """A traced ASCII knot diagram."""

    def __init__(self, diagram: str):
        self._parse_map(diagram)
        self._trace_leads()

    # -- parsing ---------------------------------------------------------

    def _parse_map(self, s: str) -> None:
        self.map: Dict[Tuple[int, int], str] = {}
        self.inv_map = defaultdict(list)
        self.labels: Dict[Tuple[int, int], "_Label"] = {}
        self.crossovers: List[Tuple[int, int]] = []
        self.lead_map = defaultdict(list)

        def mark_label(x, y, label):
            self.map[(x, y)] = "L"
            self.inv_map["L"].append((x, y))
            self.labels[(x, y)] = label

        for y, line in enumerate(s.splitlines()):
            in_label = False
            label = None
            for x, char in enumerate(line):
                if not in_label:
                    if char == "[":
                        in_label = True
                        label = _Label()
                        mark_label(x, y, label)
                    elif not char.isspace():
                        self.map[(x, y)] = char
                        self.inv_map[char].append((x, y))
                else:
                    if char == "]":
                        in_label = False
                        mark_label(x, y, label)
                    else:
                        mark_label(x, y, label)
                        label.append(char)

    def _choose(self, x, y, dx, dy):
        neighbours = _nonempty(self.map, x, y)
        valid = [
            (vx, vy)
            for vx, vy in neighbours
            if not (vx == -dx and vy == -dy) and not (vx == 0 and vy == 0)
        ]
        if len(valid) < 1:
            self._raise_error(x, y, "No neighbour to turn to")
        if len(valid) > 1:
            self._raise_error(x, y, "Ambiguous neighbour")
        return valid[0]

    def _find_heads(self):
        heads = []
        head_dirs = dict(CHAR_DIRS)
        head_dirs.update({str(d): (0, 0) for d in range(10)})
        head_dirs["v"] = head_dirs["V"]

        for char, (x_off, y_off) in head_dirs.items():
            for x, y in self.inv_map[char]:
                if x_off == 0 and y_off == 0:
                    x_off, y_off = self._choose(x, y, 0, 0)
                    name = char if char.isdigit() else ""
                    heads.append((x, y, x_off, y_off, 0, name))
                else:
                    if self.map.get((x - x_off, y - y_off)) is None:
                        heads.append((x, y, x_off, y_off, 0, ""))

        return sorted(heads, key=lambda h: (h[1], h[0]))

    def _raise_error(self, x, y, msg=""):
        k, n = 3, 6
        str_lines = [msg.center(n * 2)]
        for i in range(-n, n + 1):
            line = []
            for j in range(-n, n + 1):
                if (abs(j) == k and abs(i) < k) or (abs(j) < k and abs(i) == k):
                    line.append("@")
                else:
                    line.append(self.map.get((x + j, y + i)) or " ")
            str_lines.append("".join(line) + "\n")
        raise KnotException("".join(str_lines))

    def _trace_leads(self) -> None:
        heads = self._find_heads()
        self.leads: List[List[Tuple]] = []
        self.over_map = defaultdict(list)
        ix = 0

        for head in heads:
            lead = []
            x, y, dx, dy, z, name = head
            lead.append((x, y, dx, dy, z, name))
            x, y = x + dx, y + dy
            while (x, y) in self.map:
                char = self.map.get((x, y))
                action = FOLLOW_MAP.get((char, INV_DIRS[(dx, dy)]))

                if action in CHAR_DIRS:
                    dx, dy = CHAR_DIRS[action]
                    z = 0
                elif action == "L":
                    name = self.labels[(x, y)].label
                elif action == "U":
                    z = -1
                    self.crossovers.append((x, y))
                elif action == "C":
                    dx, dy = self._choose(x, y, dx, dy)
                    z = 0
                elif action == ".":
                    break
                elif action == "#":
                    self._raise_error(x, y, "Invalid direction")
                elif action is None:
                    self._raise_error(x, y, "Character %s unexpected" % char)

                lead.append((x, y, dx, dy, z, name))
                self.over_map[(x, y)].append((ix, dx, dy, z))
                self.lead_map[(x, y)].append((lead, ix))
                ix += 1
                x, y = x + dx, y + dy
            self.leads.append(lead)

        if not self.leads:
            raise KnotException("No valid strand found in diagram")

    # -- topology --------------------------------------------------------

    def is_crossing(self, x: int, y: int) -> bool:
        cross = self.over_map.get((x, y))
        return cross is not None and len(cross) > 1

    def gauss_code(self) -> GaussCode:
        """Extract the Gauss code of the traced diagram."""
        crossings = [
            (coord, entries)
            for coord, entries in self.over_map.items()
            if len(entries) > 1
        ]
        # Row-major ordering gives crossing ids that are stable across runs.
        crossings_sorted = sorted(crossings, key=lambda c: (c[0][1], c[0][0]))
        crossing_ids = {coord: idx + 1 for idx, (coord, _) in enumerate(crossings_sorted)}

        # Walk the strands in exactly the order `centerlines` emits points, so
        # the recorded vertex indices address the generated polylines directly.
        sequences = []
        crossing_vertices = defaultdict(list)
        vertex_index = 0
        for lead in self._emitted_leads():
            seq = []
            for x, y, _dx, _dy, z, _name in lead:
                cid = crossing_ids.get((x, y))
                if cid is not None:
                    seq.append(-cid if z == -1 else cid)
                    crossing_vertices[cid].append(vertex_index)
                vertex_index += 1
            if seq:
                sequences.append(tuple(seq))

        return GaussCode(
            tuple(sequences),
            crossing_ids,
            {cid: tuple(v) for cid, v in sorted(crossing_vertices.items())},
        )

    # -- geometry --------------------------------------------------------

    def _emitted_leads(self) -> Iterator[Sequence[Tuple]]:
        """Strands long enough to become a polyline, in emission order."""
        return (lead for lead in self.leads if len(lead) >= 2)

    def centerlines(
        self, z_depth: float = 1.25, z_bias: float = 0.0, scale: float = 1.0
    ) -> List[List[Point]]:
        """Lift the diagram into 3D, one polyline per traced strand.

        Grid cell ``(x, y)`` maps to ``(x, -y, 0)``; at a crossing the strand is
        raised or lowered by ``z_depth * (z_bias + 1) / 2`` depending on whether
        it passes over or under.  Everything is multiplied by ``scale``.
        """
        offset = z_depth * (z_bias + 1.0) / 2.0
        polylines: List[List[Point]] = []
        for lead in self._emitted_leads():
            points: List[Point] = []
            for x, y, _dx, _dy, z, _name in lead:
                if self.is_crossing(x, y):
                    z_val = -offset if z == -1 else offset
                else:
                    z_val = 0.0
                points.append((x * scale, -y * scale, z_val * scale))
            polylines.append(points)
        return polylines


class _Label:
    def __init__(self):
        self.label = ""

    def append(self, c: str) -> None:
        self.label += c


def centerlines(
    diagram: str, z_depth: float = 1.25, z_bias: float = 0.0, scale: float = 1.0
) -> List[List[Point]]:
    """Convenience wrapper: parse ``diagram`` and return its 3D centerlines."""
    return Knot(diagram).centerlines(z_depth=z_depth, z_bias=z_bias, scale=scale)
