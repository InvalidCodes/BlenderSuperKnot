"""Asset registry: every knot the pipeline can build, addressable by name.

An *asset* is anything that can produce a 3D rope centerline plus the topology
metadata that labels it.  Two families implement the same
:class:`~superknot.knots.base.Asset` interface:

* :mod:`superknot.knots.ascii_library` — knots drawn as ASCII diagrams.
  Adding one is a single entry in ``DIAGRAMS``; no new file, no new script.
* :mod:`superknot.knots.parametric` — knots defined by control points or by a
  closed-form curve, for shapes that are awkward to draw on a character grid.

Both register into the same namespace, so every downstream stage (Blender
build, MJCF export, MuJoCo tightening) only ever sees ``get(name)``.

    >>> from superknot import knots
    >>> knots.names()                     # doctest: +ELLIPSIS
    [...]
    >>> asset = knots.get("trefoil")
    >>> asset.topology["gauss_num_crossings"]
    3
"""

from __future__ import annotations

from typing import Dict, List

from .base import Asset, AsciiAsset, ParametricAsset, Point

__all__ = [
    "Asset",
    "AsciiAsset",
    "ParametricAsset",
    "Point",
    "register",
    "get",
    "names",
    "family",
    "families",
    "REGISTRY",
]

REGISTRY: Dict[str, Asset] = {}


def register(asset: Asset) -> Asset:
    """Add ``asset`` to the global registry.  Names must be unique."""
    if asset.name in REGISTRY:
        raise ValueError(f"Duplicate asset name: {asset.name!r}")
    REGISTRY[asset.name] = asset
    return asset


def get(name: str) -> Asset:
    """Look up an asset by name, with a helpful error listing valid names."""
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"Unknown asset {name!r}. Available: {', '.join(names())}"
        ) from None


def names() -> List[str]:
    """All registered asset names, sorted."""
    return sorted(REGISTRY)


def family(name: str) -> List[Asset]:
    """All assets belonging to the family ``name`` (e.g. ``topology_controls``)."""
    return [REGISTRY[n] for n in names() if REGISTRY[n].family == name]


def families() -> List[str]:
    """All distinct family names, sorted."""
    return sorted({asset.family for asset in REGISTRY.values()})


# Importing the libraries populates REGISTRY as a side effect.  Keep these at
# the bottom so `base` is fully defined first.
from . import ascii_library as ascii_library  # noqa: E402,F401
from . import parametric as parametric  # noqa: E402,F401
