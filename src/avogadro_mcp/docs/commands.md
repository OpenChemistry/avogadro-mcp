# Avogadro RPC command tables

Bundled with `avogadro-mcp` and served as the `avogadro://docs/commands`
resource. These are the command tables from
`two.avogadro.cc/source/develop/rpc.md` ("Built-in methods" and "Plugin
commands"), copied verbatim so this package does not depend on a sibling
checkout of the website at runtime. For live, authoritative parameter
schemas prefer `list_commands()` / `avogadro://commands`, which merges this
static catalogue with whatever the running Avogadro reports; this page is
the prose companion.

Re-sync by re-copying the two tables below (and the shared-options note
under Surfaces) from the current `rpc.md` when they change.

## Built-in methods

These are handled by the application itself.

| Method | Parameters | Description |
|--------|-----------|-------------|
| `version` | none | Report the running versions and RPC protocol level. |
| `listCommands` | none | List every command Avogadro will answer, built-in and plugin alike. |
| `openFile` | `fileName` | Read a file from disk and make it the active molecule. The format is inferred from the extension (e.g. `POSCAR`, `cml`, `xyz`). |
| `loadMolecule` | `content`, `format` | Read molecular data from a string in any format Avogadro can read, and make it the active molecule. |
| `moleculeInfo` | none | Summarize the active molecule: atom/bond counts, formula, mass, charge, and what it has (a unit cell, a basis set, vibrations, ...). |
| `getMolecule` | `format` (default `cjson`) | Read the active molecule back as text, in any format `FileFormatManager` can write, without touching disk. |
| `exportFile` | `fileName` | Write the active molecule. The format is inferred from the extension. Runs on a background thread -- pass `wait` to be told when the write has actually finished. |
| `saveGraphic` | `fileName` | Save a bitmap image of the active view at its current on-screen size. `.png` is appended if the name has no extension. |
| `renderImage` | `width`, `height`, `transparentBackground`, `fileName` | Render the view at a chosen size, to a file or back inline as PNG bytes. `width`/`height` must be given together or omitted together; reply always reports the true `width`/`height` produced plus `nativeWidth`/`nativeHeight` of the underlying grab. |
| `setProjection` | `type`, or `perspective` / `orthographic` | Switch the camera projection. Either `{"type": "perspective"}` or a bare `{"orthographic": true}` key works. |
| `setRenderTypes` | `types`, or one key per type | Choose which display types are active. |
| `listDisplayTypes` | none | List the scene display types and whether each is on, applicable to the current molecule, and has settings of its own. |
| `internalPing` | none | Answers `"pong"`. Handled by the RPC layer itself, so it works even before a window exists. |
| `kill` | none | Quit Avogadro. Refused unless Avogadro was started with `--testing`, which the argument parser currently rejects -- unreachable in practice. |

Every method above needs an open window except `internalPing` and
`version`. `version` answers before that check, so a client can negotiate
compatibility with a starting instance rather than raising an error just
because no window exists yet.

## Plugin commands

Tools and extensions register their own commands at startup, and any of
them can be sent as a JSON-RPC method name. Commands that belong to a tool
temporarily make that tool active, run, and then restore the tool you were
using. Unless noted otherwise, a command needs an open molecule.

### Atoms and bonds

| Command | Parameters | Description |
|---------|-----------|-------------|
| `createBonds` | none | Perceive bonds between all atoms, or within the selection if there is one. |
| `removeBonds` | none | Remove bonds from all atoms, or from the selection. |
| `addBondOrders` | none | Perceive bond orders. |
| `centerAtom` | `index` (or `id`) | Move the molecule so that this atom sits at the origin. Atoms are numbered from zero. |
| `alignAtom` | `index` (or `id`), `axis` | Rotate the molecule so this atom lies along an axis. `axis` may be `"x"`, `"y"`, `"z"` or `0`, `1`, `2`. |

### Camera and view

| Command | Parameters | Description |
|---------|-----------|-------------|
| `rotateScene` | `x`, `y`, `z` | Rotate the camera about the screen x, y, and z axes, through the current focal point. |
| `zoomScene` | `delta` | Zoom in or out. Positive values move toward the molecule. |
| `translateScene` | `x`, `y` | Pan the camera by a distance given in screen pixels. |
| `alignView` | none | Align the view to the x, y, and z axes, centered on the molecule and backed off far enough to frame it. |

### Crystals

| Command | Parameters | Description |
|---------|-----------|-------------|
| `wrapUnitCell` | none | Wrap all atoms back into the unit cell. |
| `standardCrystalOrientation` | none | Rotate the unit cell into the standard orientation. |
| `fillUnitCell` | none | Add the symmetry-equivalent atoms implied by the space group. |
| `fillTranslationalCell` | none | Fill the cell including atoms related by translation. |

### Surfaces and orbitals

| Command | Parameters | Description |
|---------|-----------|-------------|
| `renderVanDerWaals` (or `renderVDW`) | `resolution` | van der Waals surface. |
| `renderSolventAccessible` | `resolution` | Solvent-accessible surface. |
| `renderSolventExcluded` | `resolution` | Solvent-excluded surface. |
| `renderMO` (or `renderOrbital`) | `orbital`, `isovalue`, `resolution`, `spin` | Molecular orbital, for a file that includes a basis set. |
| `renderElectronDensity` | `isovalue`, `resolution`, `spin` | Total electron density. |
| `renderSpinDensity` | `isovalue`, `resolution`, `spin` | Spin density. |
| `renderCube` | `index`, `isovalue` | Render a cube that came with the file. `index` counts from one and defaults to the first. |

Shared options:

* `orbital` -- either a number, counting from one so that `1` is the lowest
  orbital, or a string relative to the frontier orbitals: `"homo"`,
  `"lumo"`, `"homo-1"`, `"lumo+2"`, and so on.
* `isovalue` -- the isosurface value, default `0.03`.
* `resolution` -- the cube spacing in Å. If left out, Avogadro picks a value
  from the size of the molecule, between 0.05 and 0.5 Å.
* `spin` -- set to `"beta"` for the beta orbitals or density of an
  open-shell calculation; alpha is used otherwise.

Every command in this group runs in the background, so pass `wait` when the
next thing your script does depends on the surface existing.

### Vibrations

| Command | Parameters | Description |
|---------|-----------|-------------|
| `showVibrations` | none | Open the vibrational modes dialog. |
| `setVibrationalMode` | `mode` | Select a mode, numbered from zero. |
| `setVibrationalAmplitude` | `amplitude` | Set the animation amplitude on the same 0-99 scale as the dialog slider, which starts at 20. |
| `startVibrationAnimation` | none | Start animating the selected mode. |
| `stopVibrationAnimation` | none | Stop the animation. |
| `generateDisplacedCoordinates` | `modes` (or `mode`), `scale`, `structures` | Append coordinate sets displaced along one or more modes. `modes` is a list of mode numbers, `scale` defaults to 1.0, and `structures` (default 1) is how many displaced geometries to generate. |
