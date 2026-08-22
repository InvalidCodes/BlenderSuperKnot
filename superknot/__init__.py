"""BlenderSuperKnot: ASCII knot diagrams to MuJoCo rope simulations.

The package is layered so that everything except the two Blender entry points
and the MuJoCo simulator is plain Python:

    superknot.ascii_diagram   ASCII diagram -> traced leads, Gauss code, centerline
    superknot.knots           Asset registries (ASCII + parametric) and the Asset API
    superknot.presets         Named export/simulation parameter sets
    superknot.blender         Scripts run inside `blender --background`
    superknot.sim             MuJoCo tightening and video rendering
    superknot.cli             `python -m superknot` command line interface

Importing this package never imports `bpy` or `mujoco`.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
