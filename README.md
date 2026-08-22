# BlenderSuperKnot

Turn a knot **drawn in ASCII** into a **physically simulated rope** that a robot-style
endpoint pull tries to tighten or undo, and get a video plus quantitative metrics out
the other end.

```
 ASCII diagram          Gauss code          Blender curve         MJCF model          MuJoCo
 ┌───────────┐          (labels)           ┌───────────┐        ┌───────────┐      ┌──────────┐
 │ >-------\ │  trace   +1,-2,+3,          │  one POLY │ export │ capsule   │ pull │  video   │
 │  /----|-\ │ ───────► -1,+2,-3   ──────► │  spline   │ ─────► │ chain +   │ ───► │    +     │
 │  \----/   │                             │  per rope │        │ ball jts  │      │ metrics  │
 └───────────┘                             └───────────┘        └───────────┘      └──────────┘
   superknot.ascii_diagram                  blender/build_asset   blender/export_mjcf   sim/tighten
```

The Gauss code is a **label, not an intermediate**: it is derived from the drawing so
every generated rope carries a machine-readable description of its topology, but no
downstream stage consumes it. That is deliberate — it lets you ask whether a learned
or scripted policy can tell a trefoil from an unknot *from the physics alone*.

---

## Install

```bash
git clone https://github.com/InvalidCodes/BlenderSuperKnot.git
cd BlenderSuperKnot

# MuJoCo stage
pip install -r requirements.txt

# Blender stages: Blender 4.0+ on PATH (no Python packages needed)
blender --version
```

The diagram/Gauss-code layer is plain Python with no dependencies at all — you can
inspect knots without installing anything:

```bash
python -m superknot show trefoil
```

If MuJoCo lives in a separate environment (e.g. conda), point the CLI at it:

```bash
python -m superknot run trefoil --python ~/anaconda3/envs/mujoco/bin/python
```

---

## Quick start

```bash
python -m superknot list                 # every knot and every preset
python -m superknot show trefoil         # diagram, Gauss code, metadata (no Blender)
python -m superknot run trefoil          # build -> export -> simulate
```

Outputs land in predictable places:

| Path | Stage |
|---|---|
| `build/<name>.blend` | Blender curve with topology metadata |
| `build/<name>.xml` | MJCF model |
| `results/<name>.mp4` | tightening video |
| `results/<name>.json` | metrics (pull distance, peak force, arc/chord, stop reason) |
| `results/logs/<name>.log` | per-stage output, with `--log` |

Each stage is a separate command reading the previous stage's file, so a failed run is
resumable rather than restartable:

```bash
python -m superknot build  trefoil
python -m superknot export trefoil
python -m superknot simulate trefoil --preset smoke
```

Batch selectors work on every command:

```bash
python -m superknot run --family topology_controls
python -m superknot run --all --preset smoke
```

---

## Adding a knot

**This is the whole procedure.** Add one entry to `DIAGRAMS` in
[`superknot/knots/ascii_library.py`](superknot/knots/ascii_library.py):

```python
"my_knot": dict(
    description="What this knot is",
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
```

It is immediately available everywhere — `show`, `build`, `export`, `simulate`, `run`,
the test suite and the Blender add-on's dropdown. No new file, no new script, no new
shell wrapper.

### Drawing rules

`-` and `|` are straight runs, `/` and `\` are corners. **At a crossing you draw the
character of the strand that passes over**; the strand that appears interrupted is the
one going under:

```
---|---     the vertical strand passes over
   |
