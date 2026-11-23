# PSFWidget Install Guide

**Draft** for version 0.0.1.dev0 (pre-release), updated 2026-11-20.
Companion: [User manual](USER_MANUAL.md). Status of the software: [STATUS.md](../STATUS.md).

This guide covers installing PSFWidget, checking the installation (and
recording the check for GLP), setting up an acquisition PC for scheduled
runs, upgrading, and uninstalling.

---

## 1. Before you start

### 1.1 What you need

| | Minimum | Recommended |
|---|---|---|
| Operating system | Windows 10 or 11, macOS (two latest versions), or Linux (64-bit) | |
| Python | 3.12 | 3.13 |
| Memory | 8 GB | 16 GB (large stacks are read lazily, one channel at a time) |
| Processor | 2 cores | 4 cores: a 1024 × 1024 × 100 field with 50 beads takes about 25 s |
| Disk | 2 GB for Python and PSFWidget with the GUI | plus room for reports and backups on the network |
| Display (GUI only) | OpenGL 2.1 | a graphics card with current drivers |
| Rights | install software in your user account | administrator rights only for system-wide scheduled tasks |

### 1.2 Choose what to install

PSFWidget comes in parts ("extras"). Pick the line that matches the computer:

| Computer | Install | Gives you |
|---|---|---|
| Analysis workstation (with GUI) | `psfwidget[napari]` | the napari GUI, reports, charts, Excel export, and the command line |
| Acquisition PC running scheduled QC | `psfwidget[report]` | the command line, reports, charts, Excel export, scheduled runs (add `[napari]` if staff also use the GUI there) |
| Headless analysis only | `psfwidget` | analysis to CSV and JSON, the results database, and the command line; no reports or charts |

The facility deployment (decided 2026-11-13) is **one installation per
acquisition PC**, each with its own local results database, writing reports
and database backups to network folders.

### 1.3 Licences

PSFWidget is MIT-licensed. All dependencies are compatible; the GUI uses
PySide6 under the LGPL. See [LICENSE_AUDIT.md](../LICENSE_AUDIT.md).

---

## 2. Install Python

PSFWidget needs **Python 3.12 or newer**. Check what you have:

```bash
python --version
```

If it is missing or older:

