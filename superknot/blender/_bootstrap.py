"""Make the repository importable from inside Blender's Python.

Blender runs ``--python`` scripts with the script's directory on ``sys.path``,
not the repository root, so ``import superknot`` fails without this.  Import
this module first in every Blender entry point.
"""

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def script_args(argv=None):
    """Return the arguments after Blender's ``--`` separator."""
    argv = list(sys.argv if argv is None else argv)
    if "--" not in argv:
        return []
    return argv[argv.index("--") + 1 :]
