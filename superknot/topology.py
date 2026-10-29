"""Knot invariants computed from a Gauss code, for checking library claims.

The library asserts things like "this diagram's closure is the trefoil".  Fox
*n*-colouring is cheap to compute from a Gauss code and strong enough to keep
those claims honest for the small knots here:

===============  ====  ====  ====
knot             p=3   p=5   p=7
===============  ====  ====  ====
unknot            1     1     1
trefoil 3_1       2     1     1
figure-eight 4_1  1     2     1
===============  ====  ====  ====

The table lists the dimension of the colouring space.  Dimension 1 means only
the ``p`` trivial (all-arcs-same-colour) solutions exist; dimension 2 means the
knot is *p*-colourable and therefore not the unknot.

Colourings are a one-way test.  :func:`alexander_polynomial` is the finer
invariant used to count how many knot types a family of diagrams spans:
distinct polynomials certify distinct knots (up to mirror image).

Only the closure of an open rope has a Gauss code in the usual sense, so the
sequence is read cyclically — matching the ``closure_convention`` recorded on
the assets.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

__all__ = [
    "arcs_of",
    "coloring_matrix",
    "coloring_dimension",
    "is_colorable",
    "alexander_polynomial",
]


def arcs_of(sequence: Sequence[int]) -> List[int]:
    """Arc index the strand is on when it *arrives* at each Gauss position.

    Reading the closure cyclically, an arc runs from one under-pass to the
    next, so a diagram with ``c`` under-passes has ``c`` arcs.
    """
    unders = [i for i, value in enumerate(sequence) if value < 0]
    if not unders:
        # No under-passes: the whole closure is a single arc.
        return [0] * len(sequence)

    arc_at = [0] * len(sequence)
    # Position 0 lies on the arc that opened at the last under-pass of the
    # previous cycle, so numbering starts there and wraps consistently.
    arc = len(unders) - 1
    for i in range(len(sequence)):
        arc_at[i] = arc
        if sequence[i] < 0:
            arc = (arc + 1) % len(unders)
    return arc_at


def coloring_matrix(sequence: Sequence[int]) -> List[List[int]]:
    """One row ``2*over - incoming - outgoing`` per crossing."""
    n = len(sequence)
    arc_at = arcs_of(sequence)
    num_arcs = max(arc_at) + 1

    over_arc: Dict[int, int] = {}
    under_pos: Dict[int, int] = {}
    for i, value in enumerate(sequence):
        if value > 0:
            over_arc[value] = arc_at[i]
        else:
            under_pos[-value] = i

    rows = []
    for crossing in sorted(under_pos):
        if crossing not in over_arc:
            continue
        position = under_pos[crossing]
        row = [0] * num_arcs
        row[over_arc[crossing]] += 2
        row[arc_at[position]] -= 1  # incoming arc
        row[arc_at[(position + 1) % n]] -= 1  # outgoing arc
        rows.append(row)
    return rows


def _rank_mod_p(matrix: List[List[int]], p: int) -> int:
    """Gaussian elimination over GF(p); ``p`` must be prime."""
    rows = [[value % p for value in row] for row in matrix]
    rank = 0
    columns = len(rows[0]) if rows else 0
    for col in range(columns):
        pivot = next((r for r in range(rank, len(rows)) if rows[r][col]), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        inverse = pow(rows[rank][col], p - 2, p)
        rows[rank] = [(value * inverse) % p for value in rows[rank]]
        for r in range(len(rows)):
            if r != rank and rows[r][col]:
                factor = rows[r][col]
                rows[r] = [
                    (a - factor * b) % p for a, b in zip(rows[r], rows[rank])
                ]
        rank += 1
    return rank


def coloring_dimension(sequence: Sequence[int], p: int) -> int:
    """Dimension of the Fox *p*-colouring space of the closure.

    1 means only trivial colourings; 2 or more means the knot is
    *p*-colourable, which certifies it is not the unknot.
    """
    matrix = coloring_matrix(sequence)
    if not matrix:
        return 1
    return len(matrix[0]) - _rank_mod_p(matrix, p)


def is_colorable(sequence: Sequence[int], p: int) -> bool:
    return coloring_dimension(sequence, p) >= 2


# Evaluating at t = 2**BITS packs the polynomial's coefficients into the digits
# of one big integer (Kronecker substitution), so a single exact integer
# determinant recovers all of them.  48 bits is far beyond the coefficient
# sizes of any diagram that fits on a screen.
_KRONECKER_BITS = 48


def _determinant(matrix: List[List[int]]) -> int:
    """Exact integer determinant by fraction-free (Bareiss) elimination."""
    m = [row[:] for row in matrix]
    n = len(m)
    sign, previous = 1, 1
    for k in range(n - 1):
        if m[k][k] == 0:
            swap = next((r for r in range(k + 1, n) if m[r][k]), None)
            if swap is None:
                return 0
            m[k], m[swap] = m[swap], m[k]
            sign = -sign
        for i in range(k + 1, n):
            for j in range(k + 1, n):
                m[i][j] = (m[i][j] * m[k][k] - m[i][k] * m[k][j]) // previous
        previous = m[k][k]
    return sign * m[n - 1][n - 1]


def alexander_polynomial(sequence: Sequence[int], signs: Dict[int, int]) -> Tuple[int, ...]:
    """Alexander polynomial of the closure, coefficients from ``t**0`` up.

    ``signs`` maps each crossing to +1/-1, as in ``GaussCode.signs``.  The
    result is normalised to no factor of ``t`` and a positive leading
    coefficient; the unknot gives ``(1,)``, the trefoil ``(1, -1, 1)``.  A knot
    and its mirror image share a polynomial.
    """
    n = len(sequence)
    arc_at = arcs_of(sequence)
    over_arc: Dict[int, int] = {}
    under_pos: Dict[int, int] = {}
    for i, value in enumerate(sequence):
        if value > 0:
            over_arc[value] = arc_at[i]
        else:
            under_pos[-value] = i

    # Fox derivatives of the Wirtinger relation at each crossing, at t = 2**B.
    t = 1 << _KRONECKER_BITS
    rows = []
    for crossing in sorted(under_pos):
        position = under_pos[crossing]
        incoming, outgoing = arc_at[position], arc_at[(position + 1) % n]
        if signs[crossing] < 0:
            incoming, outgoing = outgoing, incoming
        row = [0] * (max(arc_at) + 1)
        row[over_arc[crossing]] += 1 - t
        row[incoming] += t
        row[outgoing] -= 1
        rows.append(row)
    if len(rows) <= 1:
        return (1,)
    value = _determinant([row[:-1] for row in rows[:-1]])

    # Read the coefficients back as balanced base-2**B digits.
    coefficients = []
    while value:
        digit = value & (t - 1)
        if digit >= t // 2:
            digit -= t
        coefficients.append(digit)
        value = (value - digit) >> _KRONECKER_BITS
    while coefficients and coefficients[0] == 0:
        coefficients.pop(0)
    if coefficients[-1] < 0:
        coefficients = [-c for c in coefficients]
    return tuple(coefficients)
