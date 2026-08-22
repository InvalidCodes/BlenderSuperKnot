"""Scripts that must run inside Blender.

These modules import :mod:`bpy` and are executed as::

    blender --background --python superknot/blender/<script>.py -- <args>

They are kept deliberately thin: all knot definitions and parameters live in
the plain-Python layer, so the only job here is to turn centerlines into
Blender data and Blender data into MJCF.
"""