```

Every rope here is **open** — it needs two free ends to pull. Start a strand with `>`,
`<`, `^` or `V` pointing along it, and terminate the other end with `.`. Everything the
tracer touches must be one continuous path; a closed loop with no free end is silently
never traced, which shows up as `crossings=0`.

`expected_crossings` and `expected_components` are asserted against the trace, so a
mis-drawn diagram fails loudly at build time instead of quietly producing a different
knot. `python -m superknot show <name>` prints what your diagram actually traced to.

### When ASCII is the wrong tool

Smooth splines, closed-form curves and rigid re-projections go in
[`superknot/knots/parametric.py`](superknot/knots/parametric.py) instead. Both libraries
register into the same namespace and implement the same `Asset` interface, so every
downstream stage only ever calls `knots.get(name)`.

---

## The knot library

| Family | Knot | Crossings | Notes |
|---|---|---|---|
| `ascii` | `curl_r1` | 1 | Reidemeister-I curl; pulls straight out |
| | `trefoil` | 3 | alternating; closure is 3₁ |
| | `unknot_3x` | 3 | **same shadow as `trefoil`**, one crossing switched |
| | `figure_eight` | 4 | alternating; closure is 4₁ |
| | `complex15x` | 15 | dense single lead; exporter stress test |
| `slip` | `slip_knot` | 1 | removable curl, released by pulling both ends |
| `topology_controls` | `right_trefoil` | 3 | the base projection |
| | `three_crossing_unknot` | 3 | same shadow, one crossing switched |
| | `left_trefoil` | 3 | every crossing mirrored |
| | `right_trefoil_r1/r2/r3` | 4 / 5 / 3 | rigid rotations of `right_trefoil` |

The library is built around two controlled comparisons:

* **Same appearance, different topology.** `trefoil` vs `unknot_3x`, and
  `right_trefoil` vs `three_crossing_unknot`, share a projection exactly and differ only
  in the height of one crossing.
* **Same topology, different appearance.** `right_trefoil_r1/r2/r3` are the *same curve*
  rigidly rotated, chosen so the XY projection shows 4, 5 and 3 crossings.

---

## How each stage works

### 1. Diagram → centerline ([`ascii_diagram.py`](superknot/ascii_diagram.py))

The tracer walks each strand from a free end using a transition table keyed by
`(character, incoming direction)`. Action `U` marks the current cell as an *under*
crossing. A cell visited twice is a crossing; crossings are numbered row-major, and each
strand emits `+n`/`-n` as it passes over/under. Cells lift to `(x, -y, ±z_depth/2)`, then
scale to metres.

Pure Python, no `bpy` — which is why the Gauss code is testable and `show` works on a
bare interpreter.

[`topology.py`](superknot/topology.py) computes Fox *n*-colourings from the Gauss code to
keep the library's claims honest. `show` reports them, and the test suite asserts them:

| knot | p=3 | p=5 | verdict |
|---|---|---|---|
| `curl_r1`, `unknot_3x` | 1 | 1 | trivial colourings only |
| `trefoil` | **2** | 1 | 3-colourable, not 5 → 3₁ |
| `figure_eight` | 1 | **2** | 5-colourable, not 3 → 4₁ |

(Dimension 1 means only the trivial all-one-colour solutions exist. Colourability is a
one-way certificate: it proves a knot is *not* the unknot, but failing it proves nothing —
which is why no knot type is claimed for `complex15x`.)

### 2. Centerline → Blender ([`build_asset.py`](superknot/blender/build_asset.py))

Builds one `POLY` spline per rope component in a scene created from scratch (no template
`.blend` required), bevels it to the rope radius, and stamps the topology metadata plus
the geometry hints the exporter needs onto the object as custom properties.

### 3. Blender → MJCF ([`export_mjcf.py`](superknot/blender/export_mjcf.py))

* Samples the curve centerline with the bevel temporarily removed, resamples to a uniform
  arc-length step, and caps the segment count.
* Emits a **nested body chain**: `seg_000` carries a freejoint on the world, every later
  segment is a child of the previous one joined by a `ball` joint.
* Each segment gets **two geoms** — a collision capsule that is never drawn and a slightly
  fatter textured visual capsule that never collides — so contact tuning and appearance
  are independent.
* `<contact><exclude>` drops self-contact between segments 1 and 2 apart: their rounded
  caps overlap on tight bends and would otherwise fight. Segments ≥3 apart still collide,
  which is what makes a knot hold.
* Both endpoints are welded to **mocap bodies** — the handles the simulator pulls.
* A table plane is auto-placed at `min(z) - rope_radius - clearance`, so the rope neither
  falls from mid-air nor starts interpenetrating.

### 4. MuJoCo → video ([`tighten.py`](superknot/sim/tighten.py))

Each step the mocap targets advance along each end's outward tangent. Two things stop the
pull:

* **`taut`** — the weld tension, smoothed by an EMA so collision spikes cannot trigger it,
  stays above `taut_force` for `taut_hold_steps` consecutive steps. This is the normal
  completion condition.
* **`safety_limit`** — endpoint tracking error or raw force crosses a hard ceiling. The
  welds are compliant, so this guards a pathological pull. **Reaching it means the rope
  jammed rather than tightened**, and is a result, not an error.

The reported `arc/chord` ratio and maximum deviation from the endpoint chord quantify how
straight the rope ended up: a fully released rope approaches 1.0, a jammed knot stays far
above it.

---

## Presets

A preset owns everything that is a property of the *experiment* — friction, timestep, how
hard to pull, how long to record. Everything that is a property of the *knot* — rope
radius, capsule length, segment budget — lives on the asset. Keeping them apart is what
lets any knot run under any preset.

| Preset | Use |
|---|---|
| `tighten` | pull until taut, then settle (default for `ascii`) |
| `slip_release` | short gentle pull that lets a slip knot run out (default for `slip`) |
| `topology_control` | fixed-distance pull with a high force ceiling (default for `topology_controls`) |
| `smoke` | fast low-resolution end-to-end check |

Presets are in [`superknot/presets.py`](superknot/presets.py); dump them with
`python -m superknot presets`, override individual fields on the command line:

```bash
python -m superknot simulate trefoil --taut-force 5 --resolution 1280x720 --fps 60
```

---

## Result: endpoint pulling is not an unknotting algorithm

```bash
python -m superknot run --family topology_controls
```

| asset | pulled (m) | peak force (N) | arc/chord | max deviation (m) | stop reason |
|---|---:|---:|---:|---:|---|
| `right_trefoil` | 0.362 | 80.2 | 3.91 | 0.268 | `safety_limit` |
| `three_crossing_unknot` | 0.368 | 80.2 | 3.89 | 0.256 | `safety_limit` |
| `left_trefoil` | 0.422 | 80.8 | 3.30 | 0.279 | `safety_limit` |
| `right_trefoil_r1` | 0.242 | 80.7 | 6.45 | 0.323 | `safety_limit` |
| `right_trefoil_r2` | 0.771 | 80.5 | 1.74 | 0.631 | `safety_limit` |
| `right_trefoil_r3` | 0.316 | 80.5 | 4.59 | 0.308 | `safety_limit` |

All six controls — **including the topologically trivial `three_crossing_unknot`** — stop
at the force safety limit well before the requested 1.20 m pull, and the unknot is
indistinguishable from the trefoil it shares a shadow with (0.368 m vs 0.362 m, arc/chord
3.89 vs 3.91). Friction and self-contact lock the rope up before topology gets a chance to
matter, so an endpoint-only action is not a complete unknotting algorithm in this regime.

That is the point of the control family: any claim that a policy "undoes knots" by pulling
the ends has to beat the trivial control, and here nothing does. Full metrics land in
`results/summary.json`.

---

## Blender add-on (optional)

The GUI front end to the same registry. Link it rather than copying, so it can import the
package:

```bash
ln -s "$PWD/superknot/blender/addon.py" \
      ~/.config/blender/4.0/scripts/addons/superknot.py
