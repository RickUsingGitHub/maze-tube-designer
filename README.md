# Maze Tube Designer

A Windows desktop app that designs **3D-printable cylinder maze puzzle boxes**: a tube inside a tube.

* **Body** (inner tube, orange): a closed-bottom tube with a maze carved into its outer surface.
* **Cap** (outer tube, blue): slides over the body. Nubs on its inside ride in the maze grooves,
  so the cap comes off only after you guide it through the whole maze. The maze is hidden
  under the cap while you solve it.

The app makes the maze, shows both parts in 3D, animates the solution, and exports
watertight STL files that are already oriented for printing.

![Maze Tube Designer main window](docs/images/main_window.png)

| Solution animation (X-ray cap) | Home detent: the nub clicks into a pocket when closed |
|---|---|
| ![Solution animation](docs/images/solution_animation.png) | ![Home detent](docs/images/home_detent.png) |
| **Spiral vase mode: one continuous 0.6 mm wall** | **Printable maze map with the solution** |
| ![Vase mode section](docs/images/vase_section.png) | ![Maze map](docs/images/maze_map.png) |

## Features

* Mazes on a wrapped cylinder grid, using recursive backtracker, Prim, Kruskal or Wilson. You can set
  the seed, add a sideways bias and add extra loops, and home and exit can be placed for the longest
  solution.
* Use 1–6 nubs; the maze repeats once per nub, so all the nubs move together.
* A **home notch** and a snap **home detent** hold the cap shut at the closed position.
* **45° ceilings** let both parts print without supports.
* **Spiral vase mode** prints each part as one continuous wall.
* **Long tubes**: set the body and cap lengths separately, so only part of the length is maze.
* Live 3D view (print layout, assembled, exploded, X-ray, section), solution animation, and an
  unrolled maze map that exports to PNG or SVG.
* An automatic **fit check** moves the nubs along the whole solution and confirms they clear the body.
* Designs save as `.mazetube` files (JSON with every parameter plus the exact maze).

## Download and run

* **Windows app (no Python needed):** download `MazeTubeDesigner-windows.zip` from the
  [Releases](../../releases) page, unzip it, and run `MazeTubeDesigner.exe`.
* **From source** (Python 3.10+, any OS with OpenGL):
  ```
  git clone https://github.com/RickUsingGitHub/maze-tube-designer.git
  cd maze-tube-designer
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
  .venv\Scripts\python main.py
  ```
  On Windows you can also just double-click `run.bat`, which sets everything up on first run.
