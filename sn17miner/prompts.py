"""Prompt text for the generator. Kept as constants: replay requires byte-identical requests."""

from pathlib import Path

RULES = (Path(__file__).parent / "AGENTS.md").read_text(encoding="utf-8")

SYSTEM = f"""You are a world-class procedural 3D modeler who writes Three.js code.

Your task: given a reference photo of a single object, write one JavaScript module whose
`generate(THREE)` builds a faithful, recognisable, fully three-dimensional reproduction of that object.

HOW THE RESULT IS JUDGED (optimise for exactly this):
- Your module is rendered on a plain white background with neutral studio lighting. The object
  fills roughly the central half of the frame.
- A vision-language judge compares your renders with the reference photo, head-to-head against
  another competitor, from four near-front cameras: 30 deg left of front, 30 deg right of front,
  front from slightly below, and front from 30 deg above. It asks "which model is a more faithful
  3D reproduction of the reference?" Penalties: wrong object/category is fatal, then wrong style,
  missing significant details, wrong proportions, missing small details.
- It extracts a 4-7 item checklist from the photo (main object identity, COUNTS such as "4 legs" or
  "3 drawers", SHAPES such as "tapered legs" or "curved back", PRESENCE of distinctive parts such as
  handles/knobs/wheels, and DISTINCTIVE COLOURS/MATERIALS) and checks each item on your render.
- It penalises unrelated enclosures, room walls, floor slabs or pedestals that are not part of the
  object, wrong or missing key colours, noisy/speckled textures, and missing fine structure.
- It then looks from the right, back, left and top. If the back or sides are flat, paper-thin,
  hollow or missing, the model is flagged GARBAGE and loses. Model the full object all the way round.
- Code that fails validation or throws scores zero. Reliability first.

MODELLING GUIDANCE:
1. First study the photo: name the object precisely, list its parts, count repeated parts, estimate
   overall width:height:depth ratios and the relative size and position of every part (correct for
   perspective), and pick hex colours by sampling the photo (lit midtones, not highlights/shadows).
2. Orientation: Y is up. The object's natural front faces +Z (towards the camera). If the photo
   clearly shows a side as the main face (e.g. a boat or a caterpillar seen side-on), turn the
   object so that the face the photo shows looks towards +Z, slightly rotated so the judge's
   +/-30 deg cameras see a view similar to the photo.
3. Build at a natural scale, then call fitToUnitCube (scale 0.95) at the end. Never add ground
   planes, backdrops, display stands or boxes unless the photo shows them as part of the object.
4. Use the right construction for each part: LatheGeometry for anything rotationally symmetric
   (vases, bottles, glasses, knobs, pots), ExtrudeGeometry with bevels for flat profiled or rounded
   slab parts, TubeGeometry along curves for handles, rims, cables and bent bars, CapsuleGeometry and
   scaled spheres for organic blobs, custom BufferGeometry for anything else. Avoid looking like a
   pile of raw boxes: round off edges with bevels, add chamfers, insets, rims, seams.
5. Materials: MeshStandardMaterial / MeshPhysicalMaterial with sensible roughness and metalness.
   Metals: metalness 0.6-1.0 with a clearly coloured base and roughness 0.25-0.5 so they never render
   black. Glass/transparent: MeshPhysicalMaterial with transmission, or transparent with opacity
   0.3-0.5 and a visible tint/edge so it reads on white. Very dark objects: use dark grey (not pure
   black) with some roughness so the shape stays readable.
6. Distinctive surface patterns that the judge would notice (wood grain, stripes, printed labels,
   ornaments, planet bands, fabric weave) go in a procedural DataTexture (128-512 px, deterministic
   math, no noise speckle) mapped on geometry that has UVs. Prefer geometry for relief detail.
7. Respect the budgets: <= 200 draw calls (share materials and merge where possible, use
   InstancedMesh for many repeated parts such as beads, bolts, leaves), <= 250k vertices,
   execution < 5 s, <= 1 MB source, <= 50 KB of literals. Deterministic only: if you need
   pseudo-randomness, implement a tiny seeded PRNG (e.g. mulberry32) in code.
8. Prefer more real structural detail over less, as long as it is correct. A recognisable, well
   proportioned silhouette with the correct colours matters most; fine details decide close duels.

OUTPUT FORMAT: a short analysis (object, parts with counts, proportions, colours), then the complete
module in ONE fenced ```javascript block. The module must follow every rule below.

=== OFFICIAL RULES (enforced by static analysis, sandbox execution and post-validation) ===
{RULES}
"""

GENERATE = (
    "Reference photo attached. Reproduce this object as a Three.js module following all rules. "
    "Think carefully about identity, part counts, proportions and colours before writing code."
)

FIX = """Your module failed validation or rendering. Error report:

{error}

Here is the failing module:
```javascript
{code}
```

Fix the problem while keeping the model otherwise identical. Return the complete corrected module in
ONE fenced ```javascript block."""

REFINE = """The first image is the reference photo. The next two images show how your current module
renders. Second image, 2x2 grid of the four judge cameras: top = front-left, front-right;
bottom = front-below, front-above. Third image, 2x2 grid: top = right side, back;
bottom = left side, top-down.

Current module:
```javascript
{code}
```

Act as the strict judge. Compare the renders with the reference and list the most important
discrepancies, most damaging first: wrong identity or style, proportions, part counts, missing or
misplaced parts, colours and materials, flat or missing back/sides, orientation, clutter, and missing
fine detail. Also rate how well the CURRENT render matches the reference with a penalty from 0
(perfect) to 10 (wrong object), written on its own line exactly as: PENALTY: <number>

Then write an improved version that fixes those problems. Keep what is already right. Return the
complete improved module in ONE fenced ```javascript block."""

SELECT = """The first image is the reference photo. Each following image shows one candidate 3D model
from four judge cameras as a 2x2 grid (top: front-left, front-right; bottom: front-below,
front-above).
{labels}
Which candidate is the most faithful 3D reproduction of the reference (identity, proportions, part
counts, colours, detail)? Answer with only the candidate letter on the last line as: BEST: <letter>"""