- **Windows:** install Python 3.13 from [python.org](https://www.python.org/downloads/windows/).
  Tick **Add python.exe to PATH**. Use `py -3.13` instead of `python` if
  several versions are installed.
- **macOS:** install Python 3.13 from [python.org](https://www.python.org/downloads/macos/)
  (or `brew install python@3.13`).
- **Linux:** use your distribution's `python3.13` package, or
  [uv](https://docs.astral.sh/uv/) (`uv python install 3.13`).

Conda environments also work (`conda create -n psfwidget python=3.13`), as
long as PSFWidget itself is installed with `pip` as below.

---

## 3. Install PSFWidget

Install into its own virtual environment so it does not interfere with other
Python software.

### 3.1 Create a virtual environment

Pick a permanent folder, for example `C:\PSFWidget` on Windows or
`~/psfwidget` elsewhere.

**Windows (PowerShell or Command Prompt):**

```bat
python -m venv C:\PSFWidget\venv
C:\PSFWidget\venv\Scripts\activate
```

**macOS and Linux:**

```bash
python3 -m venv ~/psfwidget/venv
source ~/psfwidget/venv/bin/activate
```

Your prompt now starts with `(venv)`. Activate the environment the same way
every time you use PSFWidget from a terminal.

### 3.2 Install

Until PSFWidget is published on PyPI (planned for v1.0), install it from
GitHub. You need [git](https://git-scm.com/downloads) for the first command.

```bash
python -m pip install --upgrade pip
python -m pip install "psfwidget[napari] @ git+https://github.com/CU-IBIF/PSFWidget.git"
```

Replace `[napari]` with `[report]`, or remove it, according to §1.2.

**Without git:** download the repository as a ZIP from GitHub
(*Code → Download ZIP*), unpack it, and install from the folder:

```bash
python -m pip install "C:\Downloads\PSFWidget-main[napari]"
```

**A specific version** (recommended for validated installations, so every PC
runs the same code): add `@<tag or commit>` to the URL, for example
`git+https://github.com/CU-IBIF/PSFWidget.git@v1.0.0`.

**Validated installations** should also pin every dependency, so each PC has
exactly the versions that were tested. Each release ships the pins
(`requirements/validated-report.txt` and `requirements/validated-napari.txt`,
also attached to the GitHub release):

```bash
python -m pip install -r validated-report.txt "psfwidget[report] @ git+https://github.com/CU-IBIF/PSFWidget.git@v1.0.0"
```

From v1.0 the package is planned to be on PyPI (or the facility's own
index), and `python -m pip install -r validated-report.txt "psfwidget[report]==1.0.0"`
replaces the git URL.

**Behind a proxy:** set `HTTPS_PROXY` (and `HTTP_PROXY`) before running pip,
or ask IT for the proxy settings.

---

## 4. Check the installation

### 4.1 Quick check

```bash
psfwidget --version
psfwidget --help
```

### 4.2 Self-test and IQ/OQ record (QA-05)

```bash
psfwidget selftest --out C:\PSFWidget\qualification
```

The self-test analyses synthetic bead stacks with known answers and checks:

1. the theoretical PSF formulas and model;
2. that the FWHM of synthetic beads is measured within 3%;
3. that re-analysis gives bit-identical results;
4. that the results database records and refuses changes (append-only);
5. that HTML and PDF reports are written (needs the `[report]` extra).

It prints PASS or FAIL for each and writes
`selftest_<computer>_<time>.json` with the environment (OS, Python, package
versions, computer, user) and an empty **sign-off** section. For a GLP
installation record, fill in `performed_by`, `reviewed_by`, `date`, and
`comments`, and file it with the instrument's records. Run the self-test
again after every upgrade.

The exit status is 0 when every check passes and 1 otherwise.

### 4.3 GUI check

Start napari from the activated environment:

```bash
napari
```

Then:

1. *File → Open Sample → PSFWidget → Synthetic 100 nm bead field*.
2. *Plugins → PSFWidget → PSF analysis*. The dock widget appears on the right.
3. Drag one of your bead files onto the napari window. It opens in the *PSF
   analysis* widget; for a file with several series, a dialog asks which one.

If napari does not start, see §8.

PSFWidget is tested with napari 0.10.0 and PySide6 6.12 on Linux, and with the
same versions on Windows in CI. Record the versions on each acquisition PC
with `napari --info` (keep the output with the IQ record).

---

## 5. Set up an instrument

Each instrument needs an **instrument profile**: a small TOML text file with
what its files do not record (bead size, the bead dye's emission peaks, and
sometimes the objective or pinhole). The examples in
[`examples/profiles/`](../../examples/profiles/README.md) match the facility's
SP8, LSM 980, AX R (NSPARC), and CSU spinning disk (Live-SR); there is also a
multiphoton template.

1. Copy the closest example to a permanent folder, for example
   `C:\PSFWidget\profiles\sp8.toml`, and edit it (or use the GUI's
   *Instrument profile editor*).
2. Check it against a real bead file:

   ```bash
   psfwidget info D:\QC\beads.lif --profile C:\PSFWidget\profiles\sp8.toml
   ```

   Every required value must be present; the output shows where each value
   came from (file, profile, or user).
3. Add the facility's acceptance limits under `[spec]` when they are agreed
   (none are shipped).

See the user manual, chapter 4, for every profile setting.

---

## 6. Acquisition PC: scheduled runs

### 6.1 Folders

| What | Where | Why |
|---|---|---|
| Results database (`qc.sqlite`) | **local disk** of the acquisition PC, e.g. `C:\PSFWidget\data` | SQLite on a network share risks corruption; PSFWidget refuses a database path on a network share (NFR-11) |
| Bead stacks to analyse (watched folder) | where the microscope software saves them, local or network | |
| Reports, `STATUS.json`, `ALERT.txt` | network folder, e.g. `\\server\qc\reports\sp8` | so everyone can read them |
| Database backups | network folder, e.g. `\\server\qc\backups\sp8` | protects the local database |

### 6.2 Configuration

Copy [`examples/automation/qc.toml`](../../examples/automation/qc.toml) to,
for example, `C:\PSFWidget\qc.toml` and edit the paths. Then check it:

```bash
psfwidget check-config C:\PSFWidget\qc.toml
```

It reports every problem by table and key, or prints what it would do and
what is due now. The user manual, chapter 9, describes every setting.

### 6.3 Run on a schedule

Choose **one** of the following.

**Windows Task Scheduler** (recommended on Windows). In a Command Prompt:

```bat
schtasks /Create /TN "PSFWidget QC" /SC MINUTE /MO 5 ^
  /TR "\"C:\PSFWidget\venv\Scripts\psfwidget.exe\" run --config \"C:\PSFWidget\qc.toml\""
```

Then, in Task Scheduler, open the task and set: *Run whether user is logged on
or not*, an account that can read the watched folder and write to the
network folders, and *Do not start a new instance* if one is running.

**cron** (Linux, macOS). Run `crontab -e` and add:

```
*/5 * * * * /home/qc/psfwidget/venv/bin/psfwidget run --config /home/qc/psfwidget/qc.toml >> /home/qc/psfwidget/cron.log 2>&1
```

**Built-in scheduler.** Keep this running in a terminal or as a service; it
checks every minute:

```bash
psfwidget watch --config C:\PSFWidget\qc.toml
```

`psfwidget run` only runs the jobs that are due (ingest, trend report,
backup) and exits, so starting it every 5 minutes is cheap. Two runs never
overlap. Scheduled runs lower their own process priority so they do not slow
an acquisition.

### 6.4 Check that it works

After the first run:

- `<reports root>/STATUS.json` exists and lists each configuration;
- `<reports root>/_psfwidget/psfwidget.log` and `runs/*.json` record the run;
- a backup appears in the backup folder after its scheduled time
  (`psfwidget run --config qc.toml --job backup` makes one now).

For the M4 exit criterion, leave the scheduled run going for 7 days and check
the logs for missed runs.

### 6.5 Restore drill

Check once (and after any change to the backup set-up) that a backup can
actually be restored. On a second PC, with the backup and, if reachable, the
live database:

```bash
psfwidget track restore-check \\server\qc\backups\sp8\qc_20261117T020000Z.sqlite --against C:\PSFWidget\data\qc.sqlite --out C:\PSFWidget\qualification --user "IT name"
```

It verifies the backup's integrity and SHA-256, restores a temporary copy
(the backup itself is not touched), counts sessions, approvals, and audit
entries, checks that everything in the backup is also in the live database,
draws Levey-Jennings charts from the copy, and writes
`restore_drill_<time>.json` with a sign-off section. Exit status 0 means
PASS.

To move results to a new PC, copy the latest verified backup there as the
new database, or merge it into an existing one with
`psfwidget track import --db qc.sqlite --from backup.sqlite`.

---

## 7. Upgrade

1. Back up the database: `psfwidget track backup --db qc.sqlite --out qc_before_upgrade.sqlite`
   (or run the `backup` job).
2. Activate the environment and reinstall, naming the new version:

   ```bash
   python -m pip install --upgrade "psfwidget[napari] @ git+https://github.com/CU-IBIF/PSFWidget.git@<new tag>"
   ```

3. Run `psfwidget selftest --out <qualification folder>` and file the record.
4. Open the database once (for example `psfwidget track list --db qc.sqlite`).
   Older databases are upgraded in place: columns and tables are added, no
   existing row is changed, and the upgrade is written to the audit log.

Every result records the software version that produced it. Read the
[CHANGELOG](../../CHANGELOG.md) for changes to how tracked metrics are
computed: such a change can shift a Levey-Jennings chart, and may need an
event with new control limits.

---

## 8. Troubleshooting the installation

| Problem | Cause and fix |
|---|---|
| `psfwidget: command not found` / not recognised | The virtual environment is not activated. Activate it (§3.1), or call it by full path, e.g. `C:\PSFWidget\venv\Scripts\psfwidget.exe`. |
| `ERROR: ... requires a different Python` | Python is older than 3.12. Install 3.12 or newer (§2) and recreate the environment. |
| `charts need matplotlib` or reports fail with `No module named 'reportlab'` | Install the `[report]` (or `[napari]`) extra. |
| napari: `Could not load the Qt platform plugin "xcb"` (Linux) | Install the Qt system libraries, e.g. on Ubuntu: `sudo apt install libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 libxcb-randr0 libxcb-render-util0 libxcb-image0 libegl1 libgl1-mesa-dri`. |
| napari opens but the canvas is black or it crashes on start (remote desktop, virtual machine) | No OpenGL. Update the graphics driver, or use a machine with a GPU; on Windows remote desktop, try `set QT_OPENGL=software` before `napari`. |
| Two Qt bindings installed (PyQt and PySide6) cause import errors | Use a fresh environment with only the `[napari]` extra (it installs PySide6; PyQt is excluded for licensing). |
| Dropping a file opens it as plain layers, not in *PSF analysis*, or napari asks which plugin to use | Another plugin (napari's TIFF reader, napari-nd2) also reads the file type. Choose **PSFWidget** in the dialog and tick *Remember*, or set it in *File → Preferences → Plugins → File extension readers*. |
| An error mentioning `_paint_loading` or `'NoneType' object has no attribute 'height'` | A napari layer-list problem fixed in PSFWidget 0.0.1.dev0 of 2026-11-20 (DEF-01). Upgrade PSFWidget (§7). If it still appears, send the full message and `napari --info`. |
| PSFWidget is missing from napari's *Plugins* menu | napari is running from another environment. Start `napari` from the environment where PSFWidget is installed. |
| `check-config` refuses the database path | It is on a network share. Put the database on the local disk (§6.1). |
| The self-test fails a check | Keep the JSON record and report it (it names the failing check). Do not use the installation for QC until it passes. |

---

## 9. Uninstall

Delete the virtual-environment folder (for example `C:\PSFWidget\venv`) and
the scheduled task (`schtasks /Delete /TN "PSFWidget QC"`, or remove the cron
line). The results database, reports, backups, profiles, and qualification
records are separate files: keep them according to the facility's retention
policy.