* **Build the Windows app yourself:** `build_exe.bat` (uses PyInstaller; output goes to `dist\`).

## Using it

Change any value in the left panel and the design rebuilds immediately.

| Group | Key parameters |
|---|---|
| Maze | rows (or Auto = fill a **maze length**), columns (Auto fits the circumference), number of nubs (the maze repeats once per nub), path/wall width, algorithm, seed, sideways bias, extra loops, home notch, home detent |
| Body | maze diameter, groove depth, core wall, **body length**, grip band, plain wall, bottom thickness |
| Cap | wall, top thickness, **clearance** (0.25–0.35 mm suits most FDM printers), **cap length** |
| Print & finish | 45° ceilings (prints with no supports), edge chamfer, grip flutes, facet size |

* **View:** Print layout / Assembled / Exploded / Body only / Cap only, with *X-ray cap* and *Section*.
  Left-drag rotates, right-drag (or Shift+drag) pans, the wheel zooms, and double-click or F refits the view.
* **Play solution (F5):** animates the cap through the maze and tracks the nubs on the map.
* **Maze map:** the unrolled maze as seen from outside, with HOME, EXIT and the solution.
  *Export maze map* saves it as PNG or SVG, e.g. as a printed solution card.
* **Save / Open:** `.mazetube` files (JSON) store every parameter plus the exact maze.
* **Export STLs (Ctrl+E):** writes `name_body.stl` and `name_cap.stl`. The menu next to it also exports
  each part on its own, or a single print-plate STL with both parts.

The panel on the right lists the dimensions, the solution length and any warnings. It also shows an
automatic **fit check**: the app moves the cap's nubs along the whole solution path and confirms they
never touch the body.

## Printing tips

* Print the body upright. Print the cap upside down, open end up (the exported cap is already flipped).
* **Slicer settings:** at least **3 wall loops**, **4–5 top layers**, 3–4 bottom layers and 15–20% infill.
  With 1 wall and 0 top layers (typical vase/draft profiles), the body's floor and the inside of the
  cap's end come out as open infill, and the maze ridges are hollow outlines.
* Leave *45° ceilings* on to print without supports. Use 0.2 mm layers or finer.
* If the fit is too tight, raise the clearance by 0.05 mm and print again. Test with a small, low design first.
* The **home notch** lets the cap close flush only at the home position, so a finished puzzle always
  starts from the beginning.
* The **home detent** adds a small ramped ridge across the home notch. Closing the cap makes the nub
  click over it into a pocket, which holds the cap shut until you pull it back over. The value is the
  interference the cap wall has to flex past: 0.2–0.35 mm suits 2 nubs, 0.15–0.25 mm suits 3+ nubs,
  and 0 turns it off. The notch is deepened automatically so the nub clears the ridge on both sides.

## Long tubes (only part of the length is maze)

The maze always sits at the top of the body, where the cap slides on. Everything else can be plain tube:

* **Body length**: total body length. Everything below the maze section is plain tube.
* **Cap length**: total cap length. Anything longer than the maze runs past the body's rim and adds
  storage space inside the closed tube.
* **Rows → Auto + Maze length**: fits as many rows as possible into the length you give.
* **Plain wall**: a thinner wall for the plain part of the body (45° taper into the maze section, no
  supports), which saves a lot of filament on long tubes.

<img src="docs/images/long_tube_section.png" align="right" width="210" alt="Long tube, section view">

Example: body 250 mm, cap 250 mm, Rows Auto, maze length 100 mm gives a 95 mm maze (15 rows). It closes
to a 405 mm tube with about Ø33 × 400 mm of space inside. The panel on the right shows these figures.
Make sure the part lengths fit your printer's build height. The design is in
[examples/tall_tube_25cm.mazetube](examples/tall_tube_25cm.mazetube).

<br clear="right">


## Spiral vase (spiralize) printing

Turn on **Spiral vase mode** (in the *Spiral vase printing* group) to print both parts as one continuous
spiral wall. That's fast and uses very little filament. The design changes as follows:

* Every groove edge, the home notch floor and both faces of each nub become **45° slopes**, so each
  layer overlaps the one below. The tests check that no step anywhere is steeper than 45°.
* The parts are **exported solid** (file names end in `_vase`). The slicer prints only their outer skin.
  The cap's nubs are dents in its outer wall that push the thin wall inwards.
* The cap rim sits on a 45° shoulder instead of a flat ledge. Grip flutes are left off.
* Paths must be wider (at least 2 × groove depth + 2 × clearance + 0.6 mm). The app widens them
  automatically when you switch the mode on.
* In vase mode the 3D view shows the thin printed shells, so *Section* shows the real wall.

In the slicer: turn on Spiral vase / Spiralize. Set the **outer wall line width to the same value as
"Line width"** in the app (default 0.6 mm), because the cap's inner fit depends on it. Use about 1–2 mm
of bottom layers. Don't slice the `_vase` files normally: they would print as solid blocks.

## Project layout

```
mazetube/maze.py      maze generation on a wrapped grid, solver, statistics
mazetube/geometry.py  layout, radial height-map surfaces, watertight sweep meshes, fit check
mazetube/stl.py       binary STL writer
mazetube/viewer.py    OpenGL 3D viewer (PySide6 + PyOpenGL)
mazetube/mazeview.py  2D maze map
mazetube/app.py       main window
mazetube/params.py    design parameters + .mazetube save/load
tests/                geometry self-tests:  python -m tests.test_geometry
examples/             ready-made designs (e.g. a 25 cm long tube, normal and spiral vase)
```

### How the geometry works

Both mating surfaces are radial height maps r(θ, z). Each part is swept around the axis on a grid that
contains every break line. A step is modelled as two grid lines 0.05 mm apart, so every mesh stays a
plain deformed grid: closed, manifold and free of self-intersections, with no boolean operations.
The tests check this for many parameter sets. They also check that the cap clears the body along the
solution and that every closed maze wall really blocks the nub.

## License

MIT, see [LICENSE](LICENSE). STL files and puzzles you make with the app are yours to share or sell.

The Windows release bundles third-party libraries under their own licences: Qt / PySide6 (LGPLv3),
NumPy (BSD) and PyOpenGL (BSD).
