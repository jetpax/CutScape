# Inkcut fork — handover

Context for a Claude Code session picking this up. Written 2026-10-29 after a
debugging chat getting Inkcut + Inkscape driving a Secabo C IV on Ubuntu 24.04.

## Goals

1. **Fork** inkcut/inkcut with the three fixes below (and send them upstream as PRs).
2. **One-step install as an Inkscape extension** — no venv in /opt, no symlinks,
   no PATH games, works for every user account.
3. **UI that feels part of Inkscape**, not a separate app with its own windows,
   docks and device dialog.

## State of this repo

Base: upstream `master` @ `5e5f0b7` (merge of #440, the asyncio port).

| branch | content |
|---|---|
| `fix/lpr-printer-transport` | Printer transport on Linux/macOS never started `lpr` |
| `fix/inkscape-selection-ids` | Extension passed XPath keys; Inkcut matched on `@id` → whole doc sent |
| `fix/svg-selection-descendants` | Selection kept ancestors, not descendants → selected groups empty |
| `dev` | CutScape (default branch). The three fixes were merged in, then the Qt app was removed; start new work here |

Each fix branch is one commit with the root cause in the message, ready for
`gh pr create --repo inkcut/inkcut --fill`. Origin still points at upstream:
`gh repo fork --remote` then push the branches. All three were verified on the
real machine (patched into an installed 2.2.0) — the cutter cut "Jo" from a
selection via CUPS.

### The bugs, in one line each
- `transports/printer/plugin.py`: `loop.subprocess_exec(...)` not awaited, argv
  passed as a nested list; `process_exited(status)` is a Twisted signature;
  `close()` killed `lpr` so the job was discarded. Fix spawns
  `lpr -P <q> -o raw`, writes via pipe 0, closes stdin and waits for exit.
- `plugins/inkscape/inkcut_cut.py`: inkex 1.x `svg.selected` is keyed by XPath
  (`/*/*[3]/*[7]`). Now passes `el.get('id')`.
- `core/svg.py` `QtSvgDoc.__init__`: `valid_nodes.add(node)` →
  `valid_nodes.update(node.iter())`; `while parent:` → `is not None`.
  Note: an empty `valid_nodes` is falsy and silently disables filtering
  (`QtSvgG.parse`) — worth making that explicit rather than relying on it.

### Known, not fixed
- `Win32PrinterConnection.write()` calls `super().write()` without `await`.
- `requirements.txt` says PyQt6, the working install used PyQt5 5.15 — qtpy
  abstracts it, but pin/test both.
- No single-instance IPC: every extension run launches a fresh Inkcut app.
- `Job.__getstate__` blanks stdin documents, so a restarted Inkcut shows an
  empty preview (by design, but confusing).
- `QtSvgG.parse` raises on `<text>`; `<image>` is ignored silently.
- `inkcut4inkscape.convert_objects_to_paths` shells out to `inkscape` on a temp
  file — slow, and runs whatever `inkscape` is first on PATH.
- `lpadmin -m raw` is deprecated in CUPS; will break in a future CUPS.

## Why install is painful today (all hit in practice)
- `pip install` into system Python is blocked (PEP 668) → user made
  `/opt/inkcut/venv`; extension then can't find `inkcut` → needs a
  `/usr/local/bin` symlink; other accounts can't read the venv.
- Extension files are per-user (`~/.config/inkscape/extensions`); second
  account had nothing.
- Snap/Flatpak Inkscape is sandboxed: can't exec `/opt/...`, has its own
  Python (no `lxml` reports), its own extension dir. Two Inkscapes installed
  (deb + snap edge) → the menu showed the wrong one's extensions.
- PyQt wheel needs X libs not pulled in: `libxcb-xinerama0 libxcb-cursor0` etc.
  ("Could not load the Qt platform plugin xcb").
- Deb Inkscape 1.2's own Extension Manager needs `python3-appdirs` etc.

## Direction (proposals — decide before building)

### A. Packaging
Ship as a **single Inkscape extension zip** installable via Extensions →
Manage Extensions (or unzip into the user/system extensions dir). Two ways to
handle heavy deps:
1. **Bootstrap**: extension creates a private venv on first run
   (`~/.local/share/inkcut/venv`, or `$XDG_DATA_HOME`), `pip install`s pinned
   deps, shows progress in Inkscape. Simple, but Qt wheels + xcb libs remain.
2. **Drop Qt from the cut path** (preferred, pairs with B): only `lxml` +
   `pyserial` (+ optional `pycups`), both pure-ish and already shipped or
   trivially vendored. No venv at all; runs on Inkscape's own Python.

Either way: detect snap/flatpak Inkscape and say clearly what won't work.

### B. Inkscape-native UI
Inkscape already ships an HPGL plotter extension (Extensions → Export → Plot,
`plotter.inx`/`plotter.py` + `hpgl_encoder.py`). It's the model to follow:
- `.inx` notebook tabs for **Device** (driver preset, transport: CUPS queue /
  serial port+baud+flow / `/dev/usb/lpN` / file), **Material** (width, origin,
  mirror for flex), **Cut** (speed, force, overcut, blade offset, copies,
  weedline), all persisted by Inkscape between runs.
- **Live preview** checkbox: draw the toolpath (cut moves + travel moves in a
  distinct style) as a temporary layer on the canvas instead of a separate
  pyqtgraph window.
- Run = send. Errors via `inkex.errormsg`.
- Selection semantics are Inkscape's own (`self.svg.selection`), converting
  text/shapes in-process with inkex (`to_path_element`) — no CLI round-trip.

Keep the existing Inkcut app as an optional "Advanced…" launcher for job
history, joystick, etc., rather than rewriting it.

Plan: extract a Qt-free core — SVG→polylines (inkex can do this),
device config, filters (overcut, blade offset, clip, ordering), HPGL/DMPL/
GPGL protocol encoders, transports — into a package both the extension and the
Qt app import. `inkcut/core/svg.py` is QPainterPath-based; replacing it with
inkex path ops is the main porting cost.

## Test hardware / environment
- Ubuntu 24.04, kernel 7.0, deb Inkscape 1.2.2 (`/usr/bin/inkscape`), Python 3.12.
- Secabo C IV (C60 IV): rebadged SAGA, USB **printer class**, `0483:5750`
  (lsusb misnames it an STM "LED badge"), `usblp` → `/dev/usb/lpN`.
  CUPS: `usb://SAGACNC/SAGA%20Cutter%20Plotter?serial=...` raw queue `secabo`.
  HPGL, 40 units/mm. Inkcut driver preset "Secabo C60ii" works.
- Smoke test: `printf 'IN;PU0,0;PD2000,0,2000,2000,0,2000,0,0;PU0,0;' | lp -d secabo -o raw`
  → 50 mm square. Cutter must be online (panel) or CUPS sits at
  "Sending data to printer".

## Progress, 2026-10-29 (second session)

- Upstream PRs are open: inkcut/inkcut#443, #444 and #445. #443 and #445
  include regression tests. The fork is renamed **jetpax/CutScape**
  (`origin`); `upstream` is inkcut/inkcut.
- The **CutScape prototype** is in `extension/`. It takes the inkex-only path
  (B with A.2), and `README.md` documents it.
  - The core is plain Python: geometry, filters, protocols, transports,
    devices and job. Only `document.py` and `preview.py` use inkex.
  - Tests are in `tests/test_cutscape_*.py`. `tools/package_extension.py`
    builds the install zip.
  - It targets Inkscape 1.4 and later; the cutter box will be upgraded from
    1.2.2.
- **Decisions:**
  - Preview vs send is a radio in the dialog, not Inkscape's live preview,
    because a script can't tell a preview run from Apply.
  - The preview is a locked layer, laid over the drawing.
  - Material space has x across the material and y along the feed.
  - Preset invert flags reflect the job in place, and park stays at the
    origin.
  - HPGL defaults to 40 steps/mm; Inkcut used 1021 dpi.
  - Reports and confirmations open in CutScape's own GTK window
    (`cutscape/notify.py`), not stderr. Inkscape 1.4 attaches "received
    additional data … results will not be as expected" to any stderr
    from a successful effect, and `show-stderr` only applies to
    custom-GUI effects (it still showed the warning when tried). Without
    GTK, or with CUTSCAPE_STDERR=1 (tests), it falls back to stderr.
- **Folded in from unmerged upstream PRs:** #382, #388, #427, #428, #441 and
  #442. The README credits them.
- **Released** as [v0.1.0](https://github.com/jetpax/CutScape/releases/tag/v0.1.0),
  then [v0.1.1](https://github.com/jetpax/CutScape/releases/tag/v0.1.1) (docs and repo cleanup)
  and [v0.2.0](https://github.com/jetpax/CutScape/releases/tag/v0.2.0) (connections: find cutters, USB ids,
  stall timeouts, test square, and managing the CUPS queue: create, restart,
  remove, in `extension/cutscape/printers.py`), then
  [v0.3.0](https://github.com/jetpax/CutScape/releases/tag/v0.3.0) (Add Cutter / Remove Cutter, reports in
  CutScape's own window) and [v0.3.1](https://github.com/jetpax/CutScape/releases/tag/v0.3.1) (Cut failed with a
  usage message when 0.2.0 had saved an action it no longer offers), then
  [v0.4.0](https://github.com/jetpax/CutScape/releases/tag/v0.4.0) (desktop CNC: Z lift for G-code, CNC 3018
  preset, GRBL streaming), then
  [v0.5.0](https://github.com/jetpax/CutScape/releases/tag/v0.5.0) (print and cut: crop marks, lining up
  with arrow buttons), [v0.5.1](https://github.com/jetpax/CutScape/releases/tag/v0.5.1) (calibrate the laser
  with no crop marks) and [v0.6.0](https://github.com/jetpax/CutScape/releases/tag/v0.6.0) (Secabo presets from
  DrawCut's driver data, preset width fixes, problems in CutScape's own window).
  The Extensions > CutScape
  submenu has five entries, each an .inx running `cutscape_cut.py`: Cut…
  passes its "Apply will" choice as `--action` (preview, send, test
  square, line up, calibrate the laser), and Add Cutter…, Remove Cutter…, Add Crop Marks and
  Remove Preview (the last two with no dialog)
  pass a hidden `--entry`. Inkscape stores settings per entry, so anything
  that needs the device or connection settings stays in Cut…. It also
  passes a saved choice the dialog no longer offers as it is (0.2.0 saved
  `action="find"`), while showing the first option as chosen; the script
  treats any other `--action` as preview. Each release's zip is built by
  `tools/package_extension.py`; `dev` is the default branch.
- **Translations.** The UI is translated (`translationdomain="cutscape"`):
  sources live in `po/`, compiled catalogs in `extension/locale/`, and
  `tools/i18n.py` does extract/compile. French is complete.
- **Add Cutter** (`extension/cutscape/cutter.py`, 0.3.0) replaced the
  0.2.0 Find Connected Cutters report and Printer Queue dialog. It picks
  the best connection it finds (usblp device by USB id, then a raw CUPS
  queue for a USB printer with no queue, then the queue it made before,
  then a USB serial port), sets it up, and saves model + connection in
  `cutter.json` (`CUTSCAPE_CONFIG` overrides the path; the tests use it).
  Cut's Device and Connection default to "The cutter you added". Admin
  steps (lpadmin, the udev rule) go through the system's own password
  prompt (`cutscape/admin.py`: osascript on macOS, pkexec on Linux), with
  the sudo commands as the fallback. Running it again restarts a stopped
  queue. Remove Cutter has a label-only dialog so it takes a second
  click. "No cutter found" and other add/remove problems show in
  CutScape's window, not as an Inkscape error. Not yet tried against a
  real cutter, CUPS or polkit: only fakes.
- **Desktop CNC / GRBL** (0.4.0): G-code lifts to `gcode_z_up` and
  plunges to `gcode_z_down` (Device tab; the same value = no Z moves, the
  old behaviour), with the feed rate on the plunge too. A plan for G-code
  with Speed 0 reports a problem, since GRBL answers a G1 without a feed
  rate with error:22. The `CNC 3018 (GRBL)` preset is 300 x 180 mm, no
  axis swap; presets can carry `serial` (baud, flow), which Add Cutter
  saves for a serial connection and `cutter.use` applies. `_send_acked`
  sends two blank lines, waits `WAKE_DELAY` (2 s) for the controller to
  reboot after the port opens, and drops its welcome. Only tested against
  a fake GRBL.
- **Inkscape's warning dialog** (0.6.0): Inkscape shows anything on stderr under
  its "additional data" warning, and inkex's `deprecated/main.py` turns on warnings
  Python normally hides, so importing GTK for CutScape's own window set it off with
  `ImportWarning`s. `cutscape_cut.py` now ignores warnings after importing inkex,
  and `notify.gtk` silences stderr once a window is available. `effect` sends every
  problem to that window, and a bug with its traceback; only with no window do they
  go to Inkscape. `CUTSCAPE_STDERR=1` keeps them on stderr, for tests.
- **Print and cut** (0.5.0): LAPOS-style alignment, Linux USB first.
  - The C IV manual says LAPOS is a laser pointer on the head, not a sensor:
    DrawCut (Windows) prints 4 crop marks, the user aims at each, and the
    software does the maths. So nothing here depends on a Secabo protocol, and
    it works on any cutter, aiming with the blade tip if there's no laser.
  - `document.py` finds the layers and marks, `marks.py` is Add Crop Marks,
    `align.py` is the maths, the head and `Session` (no GTK), and
    `align_window.py` is the window; `job.plan_on_sheet` builds the cut. The fit
    goes from the page straight to head space, having allowed for the preset's
    axis settings, so the plan has them turned off.
  - CutScape can't ask where the head is, so it tracks its own pen-up moves,
    as DrawCut does. Moves stop at the origin, as DrawCut's can.
  - **Read from DrawCut Lite and Pro**, statically and with the user's approval
    for interoperability; nothing from it is in the repo. The C IV uses driver
    `saga2a_base`, the S and T series `saga2b_base`, which differs only in speed:
    - The laser is the **Laser** button on the panel (photo on page 5 of the
      C IV manual). No laser-on command turned up: `cUseLP` only enables fields
      and applies the offset.
    - An arrow press sends one pen-up move from the driver's template,
      `PU;PA x,y;PU;`. Up = +X, Down = -X, Left = +Y, Right = -Y. X is the feed
      (its "Roll Forward" macro is `PA4000,0`); the origin is at the front right
      and DrawCut wants it near the bottom-right mark. That is `swap_xy` +
      `invert_y`, the `SAGA` settings in `devices.py`. DrawCut's alignment dialog
      maps its axes this way for every driver, so other presets may be wrong too;
      they were left alone, since changing them would rotate existing setups.
    - Driver fields 23 and 24, -33 and -2.4, are the default laser offset, with
      head = target + offset: the dot is 33 mm along +Y and 2.4 mm along +X from
      the blade. Only the SAGA drivers have one. `foison_base` and the others
      are 0.
    - Replies over USB were only seen for automatic scanning on T-series LAPOS
      XPT: `CO;:H A L0 ECN U P1;US97; C0 TB;SN1,1;!1:0,0;!2:%d,0;!3:0,%d;!4:%d,%d;`
      starts it and `CO OZ;` reads `!1:x,y;...!OK;`. No position query was found.
    - Its widths exposed preset errors, fixed since. `Summa T600` and `Rheinstern
      LB-720A` were ten times too wide because Inkcut says `575cm` and `720cm`;
      the reported-cutters list has 575 and 720. DrawCut says 600 for the T600,
      but 575 is the safer limit. The Stika and `PNC-900` widths were media
      widths, and are now DrawCut's cutting widths. Others still differ a
      little, but many of DrawCut's are 584.2, its generic default, so they
      were left alone.
    - It also lists 272 detailed models (Foison, Graphtec, Mimaki, Roland, Secabo,
      Summa) and a table of 3227 entries from 346 brands (brand, model, width,
      language), including CNC machines and printers. Not copied here.
  - **To check on a real cutter, none of it known yet:**
    - That the laser stays on once the cutter is online and the head moves.
    - The arrow directions, and that a sheet loaded upright lands as described.
    - Whether `VS` is cm/s or mm/s on this firmware: DrawCut's driver lists speed
      in mm/s and CutScape sends cm/s, so a cut could run ten times too slow.
    - DrawCut's jobs start `MK0,0,len,width;IN;` and end `PU;PA0,0;!PG;`.
      CutScape sends neither `MK` nor `!PG;`; the Secabo cut without them earlier.
    - Whether `OA;` or `CO OZ;` answer on a C IV.
  - Rehearsed end to end against a stand-in cutter on a FIFO: real effect
    script, device-file transport and window contents, a sheet 1.2° crooked and
    stretched, cut within 0.08 mm of the line. The real window was only opened
    and cancelled, with scripted clicks.
- **The Qt app is gone from `dev`.** The `inkcut/` package, its tests, docs,
  packaging, the old Inkscape plugin and the Qt CI were all removed. CI now runs
  only the CutScape tests. The `fix/*` branches still sit on upstream `master`
  for the upstream PRs. Inkcut's community device table is kept as
  `docs/inkcut-reported-cutters.md`.
- **Not done:**
  - A real cut on the Secabo.
  - Checking the orientation of the Stika, Silhouette and VEVOR presets.
  - A live preview using 1.5's `pipe-diffs`.
  - Windows printing.
  - Silhouette USB and Bluetooth.
  - The CH554 USB transport from #428.
- **USB id clash:** #428 lists the VEVOR KH-870 as USB `0483:5750`, the same
  id as the Secabo C IV above. It says that firmware only cuts after CDC
  line-coding and DTR/RTS requests, yet the Secabo cut through usblp/CUPS.
  The two probably run different firmware behind the same id.
- **GTK under Inkscape's Python on macOS:** outside Inkscape it needs
  `GI_TYPELIB_PATH`, `DYLD_LIBRARY_PATH`, `GDK_PIXBUF_MODULE_FILE` and
  `XDG_DATA_DIRS` set as Inkscape sets them. `Gtk.OffscreenWindow.present()`
  segfaults. The venv has no GTK, so `tests/test_cutscape_align_window.py`
  skips there.
- **macOS crash:** Inkscape 1.4 launched from a shell with `ulimit -n` around
  1M crashes in GLib's spawn whenever it runs any extension. Launch it with a
  normal limit.

## Suggested first steps
1. Fork, push `fix/*`, open the three upstream PRs.
2. Add regression tests on `dev`: `tests/test_svg.py` selecting a group by id;
   a printer-transport test with a fake `lpr` on PATH.
3. Prototype B as a standalone `.inx` + script using inkex only, HPGL out to a
   file transport first, then CUPS/serial/usblp.
4. Decide A.1 vs A.2 once the prototype shows how much of Inkcut's core is needed.