```

Enable *SuperKnot Authoring* in Preferences → Add-ons; the panel is in the 3D viewport
sidebar (`N`) under **Knot**. It generates any registered knot, or traces an ASCII diagram
you paste into a Text block, and can drop labelled Empties on each crossing.

Scope is authoring only — tightening is MuJoCo's job. Earlier versions carried a Blender
soft-body/cloth tightening path; it was superseded and removed (see git history).

---

## Repository layout

```
superknot/
  ascii_diagram.py      ASCII tracing, Gauss code, 3D lift        (no bpy, no mujoco)
  topology.py           Fox n-colouring, to verify knot identity
  knots/
    base.py             the Asset interface + geometry helpers
    ascii_library.py    ← add ASCII knots here
    parametric.py       ← add spline/closed-form knots here
  presets.py            ← add experiments here
  blender/
    build_asset.py      asset -> .blend            (runs inside Blender)
    export_mjcf.py      .blend -> MJCF             (runs inside Blender)
    curve_builder.py    shared curve construction
    addon.py            optional GUI add-on
  sim/
    tighten.py          MJCF -> video + metrics    (needs MuJoCo)
  cli.py                python -m superknot
tests/                  pytest; needs neither Blender nor MuJoCo
textures/               rope textures for the MJCF material
```

## Tests

```bash
python -m pytest tests -q
```

They cover diagram tracing, Gauss-code correctness (every crossing visited exactly once
over and once under), the shared-shadow invariant between `trefoil` and `unknot_3x`, and
the registry/preset contracts. No Blender or MuJoCo required.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `crossings=0` from a new diagram | the drawing contains a closed loop with no free end, so it is never traced |
| `diagram traces N crossings, expected M` | the diagram and its `expected_crossings` disagree — check with `show` |
| `Only a freejoint was found` | the exporter produced no ball joints; the input curve had <2 points |
| video will not play in a browser | `imageio-ffmpeg` is missing, so the OpenCV/`mp4v` fallback was used |
| `stop_reason: safety_limit` | the rope jammed instead of tightening — a result, not a failure |
