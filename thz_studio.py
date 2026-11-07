import sys
import re
import csv
import json
from collections import deque
import time
from datetime import datetime
import os
import copy
# On GNOME Wayland, Qt 6 draws its own title bar, and minimize/maximize misbehave.
# Run through XWayland so GNOME draws the normal title bar.
# Set QT_QPA_PLATFORM=wayland before starting to use native Wayland.
if os.environ.get("XDG_SESSION_TYPE") == "wayland" and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "xcb"
import matplotlib
os.environ.setdefault('QT_API', 'pyside6')
matplotlib.use('QtAgg')
# Embed TrueType fonts in PDF/EPS so text stays editable (journals reject Type 3 fonts)
matplotlib.rcParams['pdf.fonttype'] = 42
matplotlib.rcParams['ps.fonttype'] = 42
import numpy as np
import pandas as pd
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QComboBox, QCheckBox, QScrollArea, QMessageBox,
    QListWidget, QListWidgetItem, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QColorDialog, QInputDialog, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QSpinBox, QPlainTextEdit, QSplitter, QFrame, QGridLayout
)
from PySide6.QtCore import Qt, QSettings
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.ticker import AutoMinorLocator, NullLocator
from matplotlib.figure import Figure
from matplotlib import font_manager
from matplotlib.text import Text
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit, least_squares, differential_evolution
from scipy.signal import savgol_filter, find_peaks


# ==================================================================
# FILE DIALOG HOOKS
# * Every Open/Save/Folder dialog starts in the last folder used, unless the
#   caller already passes its own folder.
# * DIALOG_HOOK["inject"]: a list of paths that the next Open dialog returns
#   without showing (used by drag and drop, Recent Files and sessions).
# * DIALOG_HOOK["opened"]: called with the paths picked in any Open dialog.
# ==================================================================
APP_SETTINGS = QSettings("thzstudio", "thzstudio")
DIALOG_HOOK = {"inject": None, "opened": None}


def _install_file_dialog_hooks():
    key = "paths/last_data_dir"

    def wrap(name, is_dir=False, multi=False, is_open=False):
        orig = getattr(QFileDialog, name)

        def dialog(*args, **kwargs):
            if is_open and DIALOG_HOOK["inject"] is not None:
                paths = DIALOG_HOOK["inject"]
                if paths and DIALOG_HOOK["opened"]:
                    try:
                        DIALOG_HOOK["opened"](list(paths) if multi else [paths[0]])
                    except Exception:
                        pass
                return (list(paths), "") if multi else ((paths[0] if paths else ""), "")
            args = list(args)
            kw = "dir" if "dir" in kwargs else ("directory" if "directory" in kwargs else None)
            start = kwargs[kw] if kw else (args[2] if len(args) > 2 else "")
            last = APP_SETTINGS.value(key, "") or ""
            if last and os.path.isdir(last):
                if not start:
                    start = last
                elif not os.path.isabs(start) and not os.path.dirname(start):
                    start = os.path.join(last, start)  # bare default file name
            if kw:
                kwargs[kw] = start
            else:
                while len(args) < 3:
                    args.append(None if len(args) == 0 else "")
                args[2] = start
            result = orig(*args, **kwargs)
            picked = result if is_dir else result[0]
            picked_list = list(picked) if isinstance(picked, (list, tuple)) else ([picked] if picked else [])
            if picked_list:
                folder = picked_list[0] if is_dir else os.path.dirname(picked_list[0])
                if os.path.isdir(folder):
                    APP_SETTINGS.setValue(key, folder)
                if is_open and DIALOG_HOOK["opened"]:
                    try:
                        DIALOG_HOOK["opened"](picked_list)
                    except Exception:
                        pass
            return result
        setattr(QFileDialog, name, staticmethod(dialog))

    wrap("getOpenFileName", is_open=True)
    wrap("getOpenFileNames", multi=True, is_open=True)
    wrap("getSaveFileName")
    wrap("getExistingDirectory", is_dir=True)


_install_file_dialog_hooks()


# ==================================================================
# SHORT MESSAGES IN THE STATUS BAR
# Short "information" messages (saved, exported, hints) go to the main
# window's status bar instead of a popup. Warnings and errors stay popups.
# ==================================================================
STATUS_SINK = {"show": None}
_orig_information = QMessageBox.information


def _status_information(parent, title, text, *args, **kwargs):
    text = str(text)
    if STATUS_SINK["show"] and not args and not kwargs and len(text) <= 300 and text.count("\n") <= 3:
        STATUS_SINK["show"](f"{title}: " + " ".join(text.split()))
        return QMessageBox.Ok
    return _orig_information(parent, title, text, *args, **kwargs)


QMessageBox.information = staticmethod(_status_information)


# ==================================================================
# COLLAPSIBLE SETTINGS SECTIONS, TOOLTIPS AND NUMBER CHECKS
# ==================================================================
SECTION_ORDER = ["Plot Options", "Labels & Fonts", "Axis Limits", "Ticks", "Legend", "Export"]
SECTION_OPEN_DEFAULT = {"Plot Options", "Labels & Fonts", "Axis Limits"}
SECTION_OF_LABEL = {
    "Font": "Labels & Fonts", "Plot Title": "Labels & Fonts", "X Label": "Labels & Fonts",
    "Y Label": "Labels & Fonts", "Font Size (Ticks)": "Labels & Fonts",
    "Axis Label Font Size": "Labels & Fonts", "Title Font Size": "Labels & Fonts",
    "Legend Font Size": "Labels & Fonts", "Line Width": "Labels & Fonts", "Spine Width": "Labels & Fonts",
    "X Axis Min": "Axis Limits", "X Axis Max": "Axis Limits",
    "Y Axis Min": "Axis Limits", "Y Axis Max": "Axis Limits",
    "Major Tick Length": "Ticks", "Minor Tick Length": "Ticks", "Major Tick Width": "Ticks",
    "Minor Tick Width": "Ticks", "Major Ticks X": "Ticks", "Minor Ticks X Between": "Ticks",
    "Major Ticks Y": "Ticks", "Minor Ticks Y Between": "Ticks",
    "Legend Location": "Legend", "Legend Order": "Legend",
    "Save Width (in)": "Export", "Save Height (in)": "Export", "Save DPI": "Export",
}
SECTION_OF_CHECKBOX = {
    "Bold Axis Labels": "Labels & Fonts", "Bold Title": "Labels & Fonts",
    "Bold Legend": "Labels & Fonts", "Bold Tick Labels": "Labels & Fonts",
    "Show Legend": "Legend", "Auto-Arrange Legends": "Legend",
}
TOOLTIPS = {
    "Font": "Font family for all text on the graph.",
    "Plot Title": "Title shown above the graph. Leave empty for no title.",
    "X Label": "X-axis label. Matplotlib math works, e.g. $\\mu$m.",
    "Y Label": "Y-axis label. Matplotlib math works, e.g. |S$_{11}$|.",
    "Font Size (Ticks)": "Tick label size in points.",
    "Axis Label Font Size": "Axis label size in points.",
    "Title Font Size": "Title size in points.",
    "Legend Font Size": "Legend text size in points.",
    "Line Width": "Data line width in points.",
    "Spine Width": "Width of the box around the graph, in points.",
    "X Axis Min": "Lower x limit. Leave Min and Max empty for automatic limits.",
    "X Axis Max": "Upper x limit. Leave Min and Max empty for automatic limits.",
    "Y Axis Min": "Lower y limit. Leave Min and Max empty for automatic limits.",
    "Y Axis Max": "Upper y limit. Leave Min and Max empty for automatic limits.",
    "Major Tick Length": "Length of major ticks in points.",
    "Minor Tick Length": "Length of minor ticks in points.",
    "Major Tick Width": "Line width of major ticks in points.",
    "Minor Tick Width": "Line width of minor ticks in points.",
    "Major Ticks X": "Number of labelled ticks on the x axis.",
    "Minor Ticks X Between": "Unlabelled ticks between two major x ticks (0 = none).",
    "Major Ticks Y": "Number of labelled ticks on the y axis.",
    "Minor Ticks Y Between": "Unlabelled ticks between two major y ticks (0 = none).",
    "Legend Location": "Where the legend sits; 'best' lets Matplotlib choose.",
    "Legend Order": "Drag entries to reorder; double-click to rename. Needs Auto-Arrange off.",
    "Save Width (in)": "Width of the saved image in inches (3.3 in = one journal column).",
    "Save Height (in)": "Height of the saved image in inches.",
    "Save DPI": "Resolution of saved PNG/JPEG images. 300-600 for journals.",
    "Bold Axis Labels": "Draw the axis labels in bold.",
    "Bold Title": "Draw the title in bold.",
    "Bold Legend": "Draw the legend text in bold.",
    "Bold Tick Labels": "Draw the tick numbers in bold.",
    "Show Legend": "Show or hide the legend.",
    "Auto-Arrange Legends": "Order legend entries automatically. Turn off to drag them by hand.",
}
NUMERIC_LABELS = {
    "Font Size (Ticks)", "Axis Label Font Size", "Title Font Size", "Legend Font Size",
    "Line Width", "Spine Width", "X Axis Min", "X Axis Max", "Y Axis Min", "Y Axis Max",
    "Major Tick Length", "Minor Tick Length", "Major Tick Width", "Minor Tick Width",
    "Save Width (in)", "Save Height (in)", "Save DPI", "Marker Size",
    "Data Marker Size (edge zoom)",
}
_BAD_NUMBER_STYLE = "QLineEdit { border: 2px solid #d03030; }"


def check_number_field(edit, label=""):
    """Outline the field in red while its text is not a number (empty is allowed)."""
    def check(text):
        bad = False
        if text.strip():
            try:
                float(text)
            except ValueError:
                bad = True
        edit.setStyleSheet(_BAD_NUMBER_STYLE if bad else "")
        tip = TOOLTIPS.get(label, "")
        edit.setToolTip(("Not a number. " + tip) if bad else tip)
    edit.textChanged.connect(check)
    check(edit.text())


class CollapsibleSection(QWidget):
    """A bold header that folds its grid of settings open and closed; the state is remembered."""

    def __init__(self, title, key, parent=None):
        super().__init__(parent)
        self.key = f"sections/{key}/{title}"
        expanded = APP_SETTINGS.value(self.key, title in SECTION_OPEN_DEFAULT, type=bool)
        self.button = QtWidgets.QToolButton()
        self.button.setText(title.replace("&", "&&"))
        self.button.setCheckable(True)
        self.button.setChecked(expanded)
        self.button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.button.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self.button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(10, 0, 0, 6)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.button)
        lay.addWidget(self.body)
        self.button.toggled.connect(self._set_open)
        self._set_open(expanded, store=False)

    def _set_open(self, expanded, store=True):
        self.body.setVisible(expanded)
        self.button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        if store:
            APP_SETTINGS.setValue(self.key, bool(expanded))


def build_settings_sections(rows, key):
    """Lay (label, widget) rows out in collapsible sections (Labels & Fonts, Ticks, ...).
    label None = full-width widget. Rows not in a known section go to 'Plot Options'."""
    buckets = {name: [] for name in SECTION_ORDER}
    for text, widget in rows:
        if text is None:
            name = SECTION_OF_CHECKBOX.get(getattr(widget, "text", lambda: "")(), "Plot Options")
        else:
            name = SECTION_OF_LABEL.get(text, SECTION_OF_LABEL.get(text.split(" (")[0], "Plot Options"))
        buckets[name].append((text, widget))
    outer = QVBoxLayout()
    outer.setSpacing(4)
    for name in SECTION_ORDER:
        if not buckets[name]:
            continue
        sec = CollapsibleSection(name, key)
        r = 0
        for text, widget in buckets[name]:
            tip = TOOLTIPS.get(text if text is not None else getattr(widget, "text", lambda: "")(),
                               TOOLTIPS.get((text or "").split(" (")[0], ""))
            if tip and not widget.toolTip():
                widget.setToolTip(tip)
            if isinstance(widget, QComboBox):  # long entries (font names) must not widen the panel
                widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                widget.setMinimumContentsLength(8)
            if text is None:
                sec.grid.addWidget(widget, r, 0, 1, 2)
            elif isinstance(widget, QListWidget):
                lbl = QLabel(text)
                lbl.setToolTip(tip)
                sec.grid.addWidget(lbl, r, 0, 1, 2)
                r += 1
                sec.grid.addWidget(widget, r, 0, 1, 2)
            else:
                lbl = QLabel(text)
                lbl.setToolTip(tip)
                sec.grid.addWidget(lbl, r, 0)
                sec.grid.addWidget(widget, r, 1)
                if isinstance(widget, QLineEdit) and text in NUMERIC_LABELS:
                    check_number_field(widget, text)
            r += 1
        outer.addWidget(sec)
    outer.addStretch(1)  # keep the sections stacked at the top in tall windows
    return outer

# Marker options
MARKER_OPTIONS = [
    ('None', 'None'),
    ('Circle', 'o'),
    ('Square', 's'),
    ('Triangle Up', '^'),
    ('Triangle Down', 'v'),
    ('Diamond', 'D'),
    ('Star', '*'),
    ('Plus', '+'),
    ('X', 'x')
]
MARKER_NAME_TO_SYMBOL = dict(MARKER_OPTIONS)

# Color options
COLOR_NAMES = [
    'blue', 'green', 'red', 'cyan', 'magenta', 'yellow', 'black',
    'orange', 'purple', 'brown', 'pink', 'gray'
]

# Line style options
LINE_STYLE_OPTIONS = [
    ('Solid', '-'),
    ('Dashed', '--'),
    ('Dotted', ':'),
    ('DashDot', '-.')
]
LINE_STYLE_NAME_TO_SYMBOL = dict(LINE_STYLE_OPTIONS)

# Legend location options
LEGEND_LOCATION_OPTIONS = [
    "best",
    "upper right",
    "upper left",
    "lower left",
    "lower right",
    "right",
    "center left",
    "center right",
    "lower center",
    "upper center",
    "center",
    "upper right (outside)",
    "upper left (outside)",
    "lower right (outside)",
    "lower left (outside)",
    "center right (outside)",
    "center left (outside)",
]
OUTSIDE_LEGEND_MAP = {
    "upper right (outside)":  {"loc": "upper left",  "bbox_to_anchor": (1.02, 1)},
    "upper left (outside)":   {"loc": "upper right", "bbox_to_anchor": (-0.02, 1)},
    "lower right (outside)":  {"loc": "lower left",  "bbox_to_anchor": (1.02, 0)},
    "lower left (outside)":   {"loc": "lower right", "bbox_to_anchor": (-0.02, 0)},
    "center right (outside)": {"loc": "center left", "bbox_to_anchor": (1.02, 0.5)},
    "center left (outside)":  {"loc": "center right","bbox_to_anchor": (-0.02, 0.5)},
}

# Fonts matplotlib can find; Times New Roman by default, else a look-alike
AVAILABLE_FONTS = sorted({f.name for f in font_manager.fontManager.ttflist})
DEFAULT_FONT = next((f for f in ("Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif")
                     if f in AVAILABLE_FONTS), "DejaVu Sans")
matplotlib.rcParams['font.family'] = DEFAULT_FONT
matplotlib.rcParams['mathtext.fontset'] = 'stix'  # Times-like math text


def make_font_combo():
    combo = QComboBox()
    combo.addItems(AVAILABLE_FONTS)
    combo.setCurrentText(DEFAULT_FONT)
    return combo


def apply_font(fig, family):
    """Set the font of every text in fig (labels, title, ticks, legend, annotations).
    New tick labels copy their style from the first tick, so they follow too."""
    for text in fig.findobj(Text):
        text.set_fontfamily(family)


# Save formats: (file dialog filter, default extension)
SAVE_FORMATS = [
    ("PNG image (*.png)", ".png"),
    ("JPEG image (*.jpg *.jpeg)", ".jpg"),
    ("PDF vector (*.pdf)", ".pdf"),
    ("SVG vector (*.svg)", ".svg"),
    ("EPS vector (*.eps)", ".eps"),
    ("TIFF image (*.tif *.tiff)", ".tif"),
]
SAVE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".svg", ".eps", ".tif", ".tiff"}

# Peak selection / extinction ratio options
ALL_CURVES_TEXT = "(Nearest curve / all curves)"
# T_off is the reference curve (0 V by default), T_on is the curve at voltage V
ER_ON_OFF = "T_on / T_off   (T(V) / T_ref)"
ER_OFF_ON = "T_off / T_on   (T_ref / T(V))"
ER_DEFINITIONS = [ER_ON_OFF, ER_OFF_ON]
ER_LINEAR = "Linear ratio"
ER_DB_POWER = "dB, power: 10·log10"
ER_DB_AMPLITUDE = "dB, amplitude: 20·log10"
ER_SCALES = [ER_DB_AMPLITUDE, ER_DB_POWER, ER_LINEAR]
# Quantity plotted against voltage. IL = -20·log10(T_on) (or -10·log10 for power data)
QTY_ER = "Extinction Ratio (ER)"
QTY_IL = "Insertion Loss (IL)"
QTY_BOTH = "ER (left) + IL (right)"
QTY_DEFAULT_LABELS = {QTY_ER: "Extinction Ratio", QTY_IL: "Insertion Loss",
                      QTY_BOTH: "Extinction Ratio"}


def guess_voltage(label):
    """Pull a voltage like '10V', '-2.5 V' out of a legend label, or return ''."""
    m = re.search(r'(-?\d+(?:\.\d+)?)\s*[Vv](?![A-Za-z])', label)
    return m.group(1) if m else ""

# ---- Period averaging and edge fits ----

LN9 = np.log(9.0)      # 10%->90% of a logistic spans +/- ln(9)/k about t0


def load_period_csv(path):
    """Return (t, y) with t = relative time [s] (starts at 0), y = amplitude.

    Skips any leading metadata block and finds the row that begins with
    'TIME' as the column header."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        lines = fh.readlines()

    header_idx = None
    for i, ln in enumerate(lines):
        if ln.strip().upper().startswith("TIME"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"No 'TIME' header row found in {path}")

    ncols = len(lines[header_idx].split(","))
    data = np.genfromtxt(path, delimiter=",", skip_header=header_idx + 1,
                         usecols=range(ncols), invalid_raise=False)
    data = np.atleast_2d(data)
    data = data[np.all(np.isfinite(data[:, :2]), axis=1)]  # need TIME + C1

    y = data[:, 1]                                   # C1 amplitude
    if data.shape[1] >= 3 and np.isfinite(data[:, 2]).all():
        t = data[:, 2]                               # relative time column
    else:
        t = data[:, 0]                               # absolute time -> rebase

    order = np.argsort(t)
    t, y = t[order], y[order]
    t = t - t[0]
    return t, y


def estimate_frequency(t, y, fmin=1.0, fmax=None):
    """Estimate the fundamental frequency [Hz] via a windowed FFT with
    parabolic peak interpolation."""
    n = len(y)
    dt = np.median(np.diff(t))
    fs = 1.0 / dt
    if fmax is None:
        fmax = fs / 2.0

    x = (y - y.mean()) * np.hanning(n)
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(n, dt)

    band = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(band):
        raise ValueError("No FFT bins in the requested frequency band.")
    idx_band = np.flatnonzero(band)
    k = idx_band[np.argmax(spec[band])]

    # parabolic interpolation around the peak bin for sub-bin accuracy
    if 0 < k < len(spec) - 1:
        a, b, c = spec[k - 1], spec[k], spec[k + 1]
        denom = (a - 2 * b + c)
        delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
    else:
        delta = 0.0
    df = freqs[1] - freqs[0]
    f0 = (k + delta) * df
    return f0, fs


def _moving_average(x, w):
    if w < 2:
        return x
    k = np.ones(w) / w
    return np.convolve(x, k, mode="same")


def refine_period(t, y, f0, tol=0.05):
    """Refine the period around the FFT estimate f0.

    Primary method: detect rising mid-level crossings of a lightly smoothed
    copy of the signal (works for square, pulse and sine shapes) and fit a
    straight line to crossing_time vs crossing_index; the slope is the period.
    Falls back to a narrow phase-continuity scan if too few clean crossings
    are found or the result disagrees with f0 by more than `tol`."""
    dt = np.median(np.diff(t))
    fs = 1.0 / dt
    approx_period = 1.0 / f0

    # smooth over ~1/25 of a period to beat sample noise before edge finding
    w = max(3, int(round(approx_period * fs / 25)))
    ys = _moving_average(y - np.median(y), w)

    lo, hi = np.percentile(ys, 5), np.percentile(ys, 95)
    amp = hi - lo
    f_cross = f0
    if amp > 0:
        level = 0.5 * (lo + hi)
        margin = 0.15 * amp                       # hysteresis
        above = ys > level
        armed = True                              # ready to accept a rising edge
        crossings = []
        for i in range(1, len(ys)):
            if armed and not above[i - 1] and above[i]:
                # sub-sample crossing time by linear interpolation
                frac = (level - ys[i - 1]) / (ys[i] - ys[i - 1])
                crossings.append(t[i - 1] + frac * (t[i] - t[i - 1]))
                armed = False
            elif not armed and ys[i] < level - margin:
                armed = True
        crossings = np.asarray(crossings)
        # keep only crossings that are ~1 period apart (drop doubles/misses)
        if len(crossings) >= 3:
            d = np.diff(crossings)
            good = np.abs(d - np.median(d)) < 0.25 * np.median(d)
            if good.sum() >= 2:
                idx = np.arange(len(crossings))
                keep = np.concatenate([[True], good])
                slope = np.polyfit(idx[keep], crossings[keep], 1)[0]
                f_cross = 1.0 / slope

    if abs(f_cross - f0) / f0 <= tol:
        return f_cross

    # fallback: narrow phase-continuity scan (+/- tol) around the FFT estimate
    y0 = y - y.mean()
    fs_scan = np.linspace(f0 * (1 - tol), f0 * (1 + tol), 4001)
    mag = np.abs(np.exp(-2j * np.pi * np.outer(fs_scan, t)) @ y0)
    return fs_scan[np.argmax(mag)]


def average_over_periods(t, y, period):
    """Split the record into whole periods and average them.

    One period is M = round(period * fs) consecutive samples. The record is
    cut into n = len(y) // M back-to-back blocks of M samples and the blocks
    are averaged point-by-point. No interpolation, no detrending, no
    filtering is applied to the amplitude values.

    Returns (tau, avg, std, segments, n_periods) where tau is the within-
    period time axis and segments is (n_periods, M) of the raw samples."""
    dt = np.median(np.diff(t))
    fs = 1.0 / dt
    m = int(round(period * fs))
    if m < 2:
        raise ValueError("Period is shorter than 2 samples.")
    n_periods = len(y) // m
    if n_periods < 1:
        raise ValueError(
            f"Record ({(t[-1]-t[0])*1e6:.3f} us) shorter than one period "
            f"({period*1e6:.3f} us)."
        )

    segments = y[:n_periods * m].reshape(n_periods, m)
    avg = segments.mean(axis=0)          # plain arithmetic mean
    std = segments.std(axis=0)
    tau = np.arange(m) * dt
    return tau, avg, std, segments, n_periods


# --------------------------------------------------------------------------
# sigmoid edge fitting  ->  10%-90% rise / fall time
# --------------------------------------------------------------------------

def logistic(t, y0, amp, t0, k):
    """4-parameter logistic. amp>0 = rising edge, amp<0 = falling edge.
    k>0 always (transition steepness, 1/s)."""
    z = np.clip(-k * (t - t0), -500.0, 500.0)
    return y0 + amp / (1.0 + np.exp(z))


def _fit_one_edge(tt, yy, t_cross, rising):
    """Fit a logistic to one edge given a data window (tt, yy) and an
    approximate mid-level crossing time. Returns a dict of results."""
    nend = max(3, len(yy) // 6)
    y_left = np.median(yy[:nend])
    y_right = np.median(yy[-nend:])
    y0_0 = y_left
    amp_0 = y_right - y_left
    if amp_0 == 0:
        amp_0 = (1.0 if rising else -1.0) * (yy.max() - yy.min())

    # initial steepness from the 10/90 levels of the rough amplitude
    lo_lvl = y0_0 + 0.1 * amp_0
    hi_lvl = y0_0 + 0.9 * amp_0
    def _first_time(level, up):
        s = (yy >= level) if up else (yy <= level)
        j = np.argmax(s)
        return tt[j] if s.any() else t_cross
    t10 = _first_time(lo_lvl, amp_0 > 0)
    t90 = _first_time(hi_lvl, amp_0 > 0)
    span = abs(t90 - t10) or (tt[-1] - tt[0]) / 10.0
    k_0 = 2.0 * LN9 / span

    p2p = float(yy.max() - yy.min())
    wspan = float(tt[-1] - tt[0])
    p0 = [y0_0, amp_0, t_cross, k_0]
    bounds = ([yy.min() - p2p, -3 * p2p, tt[0], 2 * LN9 / (20 * wspan)],
              [yy.max() + p2p, 3 * p2p, tt[-1], np.inf])
    p0 = [min(max(v, lo), hi) for v, lo, hi in zip(p0, *bounds)]
    popt, _ = curve_fit(logistic, tt, yy, p0=p0, bounds=bounds, maxfev=20000)
    y0, amp, t0, k = popt
    k = abs(k)

    resid = yy - logistic(tt, *popt)
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((yy - yy.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    t_10_90 = 2.0 * LN9 / k
    if not np.isfinite(r2) or r2 < 0.4:
        raise ValueError(f"{'rise' if rising else 'fall'} fit poor (R^2={r2:.2f})"
                         " - signal may not have a flat-topped edge")
    if t_10_90 > 0.8 * wspan:
        raise ValueError(f"{'rise' if rising else 'fall'} 'edge' spans the whole "
                         "window - not a step; check --freq / signal shape")

    t_10 = t0 - LN9 / k          # time at 10% of the transition
    t_90 = t0 + LN9 / k          # time at 90% of the transition
    t_20 = t0 - np.log(4.0) / k
    t_80 = t0 + np.log(4.0) / k
    return dict(
        kind="rise" if rising else "fall",
        y0=y0, amp=amp, t0=t0, k=k, r2=r2,
        level_low=y0 if amp > 0 else y0 + amp,
        level_high=y0 + amp if amp > 0 else y0,
        v10=y0 + 0.1 * amp, v90=y0 + 0.9 * amp,
        t10=t_10, t90=t_90, t20=t_20, t80=t_80,
        time_10_90=2.0 * LN9 / k,
        time_20_80=2.0 * np.log(4.0) / k,
        window=(tt[0], tt[-1]),
    )


def fit_edges(tau, avg, period, edge_frac=0.45):
    """Locate the one rising and one falling edge of the averaged period and
    fit a logistic to each. Returns (rise_dict, fall_dict).

    Edges are located on a smoothed copy (steepest +/- slope), but each
    logistic is fitted to the raw averaged samples in the window."""
    m = len(avg)
    dt = tau[1] - tau[0]

    # doubled record so a window never runs off the ends
    avg2 = np.tile(avg, 2)
    tau2 = np.arange(2 * m) * dt

    # smooth over ~1/60 of a period, only to locate the edges
    w = max(3, int(round(m / 60)))
    ys = _moving_average(avg2 - avg2.mean(), w)
    d = np.gradient(ys)

    # steepest rise inside the first copy, then steepest fall after it
    r_idx = int(np.argmax(d[:m])) + m          # work in the 2nd copy
    fall_search = d[r_idx + w: r_idx + m]
    f_idx = r_idx + w + int(np.argmin(fall_search))

    t_r = r_idx * dt
    t_f = f_idx * dt
    w_high = t_f - t_r
    w_low = period - w_high
    half = edge_frac * min(w_low, w_high)

    def _window(t_center):
        lo = np.searchsorted(tau2, t_center - half)
        hi = np.searchsorted(tau2, t_center + half)
        return tau2[lo:hi], avg2[lo:hi]

    tt_r, yy_r = _window(t_r)
    tt_f, yy_f = _window(t_f)
    if len(tt_r) < 8 or len(tt_f) < 8:
        raise ValueError("edge window too small - check the period estimate")

    rise = _fit_one_edge(tt_r, yy_r, t_r, rising=True)
    fall = _fit_one_edge(tt_f, yy_f, t_f, rising=False)
    for edge, tt, yy in ((rise, tt_r, yy_r), (fall, tt_f, yy_f)):
        # shift everything back into the single-period frame [0, period)
        edge["t0_mod"] = edge["t0"] % period
        edge["t0_local"] = edge["t0"] - m * dt
        edge["t10_local"] = edge["t10"] - m * dt
        edge["t90_local"] = edge["t90"] - m * dt
        edge["half"] = half
        edge["tt_local"] = tt - m * dt
        edge["yy"] = yy
    return rise, fall


def analyze_period_capture(path, freq=None, fmin=1e3, fmax=None, refine=True,
                           fit=True, edge_frac=0.45, use_abs=False):
    """Full analysis of one capture: load, find the period, average
    whole periods, roll to start on the low plateau, fit both edges.
    use_abs analyses |C1|, so rise/fall refer to the magnitude."""
    t, y = load_period_csv(path)
    if use_abs:
        y = np.abs(y)
    dt = np.median(np.diff(t))
    fs = 1.0 / dt
    f0 = freq if freq else estimate_frequency(t, y, fmin=fmin, fmax=fmax)[0]
    f_fft = f0
    if refine:
        f0 = refine_period(t, y, f0)
    period = 1.0 / f0
    tau, avg, std, segments, n_periods = average_over_periods(t, y, period)

    # circular time shift only; amplitudes are untouched
    if fit:
        m = len(avg)
        ds = np.gradient(_moving_average(avg - avg.mean(), max(3, m // 60)))
        shift = (int(np.argmax(ds)) - int(round(0.15 * m))) % m
        avg = np.roll(avg, -shift)
        std = np.roll(std, -shift)
        segments = np.roll(segments, -shift, axis=1)

    rise = fall = None
    fit_error = ""
    if fit:
        try:
            rise, fall = fit_edges(tau, avg, period, edge_frac=edge_frac)
        except Exception as exc:
            fit_error = str(exc)
    return dict(path=path, name=os.path.basename(path), t=t, y=y, fs=fs,
                f_fft=f_fft, f0=f0, period=period, tau=tau, avg=avg, std=std,
                segments=segments, n_periods=n_periods, rise=rise, fall=fall,
                fit_error=fit_error, use_abs=use_abs)


def pa_report_text(res):
    """Text report of both edge fits (saved as <name>_edge_fit.txt)."""
    out = [f"file: {res['name']}",
           f"frequency_Hz: {res['f0']:.4f}",
           f"period_us: {res['period'] * 1e6:.6f}",
           f"n_periods_averaged: {res['n_periods']}"]
    if res.get('use_abs'):
        out.append("signal: |C1| (absolute value)")
    rise, fall = res['rise'], res['fall']
    if rise is None:
        out.append(f"\nedge fit: {res['fit_error'] or 'off'}")
        return "\n".join(out) + "\n"
    lvl_lo = 0.5 * (rise['level_low'] + fall['level_low'])
    lvl_hi = 0.5 * (rise['level_high'] + fall['level_high'])
    out += [f"level_low: {lvl_lo:.6f}", f"level_high: {lvl_hi:.6f}",
            f"amplitude: {lvl_hi - lvl_lo:.6f}", ""]
    for d in (rise, fall):
        out += [f"[{d['kind']}]",
                f"  edge_time_us:   {d['t0_mod'] * 1e6:.6f}",
                f"  rise_fall_10_90_ns: {d['time_10_90'] * 1e9:.4f}",
                f"  rise_fall_20_80_ns: {d['time_20_80'] * 1e9:.4f}",
                f"  logistic_tau_ns (1/k): {1e9 / d['k']:.4f}",
                f"  y0: {d['y0']:.6f}",
                f"  amp: {d['amp']:.6f}",
                f"  k_per_s: {d['k']:.6g}",
                f"  R2: {d['r2']:.6f}", ""]
    return "\n".join(out)


PA_VIEW_AVG = "Averaged period + edge fits"
PA_VIEW_RISE = "Rise edge (zoom)"
PA_VIEW_FALL = "Fall edge (zoom)"
PA_VIEW_RECORD = "Full record + period boundaries"
PA_VIEW_OVERLAY = "All periods overlaid"
PA_VIEW_COMPARE = "Rise / fall time vs frequency (all files)"

# Raw photocurrent -> transmission tab options
RT_VIEW_T = "Transmission"
RT_VIEW_PC = "Photocurrents + envelopes"
RT_VIEW_ENV = "Envelopes (upper / lower)"
RT_MODE_AVG = "Average sample files (one output)"
RT_MODE_EACH = "One output per sample file"

FILE_SETTING = "(File setting)"
PICK_COLOR = "Pick custom..."

# Saved plot styles: controls left out (they list or act on loaded data), the folder
# of named styles, and the file holding the name of the style loaded at startup
SETTINGS_SKIP = {"peak_curve_combo", "pick_peak_mode", "er_reference", "style_combos",
                 "rt_result_combo"}
SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".config", "thzstudio")
STYLES_DIR = os.path.join(SETTINGS_DIR, "styles")
DEFAULT_STYLE_FILE = os.path.join(SETTINGS_DIR, "default_style.txt")
STYLE_PLACEHOLDER = "(choose a saved style)"

# Speed / 3 dB cutoff tab options
FC_VIEW_RESPONSE = "Frequency response (Vpp vs f)"
FC_VIEW_SINE = "Sine fit of selected capture"
FC_Y_VPP = "Vpp [V]"
FC_Y_GAIN = "Gain [dB] (20·log10 Vpp/V0)"
FC_Y_NORM = "Normalized Vpp / V0"
FC_V0_FIT = "Fit V0 and fc"
FC_V0_LOWEST = "V0 = lowest-frequency point"
FC_FREQ_UNITS = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}


def read_photocurrent(path):
    """Two-column photocurrent scan (frequency, current) after any text header lines,
    returned in increasing frequency."""
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.replace(',', ' ').split()
            try:
                rows.append((float(parts[0]), float(parts[1])))
            except (ValueError, IndexError):
                continue
    if len(rows) < 2:
        raise ValueError("no numeric data found")
    a = np.array(rows)
    x, y = a[:, 0], a[:, 1]
    if x[0] > x[-1]:
        x, y = x[::-1], y[::-1]
    return x, y


def average_scans(paths):
    """Mean current of several scans; frequencies from the first. Longer scans are cut
    to the shortest one."""
    scans = [read_photocurrent(p) for p in paths]
    n = min(len(x) for x, _ in scans)
    return scans[0][0][:n], np.mean([y[:n] for _, y in scans], axis=0)


def movmean(x, before, after):
    """Moving mean with a window of [before, after] points; the window shrinks at the ends."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    i = np.arange(len(x))
    lo = np.maximum(i - before, 0)
    hi = np.minimum(i + after + 1, len(x))
    return (c[hi] - c[lo]) / (hi - lo)


def movmean_k(x, k):
    """Moving mean over k points: an even k looks k/2 back and k/2-1 ahead."""
    return movmean(x, k // 2, k // 2 - 1) if k % 2 == 0 else movmean(x, k // 2, k // 2)


def envelope_rms(y, n):
    """RMS envelope: moving RMS over n points of the mean-removed signal (an even n looks
    n/2-1 back and n/2 ahead), with the mean added back."""
    mean = y.mean()
    yc = y - mean
    before, after = (n // 2 - 1, n // 2) if n % 2 == 0 else (n // 2, n // 2)
    ampl = np.sqrt(movmean(yc * yc, before, after))
    return mean + ampl, mean - ampl


def raw_transmission(ref, samp, env_window=30, mean_window=500):
    """Transmission = sample upper envelope / reference upper envelope (both RMS envelopes)."""
    (xr, yr), (xs, ys) = ref, samp
    ref_hi, ref_lo = envelope_rms(yr, env_window)
    samp_hi, samp_lo = envelope_rms(ys, env_window)
    n = min(len(xr), len(xs))
    T = samp_hi[:n] / ref_hi[:n]
    return {
        'xr': xr, 'yr': yr, 'ref_hi': ref_hi, 'ref_lo': ref_lo,
        'xs': xs, 'ys': ys, 'samp_hi': samp_hi, 'samp_lo': samp_lo,
        'x': (xr[:n] + xs[:n]) / 2, 'T': T, 'T_mean': movmean_k(T, mean_window),
    }


def read_scope_csv(path):
    """Read an oscilloscope capture: a metadata block, then a 'TIME,<channel>'
    header row, then samples. Returns (t, v) from the first two columns."""
    with open(path, 'r', errors='replace') as f:
        lines = f.readlines()
    header_idx = next((i for i, line in enumerate(lines)
                       if line.strip().upper().startswith("TIME")), None)
    if header_idx is None:
        raise ValueError("No 'TIME' header row found.")
    header = lines[header_idx]
    sep = "," if "," in header else ("\t" if "\t" in header else None)
    rows = []
    for line in lines[header_idx + 1:]:
        parts = line.strip().split(sep)
        if len(parts) < 2:
            continue
        try:
            rows.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if len(rows) < 10:
        raise ValueError("Too few numeric samples after the TIME header.")
    data = np.array(rows)
    return data[:, 0], data[:, 1]


def read_freq_vpp_csv(path):
    """Read a two-column frequency [Hz], Vpp [V] table; text/header lines are skipped."""
    rows = []
    with open(path, 'r', errors='replace') as f:
        for line in f:
            parts = re.split(r'[,;\t ]+', line.strip())
            if len(parts) < 2:
                continue
            try:
                rows.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    if not rows:
        raise ValueError("No numeric frequency / Vpp rows found.")
    return rows


def sine_model(t, A, f, phi, offset):
    return A * np.sin(2 * np.pi * f * t + phi) + offset


def fit_sine(t, v, n_periods):
    """Fit v(t) = A·sin(2πf(t − t0) + φ) + offset over the first n_periods periods.
    Frequency and phase start from the FFT peak. Returns a dict with Vpp, f and errors."""
    dt = np.mean(np.diff(t))
    spec = np.fft.rfft(v - np.mean(v))
    freqs = np.fft.rfftfreq(len(t), d=dt)
    k = np.argmax(np.abs(spec[1:])) + 1  # skip the DC bin
    f0 = freqs[k]
    t0 = t[0]
    mask = (t - t0) <= n_periods / f0
    if mask.sum() < 10:
        mask = np.ones_like(t, dtype=bool)
    tf, vf = t[mask] - t0, v[mask]
    # FFT phase is for a cosine; sin(x + π/2) = cos(x)
    p0 = [(vf.max() - vf.min()) / 2, f0, np.angle(spec[k]) + np.pi / 2, np.mean(vf)]
    popt, pcov = curve_fit(sine_model, tf, vf, p0=p0, maxfev=10000)
    A, f, phi, offset = popt
    if A < 0:
        A, phi = -A, phi + np.pi
    perr = np.sqrt(np.abs(np.diag(pcov)))
    return {'A': A, 'f': abs(f), 'phi': phi, 'offset': offset,
            'vpp': 2 * A, 'vpp_err': 2 * perr[0], 'f_err': perr[1],
            't0': t0, 't': tf, 'v': vf, 'n_used': int(mask.sum()), 'n_total': len(t)}


def fit_lowpass(f, vpp, v0_fixed=None):
    """Fit the single-pole low-pass response Vpp(f) = V0 / sqrt(1 + (f/fc)²).
    Residuals are in dB so each decade of frequency counts equally.
    With v0_fixed, only fc is fitted. Returns V0, fc, their 1σ errors and RMS residual [dB]."""
    f = np.asarray(f, float)
    vpp = np.asarray(vpp, float)
    below = np.nonzero(vpp < vpp[np.argmin(f)] / np.sqrt(2))[0]
    fc_guess = f[below].min() if below.size else 2 * f.max()

    def model(v0, log_fc):
        return v0 / np.sqrt(1 + (f / 10 ** log_fc) ** 2)

    if v0_fixed is None:
        p0 = [vpp[np.argmin(f)], np.log10(fc_guess)]
        res = least_squares(lambda p: 20 * np.log10(model(p[0], p[1]) / vpp), p0,
                            bounds=([1e-15, -6], [np.inf, 15]))
        v0, log_fc = res.x
    else:
        res = least_squares(lambda p: 20 * np.log10(model(v0_fixed, p[0]) / vpp),
                            [np.log10(fc_guess)], bounds=([-6], [15]))
        v0, log_fc = v0_fixed, res.x[0]

    npar = len(res.x)
    sigma = np.full(npar, np.nan)
    if len(f) > npar:
        J = res.jac
        cov = np.linalg.pinv(J.T @ J) * np.sum(res.fun ** 2) / (len(f) - npar)
        sigma = np.sqrt(np.abs(np.diag(cov)))
    fc = 10 ** log_fc
    return {'v0': v0, 'fc': fc,
            'v0_err': sigma[0] if v0_fixed is None else 0.0,
            'fc_err': fc * np.log(10) * sigma[-1],
            'rms_db': np.sqrt(np.mean(res.fun ** 2))}


def interpolate_3db(f, vpp, v0):
    """First frequency where 20·log10(Vpp/V0) falls to −3 dB, interpolated
    linearly in log f between the two data points around it. None if never reached."""
    order = np.argsort(f)
    f = np.asarray(f, float)[order]
    gain = 20 * np.log10(np.asarray(vpp, float)[order] / v0)
    idx = np.nonzero(gain <= -3)[0]
    if not idx.size or idx[0] == 0:
        return None
    i = idx[0]
    log_f = np.interp(-3, [gain[i], gain[i - 1]], [np.log10(f[i]), np.log10(f[i - 1])])
    return 10 ** log_f


def time_unit_for(period):
    """(unit name, scale) that keeps a few periods of a sine in readable numbers."""
    for name, scale in (("ns", 1e-9), ("µs", 1e-6), ("ms", 1e-3)):
        if period < 1000 * scale:
            return name, scale
    return "s", 1.0


class YColumnStyle(QGroupBox):
    """Title and line style for one Y column. '(File setting)' / blank fields fall back
    to the file's settings (color, line style, marker, marker size) or the global line width."""

    def __init__(self, col_index, title, on_change):
        super().__init__(f"Y Col {col_index}")
        self.on_change = on_change
        self.title = QLineEdit(title)
        self.color = QComboBox()
        self.color.addItems([FILE_SETTING] + COLOR_NAMES + [PICK_COLOR])
        self.line_style = QComboBox()
        self.line_style.addItems([FILE_SETTING] + [name for name, _ in LINE_STYLE_OPTIONS] + ["None"])
        self.marker = QComboBox()
        self.marker.addItems([FILE_SETTING] + [name for name, _ in MARKER_OPTIONS])
        self.marker_size = QLineEdit("")
        self.marker_size.setPlaceholderText("file setting")
        self.line_width = QLineEdit("")
        self.line_width.setPlaceholderText("global setting")

        grid = QtWidgets.QGridLayout(self)
        for r, (text, widget) in enumerate([
                ("Title:", self.title), ("Color:", self.color), ("Line Style:", self.line_style),
                ("Marker:", self.marker), ("Marker Size:", self.marker_size),
                ("Line Width:", self.line_width)]):
            grid.addWidget(QLabel(text), r, 0)
            grid.addWidget(widget, r, 1)

        self.color.currentIndexChanged.connect(self.on_color_changed)
        for combo in (self.line_style, self.marker):
            combo.currentIndexChanged.connect(on_change)
        for edit in (self.title, self.marker_size, self.line_width):
            edit.textChanged.connect(on_change)

    def on_color_changed(self):
        if self.color.currentText() == PICK_COLOR:
            chosen = QColorDialog.getColor(parent=self, title="Line Color")
            self.color.blockSignals(True)
            if chosen.isValid():
                # Keep picked colors in the list (just above "Pick custom...")
                name = chosen.name()
                if self.color.findText(name) < 0:
                    self.color.insertItem(self.color.count() - 1, name)
                    self.color.setItemData(self.color.findText(name), QtGui.QColor(name),
                                           QtCore.Qt.DecorationRole)
                self.color.setCurrentText(name)
            else:
                self.color.setCurrentIndex(0)
            self.color.blockSignals(False)
        self.on_change()

    def get(self):
        """Overrides only; None means use the file or global setting."""
        def num(edit):
            try:
                return float(edit.text())
            except ValueError:
                return None
        color = self.color.currentText()
        ls = self.line_style.currentText()
        marker = self.marker.currentText()
        return {
            'color': None if color == FILE_SETTING else color,
            'line_style': None if ls == FILE_SETTING else ('None' if ls == "None" else LINE_STYLE_NAME_TO_SYMBOL[ls]),
            'marker': None if marker == FILE_SETTING else MARKER_NAME_TO_SYMBOL[marker],
            'marker_size': num(self.marker_size),
            'line_width': num(self.line_width),
        }


class FileEntry(QWidget):
    def __init__(self, filename, index, on_change_callback=None, on_remove_callback=None):
        super().__init__()
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)
        self.filename = filename
        self.uid = index  # stable id; peaks and voltages are keyed by it
        self.on_change_callback = on_change_callback

        # Header: select box (for "Remove Selected") and a remove button
        self.select_box = QCheckBox(filename.split('/')[-1])
        self.select_box.setStyleSheet("font-weight: bold;")
        self.select_box.setToolTip(filename)
        btn_remove = QPushButton("✕")
        btn_remove.setFixedWidth(30)
        btn_remove.setStyleSheet("color: #c00; font-weight: bold;")
        btn_remove.setToolTip("Remove this file from the plot")
        if on_remove_callback is not None:
            btn_remove.clicked.connect(lambda: on_remove_callback(self))
        header_row = QHBoxLayout()
        header_row.addWidget(btn_remove)
        header_row.addWidget(self.select_box, 1)
        self.layout.addLayout(header_row)

        self.legend_label = QLineEdit(filename.split('/')[-1].rsplit('.', 1)[0])
        self.x_col = QLineEdit("0")
        self.y_col_widget = QListWidget()
        self.y_col_widget.setSelectionMode(QListWidget.MultiSelection)
        self.y_col_widget.setFixedHeight(80)
        self.x_scale = QLineEdit("1.0")
        self.y_square = QCheckBox("Square Y")
        self.marker = QComboBox()
        self.marker.addItems([name for name, _ in MARKER_OPTIONS])
        self.marker.setCurrentIndex(0)
        self.marker_size = QLineEdit("8")
        self.line_style = QComboBox()
        self.line_style.addItems([name for name, _ in LINE_STYLE_OPTIONS])
        self.line_style.setCurrentIndex(0)
        self.color_combo = QComboBox()
        self.color_combo.addItem("(Auto)")
        for name in COLOR_NAMES:
            self.color_combo.addItem(name)

        self.grid = QtWidgets.QGridLayout()
        self.grid.addWidget(QLabel("Legend:"), 0, 0)
        self.grid.addWidget(self.legend_label, 0, 1)
        self.grid.addWidget(QLabel("X Column:"), 1, 0)
        self.grid.addWidget(self.x_col, 1, 1)
        self.grid.addWidget(QLabel("Y Columns:"), 2, 0)
        self.grid.addWidget(self.y_col_widget, 2, 1)
        self.grid.addWidget(QLabel("X Scale:"), 3, 0)
        self.grid.addWidget(self.x_scale, 3, 1)
        self.grid.addWidget(self.y_square, 4, 0, 1, 2)
        self.grid.addWidget(QLabel("Marker:"), 5, 0)
        self.grid.addWidget(self.marker, 5, 1)
        self.grid.addWidget(QLabel("Marker Size:"), 6, 0)
        self.grid.addWidget(self.marker_size, 6, 1)
        self.grid.addWidget(QLabel("Line Style:"), 7, 0)
        self.grid.addWidget(self.line_style, 7, 1)
        self.grid.addWidget(QLabel("Line Color:"), 8, 0)
        self.grid.addWidget(QLabel("<i>Above: all columns.<br>Below: per column.</i>"), 9, 0, 1, 2)
        self.grid.addWidget(self.color_combo, 8, 1)

        # Per-Y-column style boxes, filled in by set_columns
        self.col_styles = {}

        self.layout.addLayout(self.grid)
        self._headers = None

        # Connect controls to callback if provided
        if self.on_change_callback is not None:
            self.legend_label.textChanged.connect(self.on_change_callback)
            self.x_col.textChanged.connect(self.on_change_callback)
            self.x_scale.textChanged.connect(self.on_change_callback)
            self.marker.currentIndexChanged.connect(self.on_change_callback)
            self.marker_size.textChanged.connect(self.on_change_callback)
            self.line_style.currentIndexChanged.connect(self.on_change_callback)
            self.color_combo.currentIndexChanged.connect(self.on_change_callback)
            self.y_square.stateChanged.connect(self.on_change_callback)
            self.y_col_widget.itemSelectionChanged.connect(self.on_change_callback)
        self.y_col_widget.itemSelectionChanged.connect(self.update_col_style_visibility)

    def set_columns(self, headers):
        # Clear previous Y column list and per-column style boxes
        self.y_col_widget.clear()
        for box in self.col_styles.values():
            self.layout.removeWidget(box)
            box.deleteLater()
        self.col_styles = {}

        self._headers = headers.copy()

        for i in range(1, len(headers)):
            display = f"{i}: {headers[i]}"
            item = QListWidgetItem(display)
            self.y_col_widget.addItem(item)
            if i == 1:
                item.setSelected(True)

        # One style box per Y column; only the selected columns' boxes are shown
        for i in range(1, len(headers)):
            box = YColumnStyle(i, headers[i], self.on_col_style_changed)
            self.layout.addWidget(box)
            self.col_styles[i] = box
        self.update_col_style_visibility()

    def selected_y_cols(self):
        # Column order, not the order the items were clicked
        return sorted(int(item.text().split(':')[0]) for item in self.y_col_widget.selectedItems())

    def update_col_style_visibility(self):
        selected = set(self.selected_y_cols())
        for i, box in self.col_styles.items():
            box.setVisible(i in selected)

    def on_col_style_changed(self):
        # Update internal headers with edited titles
        for i, box in self.col_styles.items():
            self._headers[i] = box.title.text()
        if self.on_change_callback:
            self.on_change_callback()

    def get(self):
        y_cols = self.selected_y_cols()
        color_selected = self.color_combo.currentText()
        if color_selected == "(Auto)":
            color_selected = None
        return {
            'legend': self.legend_label.text(),
            'x_col': int(self.x_col.text()),
            'y_cols': y_cols,
            'x_scale': float(self.x_scale.text()),
            'y_square': self.y_square.isChecked(),
            'marker': MARKER_NAME_TO_SYMBOL.get(self.marker.currentText(), 'o'),
            'marker_size': float(self.marker_size.text()),
            'line_style': LINE_STYLE_NAME_TO_SYMBOL.get(self.line_style.currentText(), '-'),
            'headers': self._headers,
            'color': color_selected,
            'col_styles': {i: box.get() for i, box in self.col_styles.items()},
        }

class StylePanel:
    """The standard plot-formatting controls (fonts, labels, limits, ticks, bold,
    legend, save size) used by the other tabs, plus a method that applies them."""

    def __init__(self, app, xlabel="", ylabel=""):
        self.app = app
        self.font_family = make_font_combo()
        self.title = QLineEdit("")
        self.xlabel = QLineEdit(xlabel)
        self.ylabel = QLineEdit(ylabel)
        self.font_size = QLineEdit("24")
        self.axis_label_font_size = QLineEdit("24")
        self.title_font_size = QLineEdit("24")
        self.legend_font_size = QLineEdit("22")
        self.line_width = QLineEdit("3")
        self.spine_width = QLineEdit("4")
        self.xmin = QLineEdit("")
        self.xmax = QLineEdit("")
        self.ymin = QLineEdit("")
        self.ymax = QLineEdit("")
        self.major_tick_length = QLineEdit("6")
        self.minor_tick_length = QLineEdit("3")
        self.major_tick_width = QLineEdit("2")
        self.minor_tick_width = QLineEdit("1")
        self.num_major_ticks_x = QComboBox()
        self.num_major_ticks_x.addItems(["Auto"] + [str(i) for i in range(2, 11)])
        self.num_minor_ticks_x = QComboBox()
        self.num_minor_ticks_x.addItems([str(i) for i in range(0, 6)])
        self.num_minor_ticks_x.setCurrentText("4")
        self.num_major_ticks_y = QComboBox()
        self.num_major_ticks_y.addItems(["Auto"] + [str(i) for i in range(2, 11)])
        self.num_minor_ticks_y = QComboBox()
        self.num_minor_ticks_y.addItems([str(i) for i in range(0, 6)])
        self.num_minor_ticks_y.setCurrentText("4")
        self.bold_axis_labels = QCheckBox("Bold Axis Labels")
        self.bold_axis_labels.setChecked(True)
        self.bold_title = QCheckBox("Bold Title")
        self.bold_title.setChecked(True)
        self.bold_legend = QCheckBox("Bold Legend")
        self.bold_legend.setChecked(True)
        self.bold_ticks = QCheckBox("Bold Tick Labels")
        self.bold_ticks.setChecked(True)
        self.show_legend = QCheckBox("Show Legend")
        self.show_legend.setChecked(True)
        self.legend_location = QComboBox()
        self.legend_location.addItems(LEGEND_LOCATION_OPTIONS)
        self.save_width = QLineEdit("10")
        self.save_height = QLineEdit("6")
        self.save_dpi = QLineEdit("300")

    def rows(self):
        """(label, widget) rows for a settings grid; label None = full-width widget."""
        return [
            ("Font", self.font_family),
            ("Plot Title", self.title),
            ("X Label", self.xlabel),
            ("Y Label", self.ylabel),
            ("Font Size (Ticks)", self.font_size),
            ("Axis Label Font Size", self.axis_label_font_size),
            ("Title Font Size", self.title_font_size),
            ("Legend Font Size", self.legend_font_size),
            ("Line Width", self.line_width),
            ("Spine Width", self.spine_width),
            ("X Axis Min", self.xmin),
            ("X Axis Max", self.xmax),
            ("Y Axis Min", self.ymin),
            ("Y Axis Max", self.ymax),
            ("Major Tick Length", self.major_tick_length),
            ("Minor Tick Length", self.minor_tick_length),
            ("Major Tick Width", self.major_tick_width),
            ("Minor Tick Width", self.minor_tick_width),
            ("Major Ticks X", self.num_major_ticks_x),
            ("Minor Ticks X Between", self.num_minor_ticks_x),
            ("Major Ticks Y", self.num_major_ticks_y),
            ("Minor Ticks Y Between", self.num_minor_ticks_y),
            (None, self.bold_axis_labels),
            (None, self.bold_title),
            (None, self.bold_legend),
            (None, self.bold_ticks),
            (None, self.show_legend),
            ("Legend Location", self.legend_location),
            ("Save Width (in)", self.save_width),
            ("Save Height (in)", self.save_height),
            ("Save DPI", self.save_dpi),
        ]

    def copy_targets(self):
        """Widgets in the order _copy_transmission_style expects."""
        return [self.font_size, self.axis_label_font_size, self.line_width,
                self.spine_width, self.major_tick_length, self.minor_tick_length,
                self.major_tick_width, self.minor_tick_width,
                self.num_major_ticks_x, self.num_minor_ticks_x,
                self.num_major_ticks_y, self.num_minor_ticks_y,
                self.bold_axis_labels, self.bold_legend, self.bold_ticks,
                self.legend_location, self.font_family,
                self.save_width, self.save_height, self.save_dpi]

    def apply(self, fig, ax, lines):
        """Limits, ticks, labels, title, legend, tick/spine style and font."""
        num = self.app._num
        if self.xmin.text().strip() and self.xmax.text().strip():
            ax.set_xlim(num(self.xmin, None), num(self.xmax, None))
        if self.ymin.text().strip() and self.ymax.text().strip():
            ax.set_ylim(num(self.ymin, None), num(self.ymax, None))
        try:
            self.app._set_er_ticks(ax, 'x', self.num_major_ticks_x, self.num_minor_ticks_x)
            self.app._set_er_ticks(ax, 'y', self.num_major_ticks_y, self.num_minor_ticks_y)
        except Exception:
            pass

        label_weight = 'bold' if self.bold_axis_labels.isChecked() else 'normal'
        label_size = num(self.axis_label_font_size, 24, int)
        ax.set_xlabel(self.xlabel.text(), fontsize=label_size, fontweight=label_weight)
        ax.set_ylabel(self.ylabel.text(), fontsize=label_size, fontweight=label_weight)
        if self.title.text().strip():
            ax.set_title(self.title.text(), fontsize=num(self.title_font_size, 24, int),
                         fontweight='bold' if self.bold_title.isChecked() else 'normal')

        if lines and self.show_legend.isChecked():
            legend_size = num(self.legend_font_size, 22, int)
            loc_str = self.legend_location.currentText()
            labels = [l.get_label() for l in lines]
            if loc_str in OUTSIDE_LEGEND_MAP:
                params = OUTSIDE_LEGEND_MAP[loc_str]
                leg = ax.legend(lines, labels, fontsize=legend_size, frameon=False,
                                loc=params["loc"], bbox_to_anchor=params["bbox_to_anchor"],
                                borderaxespad=0.)
            else:
                leg = ax.legend(lines, labels, fontsize=legend_size, frameon=False, loc=loc_str)
            legend_weight = 'bold' if self.bold_legend.isChecked() else 'normal'
            for text in leg.get_texts():
                text.set_fontweight(legend_weight)

        tick_weight = 'bold' if self.bold_ticks.isChecked() else 'normal'
        ax.tick_params(axis='both', which='major', direction='in',
                       length=num(self.major_tick_length, 6),
                       width=num(self.major_tick_width, 2),
                       labelsize=num(self.font_size, 24, int))
        ax.tick_params(axis='both', which='minor', direction='in',
                       length=num(self.minor_tick_length, 3),
                       width=num(self.minor_tick_width, 1))
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_fontweight(tick_weight)
        for spine in ax.spines.values():
            spine.set_linewidth(num(self.spine_width, 4))

        apply_font(fig, self.font_family.currentText())
        try:
            fig.tight_layout()
        except Exception:
            pass


# =====================================================================
# C-V / I-V / RC plotting tabs
# =====================================================================
# The C-V/I-V tabs also offer "None" as a line style
CV_LINE_STYLE_OPTIONS = LINE_STYLE_OPTIONS + [("None", "None")]
CV_LINE_STYLE_NAME_TO_SYMBOL = dict(CV_LINE_STYLE_OPTIONS)


def _to_float(line_edit, default):
    try:
        return float(line_edit.text())
    except Exception:
        return default


def _to_int(line_edit, default):
    try:
        return int(float(line_edit.text()))
    except Exception:
        return default


def _combo_int(combo, default):
    """Like _to_int but for a QComboBox holding integer-text items."""
    try:
        return int(combo.currentText())
    except Exception:
        return default


class CVPlotStyleGroup(QGroupBox):
    """
    Plot formatting controls: fonts, tick styling, spine width, axis limits
    with major/minor tick counts, legend placement and export size.
    """

    def __init__(self, xlabel_default, ylabel_default, plot_title_default="", title="Plot Style"):
        super().__init__(title)

        self.show_title = QCheckBox("Show graph title")
        self.show_title.setChecked(bool(plot_title_default))
        self.plot_title = QLineEdit(plot_title_default)
        self.title_font_size = QLineEdit("13")

        self.xlabel = QLineEdit(xlabel_default)
        self.ylabel = QLineEdit(ylabel_default)
        self._ylabel_user_set = False
        self.ylabel.textEdited.connect(lambda: setattr(self, "_ylabel_user_set", True))

        self.plot_font_size = QLineEdit("11")        # tick label font size
        self.axis_label_font_size = QLineEdit("12")
        self.line_width = QLineEdit("1.8")
        self.marker_size = QLineEdit("4")
        self.spine_width = QLineEdit("1.2")
        self.major_tick_length = QLineEdit("6")
        self.major_tick_width = QLineEdit("1.2")
        self.minor_tick_length = QLineEdit("3")
        self.minor_tick_width = QLineEdit("0.8")

        self.num_major_ticks_x = QComboBox()
        self.num_major_ticks_x.addItems(["Auto"] + [str(i) for i in range(2, 11)])
        self.num_minor_ticks_x = QComboBox()
        self.num_minor_ticks_x.addItems([str(i) for i in range(0, 6)])

        self.num_major_ticks_y = QComboBox()
        self.num_major_ticks_y.addItems(["Auto"] + [str(i) for i in range(2, 11)])
        self.num_minor_ticks_y = QComboBox()
        self.num_minor_ticks_y.addItems([str(i) for i in range(0, 6)])

        self.use_custom_ylim = QCheckBox("Use custom Y limits")
        self.y_min = QLineEdit("")
        self.y_max = QLineEdit("")

        self.bold_axis_labels = QCheckBox("Bold axis labels")
        self.bold_axis_labels.setChecked(True)
        self.bold_ticks = QCheckBox("Bold tick labels")
        self.bold_legend = QCheckBox("Bold legend")
        self.show_grid = QCheckBox("Show grid")
        self.show_grid.setChecked(True)

        self.legend_location = QComboBox()
        self.legend_location.addItems(LEGEND_LOCATION_OPTIONS)

        self.save_width = QLineEdit("8")
        self.save_height = QLineEdit("6")
        self.save_dpi = QLineEdit("300")

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.addRow(self.show_title)
        form.addRow("Title:", self.plot_title)
        form.addRow("Title font size:", self.title_font_size)
        form.addRow("X label:", self.xlabel)
        form.addRow("Y label:", self.ylabel)
        form.addRow("Tick font size:", self.plot_font_size)
        form.addRow("Axis label font size:", self.axis_label_font_size)
        form.addRow("Line width:", self.line_width)
        form.addRow("Marker size:", self.marker_size)
        form.addRow("Spine width:", self.spine_width)
        form.addRow("Major tick length:", self.major_tick_length)
        form.addRow("Major tick width:", self.major_tick_width)
        form.addRow("Minor tick length:", self.minor_tick_length)
        form.addRow("Minor tick width:", self.minor_tick_width)
        form.addRow("Major ticks (X):", self.num_major_ticks_x)
        form.addRow("Minor ticks between (X):", self.num_minor_ticks_x)
        form.addRow("Major ticks (Y):", self.num_major_ticks_y)
        form.addRow("Minor ticks between (Y):", self.num_minor_ticks_y)
        form.addRow(self.use_custom_ylim)
        form.addRow("Y min:", self.y_min)
        form.addRow("Y max:", self.y_max)
        form.addRow(self.bold_axis_labels)
        form.addRow(self.bold_ticks)
        form.addRow(self.bold_legend)
        form.addRow(self.show_grid)
        form.addRow("Legend location:", self.legend_location)
        form.addRow("Save width (in):", self.save_width)
        form.addRow("Save height (in):", self.save_height)
        form.addRow("Save DPI:", self.save_dpi)

        self.setLayout(form)

        for widget in (
            self.plot_title, self.title_font_size,
            self.xlabel, self.ylabel, self.plot_font_size, self.axis_label_font_size,
            self.line_width, self.marker_size, self.spine_width,
            self.major_tick_length, self.major_tick_width,
            self.minor_tick_length, self.minor_tick_width,
            self.num_major_ticks_x, self.num_minor_ticks_x,
            self.num_major_ticks_y, self.num_minor_ticks_y,
            self.y_min, self.y_max, self.legend_location,
            self.save_width, self.save_height, self.save_dpi,
        ):
            widget.setMinimumWidth(180)

        self._all_controls = [
            self.show_title, self.plot_title, self.title_font_size,
            self.xlabel, self.ylabel, self.plot_font_size, self.axis_label_font_size,
            self.line_width, self.marker_size, self.spine_width,
            self.major_tick_length, self.major_tick_width,
            self.minor_tick_length, self.minor_tick_width,
            self.num_major_ticks_x, self.num_minor_ticks_x,
            self.num_major_ticks_y, self.num_minor_ticks_y,
            self.use_custom_ylim, self.y_min, self.y_max,
            self.bold_axis_labels, self.bold_ticks, self.bold_legend,
            self.show_grid, self.legend_location,
        ]

    def set_default_ylabel(self, text):
        """Update the Y label unless the user has typed their own."""
        if not self._ylabel_user_set:
            self.ylabel.blockSignals(True)
            self.ylabel.setText(text)
            self.ylabel.blockSignals(False)

    def connect_all(self, slot):
        for widget in self._all_controls:
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(slot)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(slot)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(slot)

    # -------------------------------------------------------
    # Apply all styling to a matplotlib Axes (same rules as the
    # Transmission tab).
    # -------------------------------------------------------
    def apply_to_axes(self, ax, xmin, xmax):
        label_weight = "bold" if self.bold_axis_labels.isChecked() else "normal"
        axis_label_fs = _to_float(self.axis_label_font_size, 12)

        if self.show_title.isChecked():
            title_fs = _to_float(self.title_font_size, 13)
            ax.set_title(self.plot_title.text(), fontsize=title_fs, fontweight=label_weight)
        else:
            ax.set_title("")

        ax.set_xlabel(self.xlabel.text(), fontsize=axis_label_fs, fontweight=label_weight)
        ax.set_ylabel(self.ylabel.text(), fontsize=axis_label_fs, fontweight=label_weight)

        ax.set_xlim(xmin, xmax)
        if self.use_custom_ylim.isChecked():
            try:
                ax.set_ylim(float(self.y_min.text()), float(self.y_max.text()))
            except Exception:
                pass
        ymin, ymax = ax.get_ylim()

        # X ticks: major positions are either automatic or an explicit
        # count; minor ticks use AutoMinorLocator so they show correctly
        # in both cases (this is independent of the major-tick setting).
        try:
            if self.num_major_ticks_x.currentText() != "Auto":
                n_major = int(self.num_major_ticks_x.currentText())
                ax.set_xticks(np.linspace(xmin, xmax, n_major))
        except Exception:
            pass

        n_minor_x = _combo_int(self.num_minor_ticks_x, 0)
        if n_minor_x > 0:
            ax.xaxis.set_minor_locator(AutoMinorLocator(n_minor_x + 1))
        else:
            ax.xaxis.set_minor_locator(NullLocator())

        # Y ticks (skipped on log scale, where matplotlib's own locator is better)
        if ax.get_yscale() == "linear":
            try:
                if self.num_major_ticks_y.currentText() != "Auto":
                    n_major = int(self.num_major_ticks_y.currentText())
                    ax.set_yticks(np.linspace(ymin, ymax, n_major))
            except Exception:
                pass

            n_minor_y = _combo_int(self.num_minor_ticks_y, 0)
            if n_minor_y > 0:
                ax.yaxis.set_minor_locator(AutoMinorLocator(n_minor_y + 1))
            else:
                ax.yaxis.set_minor_locator(NullLocator())

        # Tick styling
        major_len = _to_float(self.major_tick_length, 6)
        major_w = _to_float(self.major_tick_width, 1.2)
        minor_len = _to_float(self.minor_tick_length, 3)
        minor_w = _to_float(self.minor_tick_width, 0.8)
        tick_fs = _to_float(self.plot_font_size, 11)

        ax.tick_params(axis="both", which="major", direction="in",
                        length=major_len, width=major_w, labelsize=tick_fs,
                        top=True, right=True, bottom=True, left=True)
        ax.tick_params(axis="both", which="minor", direction="in",
                        length=minor_len, width=minor_w,
                        top=True, right=True, bottom=True, left=True)

        tick_weight = "bold" if self.bold_ticks.isChecked() else "normal"
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_fontweight(tick_weight)

        # Spines
        spine_w = _to_float(self.spine_width, 1.2)
        for spine in ax.spines.values():
            spine.set_linewidth(spine_w)

        # Grid
        # Grid: matplotlib turns the grid back on if style kwargs are
        # passed together with grid(False), so keep the calls separate.
        if self.show_grid.isChecked():
            ax.grid(True, alpha=0.3, which="both")
        else:
            ax.grid(False, which="both")

        self.apply_legend(ax)

    def apply_legend(self, ax, handles=None, labels=None):
        """Draw the legend on ax (defaults to ax's own handles)."""
        if handles is None:
            handles, labels = ax.get_legend_handles_labels()
        tick_fs = _to_float(self.plot_font_size, 11)
        if handles:
            loc_str = self.legend_location.currentText()
            legend_fs = max(tick_fs - 2, 6)
            if loc_str in OUTSIDE_LEGEND_MAP:
                params = OUTSIDE_LEGEND_MAP[loc_str]
                leg = ax.legend(handles, labels, fontsize=legend_fs, frameon=False,
                                 loc=params["loc"], bbox_to_anchor=params["bbox_to_anchor"],
                                 borderaxespad=0.)
            else:
                leg = ax.legend(handles, labels, fontsize=legend_fs, frameon=False, loc=loc_str)
            legend_weight = "bold" if self.bold_legend.isChecked() else "normal"
            for text in leg.get_texts():
                text.set_fontweight(legend_weight)

    def save_png(self, figure, parent, default_name="plot.png", initial_dir=""):
        """Returns the saved file path on success, or None if cancelled/failed."""
        start_path = os.path.join(initial_dir, default_name) if initial_dir else default_name
        path, _ = QFileDialog.getSaveFileName(parent, "Save Plot as PNG", start_path, "PNG Files (*.png)")
        if not path:
            return None
        if not path.lower().endswith(".png"):
            path += ".png"

        orig_size = figure.get_size_inches().copy()
        try:
            width = _to_float(self.save_width, 8)
            height = _to_float(self.save_height, 6)
            dpi = _to_int(self.save_dpi, 300)
            figure.set_size_inches(width, height)
            figure.savefig(path, dpi=dpi, bbox_inches="tight")
            QMessageBox.information(parent, "Saved", f"Plot saved to:\n{path}")
            return path
        except Exception as e:
            QMessageBox.critical(parent, "Save Error", f"Could not save image: {e}")
            return None
        finally:
            figure.set_size_inches(orig_size)
            figure.canvas.draw()


class SheetPickerDialog(QDialog):
    """Checkable list of an Excel file's sheets; each checked sheet becomes a line."""

    def __init__(self, file_path, sheets, checked=(), already_plotted=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose sheets to plot")
        self.resize(380, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Sheets in {os.path.basename(file_path)}:"))

        self.list = QListWidget()
        for s in sheets:
            text = f"{s}   (already plotted)" if s in already_plotted else s
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, s)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if s in checked else Qt.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list)

        sel_row = QHBoxLayout()
        all_btn = QPushButton("Select all")
        none_btn = QPushButton("Select none")
        all_btn.clicked.connect(lambda: self._set_all(Qt.Checked))
        none_btn.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        sel_row.addWidget(all_btn)
        sel_row.addWidget(none_btn)
        layout.addLayout(sel_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, state):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def selected_sheets(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class CVDataset:
    """One plotted curve: a file (and sheet) plus its column/unit/style choices."""

    def __init__(self, file_path, sheets, sheet, df, sheet_cache):
        self.file_path = file_path
        self.sheets = sheets            # list of sheet names ([] for CSV)
        self.sheet = sheet              # current sheet name (None for CSV)
        self.df = df
        self.sheet_cache = sheet_cache  # sheet name -> DataFrame
        self.x_col = None
        self.y_col = None
        self.y_unit = None
        self.label = ""
        self.label_auto = True          # False once the user types a label
        self.marker = "Circle"
        self.linestyle = "Solid"
        self.color = "(Auto)"
        self.visible = True
        self.x = np.array([])
        self.y = np.array([])
        # Used by the RC tab only
        self.r_col = None
        self.r_unit = None
        self.r_is_g = False             # r_col holds a conductance (R = 1/G)
        self.sweeps = []                # per sweep: (V, V_R, R in Ohm, C in F)
        self.r_src = None               # None = R from this sheet, else a CVDataset for another file
        self.r = np.array([])           # resistance (Ohm) at each x
        self.c = np.array([])           # capacitance (F) at each x


class CVPlotTab(QWidget):
    """
    A generic X-Y plotting tab: load a data file, pick X/Y columns,
    optionally rescale the Y column's units, restrict to an X range,
    and plot with publication-style formatting controls. No curve fitting.
    """

    def __init__(
        self,
        y_quantity_name,        # e.g. "Capacitance" or "Current"
        y_units,                # e.g. ["F", "pF", "nF", "uF"]
        y_unit_to_base_factor,  # dict: unit -> multiplier to base unit
        base_unit,              # e.g. "pF" or "A"
        default_y_unit,
        x_keywords,              # keywords used to guess the X (voltage) column
        y_keywords,               # keywords used to guess the Y column
        x_fallback_index,
        y_fallback_index,
        default_title,
        allow_log_y=True,
        parent=None
    ):
        super().__init__(parent)

        self.y_quantity_name = y_quantity_name
        self.y_units = y_units
        self.y_unit_to_base_factor = y_unit_to_base_factor
        self.base_unit = base_unit
        self.default_y_unit = default_y_unit
        self.x_keywords = x_keywords
        self.y_keywords = y_keywords
        self.x_fallback_index = x_fallback_index
        self.y_fallback_index = y_fallback_index
        self.default_title = default_title
        self.allow_log_y = allow_log_y
        self.file_stem = y_quantity_name.lower()   # default export file names

        self.datasets = []
        self.current = None     # the CVDataset shown in the editor
        self._hover_lines = []
        self._hover_annotation = None

        # Last-used load/save folders, shared by the C-V, I-V and RC tabs
        self.settings = QSettings("thzstudio", "cv_iv")

        # Create plotting objects before building widgets, since some
        # widget signals may call update_plot during UI initialization.
        self.figure = Figure(figsize=(8, 6))
        self.canvas = FigureCanvas(self.figure)
        self.canvas.mpl_connect("motion_notify_event", self.on_hover)

        self._build_ui()

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------
    def _build_ui(self):
        main_layout = QHBoxLayout(self)

        # ---------------- LEFT PANEL: data controls (scrollable) ----------------
        left_container = QWidget()
        left = QVBoxLayout(left_container)

        # CVDataset list: each checked entry is drawn as its own line
        ds_group = QGroupBox("Datasets (check to show)")
        ds_layout = QVBoxLayout(ds_group)

        self.dataset_list = QListWidget()
        self.dataset_list.setMinimumHeight(120)
        self.dataset_list.currentRowChanged.connect(self.on_dataset_selected)
        self.dataset_list.itemChanged.connect(self.on_dataset_item_changed)
        ds_layout.addWidget(self.dataset_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Excel / CSV...")
        add_btn.clicked.connect(self.load_file)
        sheets_btn = QPushButton("Add sheets...")
        sheets_btn.setToolTip("Pick more sheets from the selected dataset's Excel file")
        sheets_btn.clicked.connect(self.add_sheets)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(sheets_btn)
        ds_layout.addLayout(btn_row)

        btn_row2 = QHBoxLayout()
        dup_btn = QPushButton("Duplicate")
        dup_btn.setToolTip("Copy the selected dataset; switches to the next sheet if the file has one")
        dup_btn.clicked.connect(self.duplicate_dataset)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self.remove_dataset)
        btn_row2.addWidget(dup_btn)
        btn_row2.addWidget(remove_btn)
        ds_layout.addLayout(btn_row2)

        left.addWidget(ds_group)

        self.file_label = QLabel("No data file loaded")
        self.file_label.setWordWrap(True)
        left.addWidget(self.file_label)

        # Selected dataset: sheet, label, columns
        self.col_group = QGroupBox("Selected dataset")
        col_form = QFormLayout(self.col_group)
        col_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        col_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.sheet_combo = QComboBox()
        self.sheet_combo.setMinimumWidth(180)
        self.sheet_combo.currentIndexChanged.connect(self.on_sheet_changed)
        col_form.addRow("Sheet:", self.sheet_combo)

        self.label_edit = QLineEdit()
        self.label_edit.setMinimumWidth(180)
        self.label_edit.textEdited.connect(self.on_label_edited)
        col_form.addRow("Legend label:", self.label_edit)

        self.x_col_combo = QComboBox()
        self.y_col_combo = QComboBox()
        self.x_col_combo.setMinimumWidth(180)
        self.y_col_combo.setMinimumWidth(180)

        self.x_col_combo.currentIndexChanged.connect(self.on_columns_changed)
        self.y_col_combo.currentIndexChanged.connect(self.on_columns_changed)

        col_form.addRow("Voltage column (X):", self.x_col_combo)
        col_form.addRow(f"{self.y_quantity_name} column (Y):", self.y_col_combo)

        self.y_unit_combo = QComboBox()
        self.y_unit_combo.setMinimumWidth(180)
        self.y_unit_combo.addItems(self.y_units)
        self.y_unit_combo.setCurrentText(self.default_y_unit)
        self.y_unit_combo.currentIndexChanged.connect(self.on_columns_changed)
        col_form.addRow(f"{self.y_quantity_name} unit (raw data):", self.y_unit_combo)

        left.addWidget(self.col_group)

        # X range
        range_group = QGroupBox("Voltage plot range")
        range_form = QFormLayout(range_group)
        range_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        range_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.xmin_spin = QDoubleSpinBox()
        self.xmax_spin = QDoubleSpinBox()

        for s in (self.xmin_spin, self.xmax_spin):
            s.setMinimumWidth(180)
            s.setRange(-1e6, 1e6)
            s.setDecimals(4)
            s.setSingleStep(0.1)
            s.valueChanged.connect(self.update_plot)

        self.xmin_spin.setValue(-15.0)
        self.xmax_spin.setValue(15.0)

        range_form.addRow("Minimum V:", self.xmin_spin)
        range_form.addRow("Maximum V:", self.xmax_spin)

        left.addWidget(range_group)

        # Display options
        self.options_group = QGroupBox("Display options (selected dataset)")
        options_group = self.options_group
        options_layout = QVBoxLayout(options_group)

        self.marker_combo = QComboBox()
        self.marker_combo.setMinimumWidth(180)
        self.marker_combo.addItems([name for name, _ in MARKER_OPTIONS])
        self.marker_combo.setCurrentText("Circle")
        self.marker_combo.currentIndexChanged.connect(self.on_style_changed)
        options_layout.addWidget(QLabel("Marker style:"))
        options_layout.addWidget(self.marker_combo)

        self.linestyle_combo = QComboBox()
        self.linestyle_combo.setMinimumWidth(180)
        self.linestyle_combo.addItems([name for name, _ in CV_LINE_STYLE_OPTIONS])
        self.linestyle_combo.setCurrentText("Solid")
        self.linestyle_combo.currentIndexChanged.connect(self.on_style_changed)
        options_layout.addWidget(QLabel("Line style:"))
        options_layout.addWidget(self.linestyle_combo)

        self.color_combo = QComboBox()
        self.color_combo.setMinimumWidth(180)
        self.color_combo.addItems(["(Auto)"] + COLOR_NAMES)
        self.color_combo.setCurrentText("(Auto)")
        self.color_combo.currentIndexChanged.connect(self.on_style_changed)
        options_layout.addWidget(QLabel("Color:"))
        options_layout.addWidget(self.color_combo)

        if self.allow_log_y:
            self.log_y = QCheckBox(f"Log scale on |{self.y_quantity_name}|")
            self.log_y.setChecked(False)
            self.log_y.stateChanged.connect(self.update_plot)
            options_layout.addWidget(self.log_y)
        else:
            self.log_y = None

        left.addWidget(options_group)

        self.info_label = QLabel("Selected points: --")
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left.addWidget(self.info_label)

        left.addStretch()

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_container)
        left_scroll.setMinimumWidth(200)
        left_scroll.setMaximumWidth(600)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # ---------------- CENTER PANEL: plot ----------------
        center = QVBoxLayout()
        center.addWidget(self.canvas)

        # ---------------- RIGHT PANEL: plot style (scrollable) ----------------
        right_container = QWidget()
        right = QVBoxLayout(right_container)

        # Plot style
        self.style = CVPlotStyleGroup(
            xlabel_default="Bias voltage (V)",
            ylabel_default=f"{self.y_quantity_name} ({self.base_unit})",
            plot_title_default=self.default_title,
        )
        self.style.connect_all(self.update_plot)
        right.addWidget(self.style)

        # Buttons
        export_btn = QPushButton("Export Plotted Data (CSV)")
        export_btn.clicked.connect(self.export_csv)
        right.addWidget(export_btn)

        save_png_btn = QPushButton("Save Plot as PNG")
        save_png_btn.clicked.connect(self.save_plot_as_png)
        right.addWidget(save_png_btn)

        self.right_panel = right
        right.addStretch()

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(right_container)
        right_scroll.setMinimumWidth(200)
        right_scroll.setMaximumWidth(600)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        main_layout.addWidget(left_scroll, 0)
        main_layout.addLayout(center, 1)
        main_layout.addWidget(right_scroll, 0)

        self._set_editor_enabled(False)

    def _set_editor_enabled(self, enabled):
        self.col_group.setEnabled(enabled)
        self.options_group.setEnabled(enabled)

    # ---------------------------------------------------------
    # Column auto-detection
    # ---------------------------------------------------------
    def _guess_column_index(self, cols, keywords, fallback_index):
        lower_cols = [str(c).lower() for c in cols]
        for kw in keywords:
            kw = kw.lower()
            for i, c in enumerate(lower_cols):
                if kw in c:
                    return i
        return fallback_index if fallback_index < len(cols) else 0

    # ---------------------------------------------------------
    # File reading
    # ---------------------------------------------------------
    @staticmethod
    def _str_columns(df):
        df = df.copy()
        df.columns = [str(c) for c in df.columns]
        return df

    @staticmethod
    def _read_csv(path):
        try:
            df = pd.read_csv(path)
        except Exception:
            df = pd.read_csv(path, sep=None, engine="python")
        if len(df.columns) == 1:
            df = pd.read_csv(path, sep=None, engine="python")
        return df

    def _read_sheet(self, ds, sheet):
        if sheet not in ds.sheet_cache:
            ds.sheet_cache[sheet] = self._str_columns(
                pd.read_excel(ds.file_path, sheet_name=sheet)
            )
        return ds.sheet_cache[sheet]

    def _open_file(self, path):
        """Read an Excel/CSV file into a CVDataset (first sheet), no columns chosen."""
        ext = path.lower().rsplit(".", 1)[-1]
        if ext in ("xlsx", "xls"):
            with pd.ExcelFile(path) as xl:
                sheets = [str(s) for s in xl.sheet_names]
                df = self._str_columns(xl.parse(xl.sheet_names[0]))
            return CVDataset(path, sheets, sheets[0], df, {sheets[0]: df})
        df = self._str_columns(self._read_csv(path))
        return CVDataset(path, [], None, df, {})

    def _make_dataset(self, path):
        ds = self._open_file(path)
        ds.y_unit = self.default_y_unit
        self._auto_columns(ds, keep=False)
        ds.label = self._auto_label(ds)
        self._compute(ds)
        return ds

    def _auto_columns(self, ds, keep=True):
        """Pick X/Y columns; keep the previous names if the new sheet has them."""
        cols = list(ds.df.columns)
        if not cols:
            ds.x_col = ds.y_col = None
            return
        if not (keep and ds.x_col in cols):
            ds.x_col = cols[self._guess_column_index(cols, self.x_keywords, self.x_fallback_index)]
        if not (keep and ds.y_col in cols):
            ds.y_col = cols[self._guess_column_index(cols, self.y_keywords, self.y_fallback_index)]

    @staticmethod
    def _auto_label(ds):
        name = os.path.splitext(os.path.basename(ds.file_path))[0]
        if len(ds.sheets) > 1:
            name += f" [{ds.sheet}]"
        return name

    def _compute(self, ds):
        """Parse the dataset's selected columns into numeric arrays (base units)."""
        try:
            x = pd.to_numeric(ds.df[ds.x_col], errors="coerce").to_numpy(dtype=float)
            y = pd.to_numeric(ds.df[ds.y_col], errors="coerce").to_numpy(dtype=float)
            valid = np.isfinite(x) & np.isfinite(y)
            ds.x = x[valid]
            ds.y = y[valid] * self.y_unit_to_base_factor[ds.y_unit]
        except Exception:
            ds.x = np.array([])
            ds.y = np.array([])

    # ---------------------------------------------------------
    # CVDataset list management
    # ---------------------------------------------------------
    def load_file(self):
        last_dir = self.settings.value("paths/last_load_dir", "")

        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add data file(s)",
            last_dir,
            "Excel files (*.xlsx *.xls);;CSV files (*.csv *.txt);;All files (*.*)"
        )

        if not paths:
            return

        self.settings.setValue("paths/last_load_dir", os.path.dirname(paths[0]))

        for path in paths:
            try:
                ds = self._make_dataset(path)
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "File error",
                    f"Could not load the file:\n{path}\n\n{e}"
                )
                continue

            if len(ds.sheets) > 1:
                preset = (getattr(self, "preset_sheets", None) or {}).get(path)
                if preset:  # sheets saved in a session: no need to ask again
                    chosen = [sh for sh in preset if sh in ds.sheets]
                else:
                    dlg = SheetPickerDialog(path, ds.sheets, checked=[ds.sheet], parent=self)
                    if dlg.exec() != QDialog.Accepted:
                        continue
                    chosen = dlg.selected_sheets()
                for sheet in chosen:
                    new = self._sheet_variant(ds, sheet)
                    if new is not None:
                        self._add_dataset(new)
            else:
                self._add_dataset(ds)

        self._reset_x_range()
        self.update_plot()

    def add_sheets(self):
        """Add one line per chosen sheet from the selected dataset's file."""
        ds = self.current
        if ds is None:
            QMessageBox.information(self, "Add sheets", "Select an Excel dataset in the list first.")
            return
        if len(ds.sheets) < 2:
            QMessageBox.information(self, "Add sheets", "This file has only one sheet (or is a CSV).")
            return

        plotted = {d.sheet for d in self.datasets if d.file_path == ds.file_path}
        dlg = SheetPickerDialog(ds.file_path, ds.sheets, already_plotted=plotted, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        for sheet in dlg.selected_sheets():
            new = self._sheet_variant(ds, sheet)
            if new is not None:
                self._add_dataset(new)

        self._reset_x_range()
        self.update_plot()

    def _sheet_variant(self, ds, sheet):
        """A copy of ds pointing at another sheet, keeping its column/unit/style choices."""
        new = copy.copy(ds)
        new.visible = True
        if sheet != ds.sheet:
            try:
                new.df = self._read_sheet(ds, sheet)
            except Exception as e:
                QMessageBox.warning(self, "Sheet error", f"Could not read sheet '{sheet}':\n\n{e}")
                return None
            new.sheet = sheet
            self._auto_columns(new, keep=True)
        new.label_auto = True
        new.label = self._auto_label(new)
        self._compute(new)
        return new

    def _add_dataset(self, ds):
        self.datasets.append(ds)
        item = QListWidgetItem(ds.label)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if ds.visible else Qt.Unchecked)
        self.dataset_list.blockSignals(True)
        self.dataset_list.addItem(item)
        self.dataset_list.blockSignals(False)
        self.dataset_list.setCurrentRow(len(self.datasets) - 1)

    def duplicate_dataset(self):
        ds = self.current
        if ds is None:
            return
        new = copy.copy(ds)
        if ds.sheets:
            idx = ds.sheets.index(ds.sheet)
            if idx + 1 < len(ds.sheets):
                new.sheet = ds.sheets[idx + 1]
                try:
                    new.df = self._read_sheet(new, new.sheet)
                except Exception as e:
                    QMessageBox.warning(self, "Sheet error", f"Could not read sheet '{new.sheet}':\n\n{e}")
                    new.sheet, new.df = ds.sheet, ds.df
                self._auto_columns(new, keep=True)
        if new.label_auto:
            new.label = self._auto_label(new)
        if new.label == ds.label:
            new.label += " (copy)"
        self._compute(new)
        self._add_dataset(new)
        self._reset_x_range()
        self.update_plot()

    def remove_dataset(self):
        row = self.dataset_list.currentRow()
        if row < 0:
            return
        self.datasets.pop(row)
        self.dataset_list.takeItem(row)
        if not self.datasets:
            self.on_dataset_selected(-1)
        self._reset_x_range()
        self.update_plot()

    def on_dataset_selected(self, row):
        self.current = self.datasets[row] if 0 <= row < len(self.datasets) else None
        self._load_editor()

    def on_dataset_item_changed(self, item):
        row = self.dataset_list.row(item)
        if 0 <= row < len(self.datasets):
            self.datasets[row].visible = item.checkState() == Qt.Checked
            self._reset_x_range()
            self.update_plot()

    def _set_item_text(self, ds):
        row = self.datasets.index(ds)
        self.dataset_list.blockSignals(True)
        self.dataset_list.item(row).setText(ds.label)
        self.dataset_list.blockSignals(False)

    def _load_editor(self):
        """Show the current dataset's settings in the editor widgets."""
        ds = self.current
        widgets = (self.sheet_combo, self.label_edit, self.x_col_combo, self.y_col_combo,
                   self.y_unit_combo, self.marker_combo, self.linestyle_combo, self.color_combo)
        for w in widgets:
            w.blockSignals(True)

        self.sheet_combo.clear()
        self.x_col_combo.clear()
        self.y_col_combo.clear()

        if ds is None:
            self.label_edit.clear()
            self.file_label.setText("No data file loaded")
            self._set_editor_enabled(False)
        else:
            self._set_editor_enabled(True)
            self.file_label.setText(ds.file_path)
            if ds.sheets:
                self.sheet_combo.addItems(ds.sheets)
                self.sheet_combo.setCurrentText(ds.sheet)
                self.sheet_combo.setEnabled(True)
            else:
                self.sheet_combo.addItem("(CSV - no sheets)")
                self.sheet_combo.setEnabled(False)
            self.label_edit.setText(ds.label)
            cols = list(ds.df.columns)
            self.x_col_combo.addItems(cols)
            self.y_col_combo.addItems(cols)
            if ds.x_col is not None:
                self.x_col_combo.setCurrentText(ds.x_col)
            if ds.y_col is not None:
                self.y_col_combo.setCurrentText(ds.y_col)
            self.y_unit_combo.setCurrentText(ds.y_unit)
            self.marker_combo.setCurrentText(ds.marker)
            self.linestyle_combo.setCurrentText(ds.linestyle)
            self.color_combo.setCurrentText(ds.color)

        for w in widgets:
            w.blockSignals(False)

    # ---------------------------------------------------------
    # Editor -> current dataset
    # ---------------------------------------------------------
    def on_sheet_changed(self):
        ds = self.current
        if ds is None or not ds.sheets:
            return
        sheet = self.sheet_combo.currentText()
        if not sheet or sheet == ds.sheet:
            return
        try:
            df = self._read_sheet(ds, sheet)
        except Exception as e:
            QMessageBox.warning(self, "Sheet error", f"Could not read sheet '{sheet}':\n\n{e}")
            self._load_editor()
            return
        ds.sheet = sheet
        ds.df = df
        self._auto_columns(ds, keep=True)
        if ds.label_auto:
            ds.label = self._auto_label(ds)
            self._set_item_text(ds)
        self._compute(ds)
        self._load_editor()
        self._reset_x_range()
        self.update_plot()

    def on_label_edited(self, text):
        ds = self.current
        if ds is None:
            return
        ds.label = text
        ds.label_auto = False
        self._set_item_text(ds)
        self.update_plot()

    def on_columns_changed(self):
        ds = self.current
        if ds is None:
            return
        ds.x_col = self.x_col_combo.currentText()
        ds.y_col = self.y_col_combo.currentText()
        ds.y_unit = self.y_unit_combo.currentText()
        self._compute(ds)
        self.style.set_default_ylabel(f"{self.y_quantity_name} ({self.base_unit})")
        self._reset_x_range()
        self.update_plot()

    def on_style_changed(self):
        ds = self.current
        if ds is None:
            return
        ds.marker = self.marker_combo.currentText()
        ds.linestyle = self.linestyle_combo.currentText()
        ds.color = self.color_combo.currentText()
        self.update_plot()

    def _reset_x_range(self):
        """Set the voltage range to span all visible datasets."""
        xs = [ds.x for ds in self.datasets if ds.visible and len(ds.x) > 0]
        if not xs:
            return
        self.xmin_spin.blockSignals(True)
        self.xmax_spin.blockSignals(True)
        # Round outwards to the spin boxes' 4 decimals so the end points stay inside
        self.xmin_spin.setValue(np.floor(min(np.min(x) for x in xs) * 1e4) / 1e4)
        self.xmax_spin.setValue(np.ceil(max(np.max(x) for x in xs) * 1e4) / 1e4)
        self.xmin_spin.blockSignals(False)
        self.xmax_spin.blockSignals(False)

    # ---------------------------------------------------------
    # Selection (range)
    # ---------------------------------------------------------
    def selected_data(self, ds):
        if len(ds.x) == 0:
            return np.array([]), np.array([])

        xmin = min(self.xmin_spin.value(), self.xmax_spin.value())
        xmax = max(self.xmin_spin.value(), self.xmax_spin.value())

        mask = (ds.x >= xmin) & (ds.x <= xmax)

        X = ds.x[mask]
        Y = ds.y[mask]

        order = np.argsort(X)
        return X[order], Y[order]

    # ---------------------------------------------------------
    # Plot
    # ---------------------------------------------------------
    def update_plot(self):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        self._hover_lines = []

        self._hover_annotation = ax.annotate(
            "", xy=(0, 0), xytext=(15, 15), textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w"),
            arrowprops=dict(arrowstyle="->")
        )
        self._hover_annotation.set_visible(False)

        if not self.datasets:
            ax.set_title("Load a data file to begin")
            self.canvas.draw()
            return

        use_log = bool(self.log_y is not None and self.log_y.isChecked())
        line_width = _to_float(self.style.line_width, 1.8)
        marker_size = _to_float(self.style.marker_size, 4)

        x_lo, x_hi = np.inf, -np.inf
        info = []

        for i, ds in enumerate(self.datasets):
            if not ds.visible:
                continue
            X, Y = self.selected_data(ds)
            info.append(f"{ds.label}: {len(X)} points")
            if len(X) == 0:
                continue

            plot_y = np.abs(Y) if use_log else Y

            marker = MARKER_NAME_TO_SYMBOL.get(ds.marker, "o")
            if marker == "None":
                marker = None
            linestyle = CV_LINE_STYLE_NAME_TO_SYMBOL.get(ds.linestyle, "-")
            # Auto colour is tied to the dataset's position, so hiding one
            # line doesn't shift the colours of the others.
            color = f"C{i % 10}" if ds.color == "(Auto)" else ds.color

            line, = ax.plot(
                X, plot_y, marker=marker, linestyle=linestyle, color=color,
                linewidth=line_width, markersize=marker_size, label=ds.label
            )
            self._hover_lines.append((line, X, plot_y, ds.label))
            x_lo, x_hi = min(x_lo, X[0]), max(x_hi, X[-1])

        self.info_label.setText("Selected points:\n" + ("\n".join(info) if info else "--"))

        if not self._hover_lines:
            ax.set_title("No points in selected voltage range")
            self.canvas.draw()
            return

        if use_log:
            ax.set_yscale("log")

        self.style.apply_to_axes(ax, x_lo, x_hi)

        self.figure.tight_layout()
        self.canvas.draw()

    # ---------------------------------------------------------
    # Hover tooltip
    # ---------------------------------------------------------
    def on_hover(self, event):
        if self._hover_annotation is None:
            return
        vis = self._hover_annotation.get_visible()
        if event.inaxes and self._hover_lines:
            for line, xdata, ydata, label in self._hover_lines:
                cont, _ = line.contains(event)
                if cont:
                    # Compare in screen pixels so lines on a twin or log
                    # axis pick the right point.
                    pts = line.axes.transData.transform(np.column_stack([xdata, ydata]))
                    dists = np.hypot(pts[:, 0] - event.x, pts[:, 1] - event.y)
                    if not np.any(np.isfinite(dists)):
                        continue
                    idx = np.nanargmin(dists)
                    x_pt, y_pt = xdata[idx], ydata[idx]
                    self._hover_annotation.xy = \
                        self._hover_annotation.axes.transData.inverted().transform(pts[idx])
                    self._hover_annotation.set_text(f"{label}\nX: {x_pt:.4g}\nY: {y_pt:.4g}")
                    self._hover_annotation.set_visible(True)
                    self.canvas.draw_idle()
                    return
        if vis:
            self._hover_annotation.set_visible(False)
            self.canvas.draw_idle()

    # ---------------------------------------------------------
    # Export
    # ---------------------------------------------------------
    def _export_columns(self):
        # One pair of columns per visible dataset, side by side
        # (shorter curves are padded with blanks).
        columns = []
        for ds in self.datasets:
            if not ds.visible:
                continue
            X, Y = self.selected_data(ds)
            if len(X) == 0:
                continue
            columns.append(pd.Series(X, name=f"{ds.label}_Voltage_V"))
            columns.append(pd.Series(Y, name=f"{ds.label}_{self.y_quantity_name}_{self.base_unit}"))
        return columns

    def export_csv(self):
        columns = self._export_columns()
        if not columns:
            QMessageBox.warning(
                self,
                "No data",
                "No selected data to export."
            )
            return

        out = pd.concat(columns, axis=1)

        last_dir = self.settings.value("paths/last_save_dir", "")
        default_name = f"{self.file_stem}_plot_data.csv"
        start_path = os.path.join(last_dir, default_name) if last_dir else default_name

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save plotted data",
            start_path,
            "CSV files (*.csv)"
        )

        if not path:
            return

        if not path.lower().endswith(".csv"):
            path += ".csv"

        try:
            out.to_csv(path, index=False)
            self.settings.setValue("paths/last_save_dir", os.path.dirname(path))
            QMessageBox.information(
                self,
                "Saved",
                f"Plotted data saved to:\n{path}"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Save error",
                str(e)
            )

    def save_plot_as_png(self):
        last_dir = self.settings.value("paths/last_save_dir", "")
        saved_path = self.style.save_png(
            self.figure, self,
            default_name=f"{self.file_stem}_vs_voltage.png",
            initial_dir=last_dir
        )
        if saved_path:
            self.settings.setValue("paths/last_save_dir", os.path.dirname(saved_path))


# ---------------------------------------------------------
# RC tab: time constant tau = R*C and frequency f = 1/(2*pi*R*C)
# ---------------------------------------------------------
CAP_TO_F = {"F": 1.0, "mF": 1e-3, "uF": 1e-6, "nF": 1e-9, "pF": 1e-12, "fF": 1e-15}
RES_TO_OHM = {"Ohm": 1.0, "mOhm": 1e-3, "kOhm": 1e3, "MOhm": 1e6, "GOhm": 1e9}
TAU_UNITS = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "ps": 1e-12, "fs": 1e-15}
FREQ_UNITS = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}

RC_MODE_TAU = "Time constant  τ = RC"
RC_MODE_FREQ = "Frequency  f"
RC_MODE_BOTH = "τ and f (twin Y axes)"

R_SOURCE_SAME = "Same sheet as C"
R_SOURCE_FILE = "Another file / sheet"

SWEEP_1 = "Sweep 1 only"
SWEEP_2 = "Sweep 2 only"
SWEEP_EACH = "Each sweep as its own curve"


def split_sweeps(v):
    """
    Split a voltage trace into monotonic sweeps (e.g. a dual sweep
    -10 -> 3 -> -10 V gives two). A turn is only counted once the voltage
    has moved back by more than 1.5 steps, so repeated end points and the
    small drift of a compliance-limited IV sweep don't start a new sweep.
    Returns a list of index arrays; the turning point belongs to the first.
    """
    n = len(v)
    steps = np.abs(np.diff(v))
    steps = steps[steps > 0]
    if n < 3 or len(steps) == 0:
        return [np.arange(n)]
    thr = 1.5 * np.median(steps)

    cuts = []
    direction = 0
    ext = 0                 # index of the furthest point in the current direction
    for i in range(1, n):
        if direction == 0:
            if abs(v[i] - v[0]) > thr:
                direction = np.sign(v[i] - v[0])
                ext = i
            continue
        if (v[i] - v[ext]) * direction > 0:
            ext = i
        elif (v[ext] - v[i]) * direction > thr:
            cuts.append(ext)
            direction = -direction
            ext = i

    bounds = [0] + [c + 1 for c in cuts] + [n]
    return [np.arange(a, b) for a, b in zip(bounds[:-1], bounds[1:]) if b > a]


def match_voltages(v_a, v_b, tol):
    """
    Pair points of v_a and v_b at the same voltage (|dV| <= tol), with no
    interpolation. Each point is used at most once; when several points of
    v_b land on one v_a point, the closest is kept. Returns (ia, ib) sorted by v_a.
    """
    if len(v_a) == 0 or len(v_b) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    order = np.argsort(v_a)
    vs = v_a[order]
    pos = np.clip(np.searchsorted(vs, v_b), 1, len(vs) - 1) if len(vs) > 1 \
        else np.zeros(len(v_b), dtype=int)
    left = np.maximum(pos - 1, 0)
    pick = np.where(np.abs(vs[left] - v_b) <= np.abs(vs[pos] - v_b), left, pos)
    dist = np.abs(vs[pick] - v_b)

    jb = np.where(dist <= tol)[0]
    jb = jb[np.argsort(dist[jb], kind="stable")]       # closest first
    _, first = np.unique(pick[jb], return_index=True)  # one v_b point per v_a point
    jb = jb[first]
    ia = order[pick[jb]]
    srt = np.argsort(v_a[ia])
    return ia[srt], jb[srt]


class RCPlotTab(CVPlotTab):
    """
    Plots tau = R*C and/or f = 1/(2*pi*R*C) against voltage. C comes from
    the dataset's sheet; R comes from the same sheet (paired row by row) or
    from another file, in which case only voltages present in both files
    (within a tolerance) are used. Dual sweeps are split, and sweep k of
    the C data is paired with sweep k of the R data.
    """

    def __init__(self, parent=None):
        super().__init__(
            y_quantity_name="Capacitance",
            y_units=list(CAP_TO_F),
            y_unit_to_base_factor=CAP_TO_F,
            base_unit="F",
            default_y_unit="F",
            x_keywords=["voltage", "volt", "anodev", "dcv"],
            y_keywords=["capacitance", "cp", "cap"],
            x_fallback_index=2,
            y_fallback_index=0,
            default_title="RC time constant",
            allow_log_y=True,
            parent=parent,
        )
        self.file_stem = "rc"
        self.r_keywords = ["resistance", "ohm", "res", "rp", "rs"]
        self.r_fallback_index = 1
        self.default_r_unit = "Ohm"

    # ---------------------------------------------------------
    # UI: add resistance controls and the tau / f options
    # ---------------------------------------------------------
    def _build_ui(self):
        super()._build_ui()
        self.log_y.setText("Log scale on Y (|τ|, |f|)")

        col_form = self.col_group.layout()

        self.r_source_combo = QComboBox()
        self.r_source_combo.addItems([R_SOURCE_SAME, R_SOURCE_FILE])
        self.r_source_combo.currentIndexChanged.connect(self.on_r_source_changed)
        col_form.addRow("Resistance from:", self.r_source_combo)

        self.r_file_btn = QPushButton("Choose R file...")
        self.r_file_btn.clicked.connect(self.choose_r_file)
        col_form.addRow(self.r_file_btn)

        self.r_file_label = QLabel("")
        self.r_file_label.setWordWrap(True)
        col_form.addRow(self.r_file_label)

        self.r_sheet_combo = QComboBox()
        self.r_sheet_combo.currentIndexChanged.connect(self.on_r_sheet_changed)
        col_form.addRow("R sheet:", self.r_sheet_combo)

        self.r_v_col_combo = QComboBox()
        self.r_v_col_combo.currentIndexChanged.connect(self.on_r_columns_changed)
        col_form.addRow("R voltage column:", self.r_v_col_combo)

        self.r_col_combo = QComboBox()
        self.r_col_combo.currentIndexChanged.connect(self.on_r_columns_changed)
        col_form.addRow("Resistance column:", self.r_col_combo)

        self.r_is_g = QCheckBox("Column is a conductance in S (R = 1/G)")
        self.r_is_g.setToolTip("e.g. Gp_AC from the CV sheet, giving τ = Cp/Gp")
        self.r_is_g.stateChanged.connect(self.on_r_columns_changed)
        col_form.addRow(self.r_is_g)

        self.r_unit_combo = QComboBox()
        self.r_unit_combo.addItems(list(RES_TO_OHM))
        self.r_unit_combo.currentIndexChanged.connect(self.on_r_columns_changed)
        col_form.addRow("Resistance unit (raw data):", self.r_unit_combo)

        self._r_widgets = (self.r_source_combo, self.r_sheet_combo, self.r_v_col_combo,
                           self.r_col_combo, self.r_unit_combo, self.r_is_g)
        for w in self._r_widgets:
            w.setMinimumWidth(180)

        # Options that apply to all datasets
        rc_group = QGroupBox("RC quantity (all datasets)")
        rc_form = QFormLayout(rc_group)
        rc_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        rc_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([RC_MODE_TAU, RC_MODE_FREQ, RC_MODE_BOTH])
        rc_form.addRow("Plot:", self.mode_combo)

        self.sweep_combo = QComboBox()
        self.sweep_combo.addItems([SWEEP_1, SWEEP_2, SWEEP_EACH])
        self.sweep_combo.setToolTip("Dual-sweep data is split where the voltage turns around")
        rc_form.addRow("Sweep:", self.sweep_combo)

        self.v_tol_spin = QDoubleSpinBox()
        self.v_tol_spin.setDecimals(4)
        self.v_tol_spin.setRange(0.0, 1.0)
        self.v_tol_spin.setSingleStep(0.005)
        self.v_tol_spin.setValue(0.01)
        self.v_tol_spin.setToolTip("R and C points are paired only when their voltages differ by at most this")
        self.v_tol_spin.valueChanged.connect(self.on_tolerance_changed)
        rc_form.addRow("Voltage match tolerance (V):", self.v_tol_spin)

        self.tau_unit_combo = QComboBox()
        self.tau_unit_combo.addItems(list(TAU_UNITS))
        self.tau_unit_combo.setCurrentText("ns")
        rc_form.addRow("τ unit:", self.tau_unit_combo)

        self.freq_unit_combo = QComboBox()
        self.freq_unit_combo.addItems(list(FREQ_UNITS))
        self.freq_unit_combo.setCurrentText("MHz")
        rc_form.addRow("f unit:", self.freq_unit_combo)

        self.two_pi = QCheckBox("f = 1/(2πRC)   (unchecked: f = 1/(RC))")
        self.two_pi.setChecked(True)
        rc_form.addRow(self.two_pi)

        for w in (self.mode_combo, self.sweep_combo, self.tau_unit_combo, self.freq_unit_combo):
            w.setMinimumWidth(180)
            w.currentIndexChanged.connect(self.on_rc_options_changed)
        self.v_tol_spin.setMinimumWidth(180)
        self.two_pi.stateChanged.connect(self.on_rc_options_changed)

        left = self.col_group.parentWidget().layout()
        left.insertWidget(left.indexOf(self.col_group) + 1, rc_group)

        self._update_default_ylabel()

    def _set_r_file_widgets_enabled(self, enabled):
        for w in (self.r_file_btn, self.r_file_label, self.r_sheet_combo, self.r_v_col_combo):
            w.setEnabled(enabled)

    # ---------------------------------------------------------
    # Columns and computation
    # ---------------------------------------------------------
    def _auto_columns(self, ds, keep=True):
        super()._auto_columns(ds, keep)
        if ds.r_unit is None:
            ds.r_unit = self.default_r_unit
        src = ds.r_src if ds.r_src is not None else ds
        cols = list(src.df.columns)
        if not cols:
            ds.r_col = None
            return
        if not (keep and ds.r_col in cols):
            ds.r_col = cols[self._guess_column_index(cols, self.r_keywords, self.r_fallback_index)]
            # e.g. the fallback lands on Gp_AC in a CV sheet with no R column
            name = ds.r_col.lower()
            ds.r_is_g = name.startswith("g") or "conduct" in name
        if ds.r_src is not None and not (keep and src.x_col in cols):
            src.x_col = cols[self._guess_column_index(cols, self.x_keywords, self.x_fallback_index)]

    @staticmethod
    def _numeric(df, col):
        return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)

    def _resistance(self, ds, raw):
        if ds.r_is_g:
            with np.errstate(divide="ignore"):
                return 1.0 / raw
        return raw * RES_TO_OHM[ds.r_unit]

    def _compute(self, ds):
        """
        Build ds.sweeps: one (V, V_R, R in Ohm, C in F) tuple per sweep,
        with R and C taken at the same voltage.
        """
        ds.sweeps = []
        try:
            v = self._numeric(ds.df, ds.x_col)
            c = self._numeric(ds.df, ds.y_col) * CAP_TO_F[ds.y_unit]
            if ds.r_src is None:
                r = self._resistance(ds, self._numeric(ds.df, ds.r_col))
                ok = np.isfinite(v) & np.isfinite(c) & np.isfinite(r)
                v, c, r = v[ok], c[ok], r[ok]
                for idx in split_sweeps(v):
                    ds.sweeps.append((v[idx], v[idx], r[idx], c[idx]))
            else:
                ok = np.isfinite(v) & np.isfinite(c)
                v, c = v[ok], c[ok]
                vr = self._numeric(ds.r_src.df, ds.r_src.x_col)
                r = self._resistance(ds, self._numeric(ds.r_src.df, ds.r_col))
                ok = np.isfinite(vr) & np.isfinite(r)
                vr, r = vr[ok], r[ok]
                r_sweeps = split_sweeps(vr)
                tol = self.v_tol_spin.value()
                for k, ic in enumerate(split_sweeps(v)):
                    # A single-sweep R file is reused for every C sweep
                    jr = r_sweeps[min(k, len(r_sweeps) - 1)]
                    ia, jb = match_voltages(v[ic], vr[jr], tol)
                    ds.sweeps.append((v[ic][ia], vr[jr][jb], r[jr][jb], c[ic][ia]))
        except Exception:
            ds.sweeps = []

        ds.x = np.concatenate([s[0] for s in ds.sweeps]) if ds.sweeps else np.array([])
        ds.r = np.concatenate([s[2] for s in ds.sweeps]) if ds.sweeps else np.array([])
        ds.c = np.concatenate([s[3] for s in ds.sweeps]) if ds.sweeps else np.array([])
        ds.y = ds.r * ds.c

    def _chosen_sweeps(self, ds):
        """(sweep number, sweep data) pairs picked by the sweep selector."""
        choice = self.sweep_combo.currentText()
        numbered = list(enumerate(ds.sweeps, start=1))
        if choice == SWEEP_1:
            return numbered[:1]
        if choice == SWEEP_2:
            return numbered[1:2]
        return numbered

    def rc_selected(self, ds):
        """
        For each chosen sweep inside the voltage range: (sweep number, V,
        V_R, R in Ohm, C in F, tau in s, f in Hz), sorted by V.
        """
        xmin = min(self.xmin_spin.value(), self.xmax_spin.value())
        xmax = max(self.xmin_spin.value(), self.xmax_spin.value())
        k = 2 * np.pi if self.two_pi.isChecked() else 1.0
        out = []
        for n, (V, VR, R, C) in self._chosen_sweeps(ds):
            mask = (V >= xmin) & (V <= xmax)
            order = np.argsort(V[mask], kind="stable")
            V_, VR_, R_, C_ = (a[mask][order] for a in (V, VR, R, C))
            tau = R_ * C_
            with np.errstate(divide="ignore", invalid="ignore"):
                f = 1.0 / (k * tau)
            f[~np.isfinite(f)] = np.nan
            out.append((n, V_, VR_, R_, C_, tau, f))
        return out

    # ---------------------------------------------------------
    # Editor
    # ---------------------------------------------------------
    def _load_editor(self):
        super()._load_editor()
        ds = self.current
        for w in self._r_widgets:
            w.blockSignals(True)

        self.r_sheet_combo.clear()
        self.r_v_col_combo.clear()
        self.r_col_combo.clear()

        if ds is None:
            self.r_file_label.setText("")
            self._set_r_file_widgets_enabled(False)
        else:
            src = ds.r_src
            self.r_source_combo.setCurrentText(R_SOURCE_SAME if src is None else R_SOURCE_FILE)
            self._set_r_file_widgets_enabled(src is not None)
            if src is None:
                self.r_file_label.setText("")
                self.r_col_combo.addItems(list(ds.df.columns))
            else:
                self.r_file_label.setText(src.file_path)
                if src.sheets:
                    self.r_sheet_combo.addItems(src.sheets)
                    self.r_sheet_combo.setCurrentText(src.sheet)
                else:
                    self.r_sheet_combo.addItem("(CSV - no sheets)")
                    self.r_sheet_combo.setEnabled(False)
                cols = list(src.df.columns)
                self.r_v_col_combo.addItems(cols)
                self.r_col_combo.addItems(cols)
                if src.x_col is not None:
                    self.r_v_col_combo.setCurrentText(src.x_col)
            if ds.r_col is not None:
                self.r_col_combo.setCurrentText(ds.r_col)
            self.r_unit_combo.setCurrentText(ds.r_unit or self.default_r_unit)
            self.r_is_g.setChecked(ds.r_is_g)
            self.r_unit_combo.setEnabled(not ds.r_is_g)

        for w in self._r_widgets:
            w.blockSignals(False)

    def _refresh_current(self):
        self._compute(self.current)
        self._load_editor()
        self._reset_x_range()
        self.update_plot()

    def choose_r_file(self):
        """Pick the file that holds R for the selected dataset. Returns True on success."""
        ds = self.current
        if ds is None:
            return False
        last_dir = self.settings.value("paths/last_load_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose resistance file", last_dir,
            "Excel files (*.xlsx *.xls);;CSV files (*.csv *.txt);;All files (*.*)"
        )
        if not path:
            return False
        try:
            src = self._open_file(path)
        except Exception as e:
            QMessageBox.critical(self, "File error", f"Could not load the file:\n{path}\n\n{e}")
            return False
        self.settings.setValue("paths/last_load_dir", os.path.dirname(path))
        # Start on the sheet with the same name as the C sheet, if there is one
        if ds.sheet in src.sheets and ds.sheet != src.sheet:
            try:
                src.df = self._read_sheet(src, ds.sheet)
                src.sheet = ds.sheet
            except Exception:
                pass
        ds.r_src = src
        self._auto_columns(ds, keep=False)
        self._refresh_current()
        return True

    def on_r_source_changed(self):
        ds = self.current
        if ds is None:
            return
        if self.r_source_combo.currentText() == R_SOURCE_SAME:
            ds.r_src = None
            self._auto_columns(ds, keep=True)
            self._refresh_current()
        elif ds.r_src is None and not self.choose_r_file():
            self._load_editor()     # cancelled: back to "same sheet"

    def on_r_sheet_changed(self):
        ds = self.current
        if ds is None or ds.r_src is None or not ds.r_src.sheets:
            return
        sheet = self.r_sheet_combo.currentText()
        if not sheet or sheet == ds.r_src.sheet:
            return
        try:
            df = self._read_sheet(ds.r_src, sheet)
        except Exception as e:
            QMessageBox.warning(self, "Sheet error", f"Could not read sheet '{sheet}':\n\n{e}")
            self._load_editor()
            return
        # Duplicated datasets share r_src, so change a private copy
        ds.r_src = copy.copy(ds.r_src)
        ds.r_src.sheet, ds.r_src.df = sheet, df
        self._auto_columns(ds, keep=True)
        self._refresh_current()

    def on_r_columns_changed(self):
        ds = self.current
        if ds is None:
            return
        ds.r_col = self.r_col_combo.currentText()
        ds.r_unit = self.r_unit_combo.currentText()
        ds.r_is_g = self.r_is_g.isChecked()
        self.r_unit_combo.setEnabled(not ds.r_is_g)
        if ds.r_src is not None:
            ds.r_src = copy.copy(ds.r_src)
            ds.r_src.x_col = self.r_v_col_combo.currentText()
        self._compute(ds)
        self._reset_x_range()
        self.update_plot()

    def on_columns_changed(self):
        ds = self.current
        if ds is None:
            return
        ds.x_col = self.x_col_combo.currentText()
        ds.y_col = self.y_col_combo.currentText()
        ds.y_unit = self.y_unit_combo.currentText()
        self._compute(ds)
        self._reset_x_range()
        self.update_plot()

    def on_tolerance_changed(self):
        for ds in self.datasets:
            self._compute(ds)
        self._reset_x_range()
        self.update_plot()

    # ---------------------------------------------------------
    # Labels
    # ---------------------------------------------------------
    def _tau_label(self):
        return f"τ = RC ({self.tau_unit_combo.currentText()})"

    def _freq_label(self):
        formula = "1/(2πRC)" if self.two_pi.isChecked() else "1/(RC)"
        return f"f = {formula} ({self.freq_unit_combo.currentText()})"

    def _update_default_ylabel(self):
        mode = self.mode_combo.currentText()
        self.style.set_default_ylabel(self._freq_label() if mode == RC_MODE_FREQ else self._tau_label())

    def on_rc_options_changed(self):
        self._update_default_ylabel()
        self.update_plot()

    # ---------------------------------------------------------
    # Plot
    # ---------------------------------------------------------
    def update_plot(self):
        # The base __init__ can trigger a redraw before the RC widgets exist
        if not hasattr(self, "v_tol_spin"):
            return super().update_plot()

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        mode = self.mode_combo.currentText()
        twin = ax.twinx() if mode == RC_MODE_BOTH else None
        top = twin if twin is not None else ax
        self._hover_lines = []

        self._hover_annotation = top.annotate(
            "", xy=(0, 0), xytext=(15, 15), textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w"),
            arrowprops=dict(arrowstyle="->")
        )
        self._hover_annotation.set_visible(False)

        if not self.datasets:
            ax.set_title("Load a data file to begin")
            self.canvas.draw()
            return

        use_log = self.log_y.isChecked()
        each = self.sweep_combo.currentText() == SWEEP_EACH
        line_width = _to_float(self.style.line_width, 1.8)
        marker_size = _to_float(self.style.marker_size, 4)
        tau_scale = TAU_UNITS[self.tau_unit_combo.currentText()]
        freq_scale = FREQ_UNITS[self.freq_unit_combo.currentText()]

        x_lo, x_hi = np.inf, -np.inf
        info = []

        for i, ds in enumerate(self.datasets):
            if not ds.visible:
                continue
            sweeps = self.rc_selected(ds)
            if not sweeps:
                info.append(f"{ds.label}: no such sweep ({len(ds.sweeps)} found)")
                continue

            marker = MARKER_NAME_TO_SYMBOL.get(ds.marker, "o")
            if marker == "None":
                marker = None
            linestyle = CV_LINE_STYLE_NAME_TO_SYMBOL.get(ds.linestyle, "-")
            color = f"C{i % 10}" if ds.color == "(Auto)" else ds.color

            for n, X, _, _, _, tau, f in sweeps:
                name = f"{ds.label} sweep {n}" if each else ds.label
                info.append(f"{name}: {len(X)} matched points")
                if len(X) == 0:
                    continue

                tau_y, f_y = tau / tau_scale, f / freq_scale
                if use_log:
                    tau_y, f_y = np.abs(tau_y), np.abs(f_y)

                # Later sweeps get open markers so the directions can be told apart
                kw = dict(marker=marker, color=color, linewidth=line_width, markersize=marker_size)
                if n > 1:
                    kw["markerfacecolor"] = "none"

                if mode == RC_MODE_FREQ:
                    curves = [(ax, f_y, name, linestyle)]
                elif mode == RC_MODE_TAU:
                    curves = [(ax, tau_y, name, linestyle)]
                else:
                    # f drawn dashed on the right axis
                    curves = [(ax, tau_y, f"{name} (τ)", linestyle),
                              (twin, f_y, f"{name} (f)", "--")]

                for axis, y, label, ls in curves:
                    line, = axis.plot(X, y, linestyle=ls, label=label, **kw)
                    self._hover_lines.append((line, X, y, label))
                x_lo, x_hi = min(x_lo, X[0]), max(x_hi, X[-1])

        self.info_label.setText("Selected points:\n" + ("\n".join(info) if info else "--"))

        if not self._hover_lines:
            ax.set_title("No matched R-C points in the selected voltage range")
            self.canvas.draw()
            return

        if use_log:
            ax.set_yscale("log")
            if twin is not None:
                twin.set_yscale("log")

        self.style.apply_to_axes(ax, x_lo, x_hi)
        if twin is not None:
            self._style_twin(ax, twin)

        self.figure.tight_layout()
        self.canvas.draw()

    def _style_twin(self, ax, twin):
        """Match the right-hand f axis to the plot style and merge both legends."""
        st = self.style
        label_weight = "bold" if st.bold_axis_labels.isChecked() else "normal"
        twin.set_ylabel(self._freq_label(), fontsize=_to_float(st.axis_label_font_size, 12),
                        fontweight=label_weight)

        if twin.get_yscale() == "linear":
            n_minor_y = _combo_int(st.num_minor_ticks_y, 0)
            twin.yaxis.set_minor_locator(
                AutoMinorLocator(n_minor_y + 1) if n_minor_y > 0 else NullLocator())

        ax.tick_params(axis="y", which="both", right=False)
        twin.tick_params(axis="y", which="major", direction="in",
                         length=_to_float(st.major_tick_length, 6),
                         width=_to_float(st.major_tick_width, 1.2),
                         labelsize=_to_float(st.plot_font_size, 11),
                         left=False, right=True)
        twin.tick_params(axis="y", which="minor", direction="in",
                         length=_to_float(st.minor_tick_length, 3),
                         width=_to_float(st.minor_tick_width, 0.8),
                         left=False, right=True)
        tick_weight = "bold" if st.bold_ticks.isChecked() else "normal"
        for lbl in twin.get_yticklabels():
            lbl.set_fontweight(tick_weight)
        spine_w = _to_float(st.spine_width, 1.2)
        for spine in twin.spines.values():
            spine.set_linewidth(spine_w)

        # One legend for both axes, drawn on the top axis so lines don't cover it
        if ax.get_legend() is not None:
            ax.get_legend().remove()
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = twin.get_legend_handles_labels()
        st.apply_legend(twin, h1 + h2, l1 + l2)

    # ---------------------------------------------------------
    # Export
    # ---------------------------------------------------------
    def _export_columns(self):
        tau_unit = self.tau_unit_combo.currentText()
        freq_unit = self.freq_unit_combo.currentText()
        columns = []
        for ds in self.datasets:
            if not ds.visible:
                continue
            for n, X, VR, R, C, tau, f in self.rc_selected(ds):
                if len(X) == 0:
                    continue
                p = f"{ds.label}_sweep{n}"
                columns += [
                    pd.Series(X, name=f"{p}_Voltage_V"),
                    pd.Series(VR, name=f"{p}_Voltage_R_V"),
                    pd.Series(R, name=f"{p}_Resistance_Ohm"),
                    pd.Series(C, name=f"{p}_Capacitance_F"),
                    pd.Series(tau / TAU_UNITS[tau_unit], name=f"{p}_tau_{tau_unit}"),
                    pd.Series(f / FREQ_UNITS[freq_unit], name=f"{p}_f_{freq_unit}"),
                ]
        return columns


# =====================================================================
# THz Drude fitting tab (2DEG sheet conductance from multilayer transmission)
# =====================================================================
# DrudeFitApp is a QMainWindow shown inside a tab.
Z0 = 376.730313668
Q = 1.602176634e-19
M0 = 9.1093837015e-31
C0 = 299792458.0


def load_two_column_file(path):
    """
    Load a numeric two-column file.
    Supports whitespace, comma, semicolon, or tab separated text.
    Header lines are ignored when possible.
    """
    loaders = [
        lambda: np.loadtxt(path),
        lambda: np.loadtxt(path, delimiter=","),
        lambda: np.loadtxt(path, delimiter=";"),
        lambda: np.genfromtxt(path, delimiter=None, invalid_raise=False),
        lambda: np.genfromtxt(path, delimiter=",", invalid_raise=False),
    ]

    last_err = None
    for loader in loaders:
        try:
            data = loader()
            data = np.asarray(data, dtype=float)

            if data.ndim == 1:
                if data.size == 2:
                    data = data.reshape(1, 2)
                else:
                    continue

            if data.shape[1] < 2:
                continue

            data = data[:, :2]
            mask = np.isfinite(data[:, 0]) & np.isfinite(data[:, 1])
            data = data[mask]

            if len(data) < 5:
                continue

            f = data[:, 0]
            y = data[:, 1]

            idx = np.argsort(f)
            return f[idx], y[idx]
        except Exception as exc:
            last_err = exc

    raise ValueError(f"Could not read a valid two-column numeric file.\n{last_err}")


def choose_savgol_window(n, fraction):
    """
    Convert a fractional smoothing span into a valid odd Savitzky-Golay window.
    """
    fraction = float(np.clip(fraction, 0.001, 1.0))
    w = max(5, int(round(n * fraction)))

    if w % 2 == 0:
        w += 1

    max_w = n if n % 2 == 1 else n - 1
    w = min(w, max_w)

    if w < 5:
        w = 5 if n >= 5 else max_w

    if w % 2 == 0:
        w -= 1

    return max(3, w)


def drude_sheet_conductivity(freq_ghz, g0, tau, sign=1):
    """
    Complex Drude sheet conductivity:
        G(w) = G0 / (1 + i*w*tau)

    The sign matches the exp(i(wt - kz)) time dependence of the layer
    matrices (propagation phase exp(-i*delta), Pedrotti, Introduction to Optics, Ch. 22).

    G0   : DC sheet conductance [S/square]
    tau  : momentum scattering time [s]
    sign : +1 gives G0/(1 + i*w*tau) (correct for these matrices);
           -1 gives G0/(1 - i*w*tau), kept only as a diagnostic comparison
    """
    omega = 2 * np.pi * np.asarray(freq_ghz) * 1e9
    return g0 / (1.0 + sign * 1j * omega * tau)


def thin_sheet_transmission(freq_ghz, g0, tau, n_sub, sign=1):
    """
    Tinkham thin-sheet approximation for a conducting sheet at an
    air / semi-infinite dielectric interface.

        t_norm = (1+n) / (1+n + Z0*G(w))

    Used as a fast envelope model. For the real multilayer
    heterostructure, use one of the full-spectrum TMM models.
    """
    G = drude_sheet_conductivity(freq_ghz, g0, tau, sign)
    t = (1.0 + n_sub) / (1.0 + n_sub + Z0 * G)
    return np.abs(t)


def optical_value_to_n(kind, value):
    """
    Convert a layer optical input into refractive index.

    kind = "eps_r": n = sqrt(eps_r)
    kind = "n"    : n is used directly

    Values may be real or complex. The GUI currently supplies real values.
    Complex values follow the exp(i(wt - kz)) convention used here: an
    absorbing layer is n - i*kappa, or eps_r = eps' - i*eps''.
    """
    value = complex(value)
    if kind == "eps_r":
        return np.sqrt(value + 0j)
    if kind == "n":
        return value
    raise ValueError(f"Unknown optical input type: {kind}")


def _mat_mul(A, B):
    """Product of two 2x2 matrices stored as (m00, m01, m10, m11) arrays."""
    return (
        A[0] * B[0] + A[1] * B[2],
        A[0] * B[1] + A[1] * B[3],
        A[2] * B[0] + A[3] * B[2],
        A[2] * B[1] + A[3] * B[3],
    )


def _stack_product(freq_hz, layers):
    """
    Characteristic matrix of a list of layers, one 2x2 matrix per frequency,
    returned as a tuple of four arrays. An empty list gives the identity.
    """
    one = np.ones_like(freq_hz, dtype=complex)
    zero = np.zeros_like(freq_hz, dtype=complex)
    M = (one, zero, zero, one)

    for layer in layers:
        nlay = complex(layer["n"])
        dlay = float(layer["thickness_m"])

        delta = 2.0 * np.pi * freq_hz * nlay * dlay / C0
        c = np.cos(delta)
        sd = np.sin(delta)

        M = _mat_mul(M, (c, 1j * sd / nlay, 1j * nlay * sd, c))

    return M


def _transmission_from_matrix(M, n_inc, n_exit):
    q0 = complex(n_inc)
    qs = complex(n_exit)
    denom = q0 * M[0] + q0 * qs * M[1] + M[2] + qs * M[3]
    return 2.0 * q0 / denom


def _split_stack(layers, sheet_after):
    """Split the stack at the sheet boundary into (front, back) layer lists."""
    if sheet_after < -1 or sheet_after >= len(layers):
        return list(layers), []
    return list(layers[:sheet_after + 1]), list(layers[sheet_after + 1:])


def _broadcast_sheet(sheet_g, nfreq):
    sheet_g = np.asarray(sheet_g, dtype=complex)
    if sheet_g.ndim == 0:
        return np.full(nfreq, sheet_g, dtype=complex)
    return np.broadcast_to(sheet_g, (nfreq,)).astype(complex, copy=False)


def multilayer_complex_transmission(
    freq_ghz, layers, sheet_g=None, sheet_after=-1, n_inc=1.0, n_exit=1.0
):
    """
    Complex field transmission through an arbitrary dielectric multilayer
    at normal incidence, with an optional conductive sheet.

    Parameters
    ----------
    freq_ghz : array-like
        Frequency in GHz.
    layers : list of dicts
        Each dict contains:
            n           complex refractive index, n - i*kappa
            thickness_m physical thickness in meters
            name        optional layer name
    sheet_g : array-like complex or None
        Complex sheet conductance G(f) [S/square].
    sheet_after : int
        Sheet boundary index:
            -1 -> before layer 0
             0 -> after layer 0
             1 -> after layer 1
             ...
        Any other value means no sheet.
        For AlGaN/GaN, placing the 2DEG after the AlN row puts it at the
        AlN/GaN interface.
    n_inc, n_exit : complex
        Refractive indices of incident and exit semi-infinite media.

    Returns
    -------
    complex ndarray
        Complex electric-field transmission amplitude.
    """
    freq_ghz = np.atleast_1d(np.asarray(freq_ghz, dtype=float))
    freq_hz = freq_ghz * 1e9

    if sheet_g is None or not (-1 <= sheet_after < len(layers)):
        M = _stack_product(freq_hz, layers)
        return _transmission_from_matrix(M, n_inc, n_exit)

    sheet_g = _broadcast_sheet(sheet_g, freq_hz.size)
    front, back = _split_stack(layers, sheet_after)
    A = _stack_product(freq_hz, front)
    B = _stack_product(freq_hz, back)

    one = np.ones_like(freq_hz, dtype=complex)
    zero = np.zeros_like(freq_hz, dtype=complex)
    S = (one, zero, Z0 * sheet_g, one)

    M = _mat_mul(_mat_mul(A, S), B)
    return _transmission_from_matrix(M, n_inc, n_exit)


def multilayer_sample_and_bare(
    freq_ghz, g0, tau, layers, sheet_after, n_inc=1.0, n_exit=1.0, sign=1
):
    """
    Complex transmission of the stack with and without the 2DEG sheet.
    The front and back sub-stack matrices are computed once and reused,
    so this costs about the same as a single transmission call.
    """
    freq_ghz = np.atleast_1d(np.asarray(freq_ghz, dtype=float))
    freq_hz = freq_ghz * 1e9

    front, back = _split_stack(layers, sheet_after)
    A = _stack_product(freq_hz, front)
    B = _stack_product(freq_hz, back)

    bare = _transmission_from_matrix(_mat_mul(A, B), n_inc, n_exit)

    if not (-1 <= sheet_after < len(layers)):
        return bare, bare

    y = Z0 * drude_sheet_conductivity(freq_ghz, g0, tau, sign)
    # A * [[1, 0], [y, 1]] = [[a00 + a01*y, a01], [a10 + a11*y, a11]]
    AS = (A[0] + A[1] * y, A[1], A[2] + A[3] * y, A[3])
    sample = _transmission_from_matrix(_mat_mul(AS, B), n_inc, n_exit)
    return sample, bare


def multilayer_transmission_with_sheet(
    freq_ghz, g0, tau, layers, sheet_after, sign=1
):
    """Absolute field-transmission magnitude of the multilayer + 2DEG."""
    G = drude_sheet_conductivity(freq_ghz, g0, tau, sign)
    t = multilayer_complex_transmission(
        freq_ghz, layers, sheet_g=G, sheet_after=sheet_after
    )
    return np.abs(t)


def normalized_multilayer_transmission(
    freq_ghz, g0, tau, layers, sheet_after, sign=1
):
    """
    Multilayer + 2DEG transmission normalized to the identical dielectric
    multilayer without the conducting sheet.
    """
    sample, bare = multilayer_sample_and_bare(
        freq_ghz, g0, tau, layers, sheet_after, sign=sign
    )
    return np.abs(sample) / np.maximum(np.abs(bare), 1e-15)


# Layer table columns
COL_NAME, COL_KIND, COL_VALUE, COL_FIT_VALUE, COL_VALUE_PCT, \
    COL_THICK, COL_UNIT, COL_FIT_THICK, COL_THICK_PCT = range(9)

UNIT_SCALE = {"nm": 1e-9, "um": 1e-6, "mm": 1e-3}


class FitStopped(Exception):
    """Raised inside the optimizer when the user presses Stop."""


class MplCanvas(FigureCanvas):
    def __init__(self):
        self.fig = Figure(figsize=(8, 6), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)


class DrudeFitApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("THz Drude Fitting Tool")
        # Fit the window inside the available screen area.
        avail = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1500, int(avail.width() * 0.95)),
                    min(950, int(avail.height() * 0.90)))

        self.file_path = None
        self.freq = None
        self.amp = None
        self.smooth_amp = None
        self.peak_freq = None
        self.peak_amp = None
        self.fit_curve = None
        self.fit_params = None
        self.fit_metrics = None
        self.plot_settings = {}
        self.stop_requested = False

        self.build_ui()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        outer = QHBoxLayout(root)
        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter)

        # ---------------- Left panel: tabs of controls + results ----------------
        # Controls are split over tabs so the window fits on small or
        # high-DPI screens; each tab scrolls on its own.
        fit_page = QWidget()
        fit_layout = QVBoxLayout(fit_page)
        layer_page = QWidget()
        layer_layout = QVBoxLayout(layer_page)

        data_group = QGroupBox("Data")
        dg = QGridLayout(data_group)

        self.file_label = QLabel("No file loaded")
        self.file_label.setWordWrap(True)

        load_btn = QPushButton("Load TXT / CSV")
        load_btn.clicked.connect(self.load_file)

        clear_btn = QPushButton("Clear Plot")
        clear_btn.clicked.connect(self.clear_plot)

        dg.addWidget(load_btn, 0, 0)
        dg.addWidget(clear_btn, 0, 1)
        dg.addWidget(self.file_label, 1, 0, 1, 2)
        fit_layout.addWidget(data_group)

        # Processing controls
        proc = QGroupBox("Processing")
        pg = QGridLayout(proc)

        self.show_raw_cb = QCheckBox("Show raw data")
        self.show_raw_cb.setChecked(True)
        self.show_smooth_cb = QCheckBox("Show smoothing")
        self.show_smooth_cb.setChecked(True)
        self.show_peaks_cb = QCheckBox("Show detected maxima")
        self.show_peaks_cb.setChecked(True)
        self.show_fit_cb = QCheckBox("Show fitted model")
        self.show_fit_cb.setChecked(True)

        for cb in (self.show_raw_cb, self.show_smooth_cb, self.show_peaks_cb, self.show_fit_cb):
            cb.stateChanged.connect(self.plot_data)

        self.smooth_spin = QDoubleSpinBox()
        self.smooth_spin.setRange(0.001, 1.0)
        self.smooth_spin.setSingleStep(0.01)
        self.smooth_spin.setDecimals(3)
        # Light smoothing: a wide window flattens the Fabry-Perot fringes
        # that the full-spectrum fit needs to resolve.
        self.smooth_spin.setValue(0.01)
        self.smooth_spin.valueChanged.connect(self.reprocess_and_plot)

        self.prominence_spin = QDoubleSpinBox()
        self.prominence_spin.setRange(0.0, 1.0)
        self.prominence_spin.setSingleStep(0.002)
        self.prominence_spin.setDecimals(4)
        self.prominence_spin.setValue(0.005)
        self.prominence_spin.valueChanged.connect(self.reprocess_and_plot)

        self.xscale_spin = QDoubleSpinBox()
        self.xscale_spin.setRange(1e-9, 1e9)
        self.xscale_spin.setDecimals(6)
        self.xscale_spin.setValue(1.0)
        self.xscale_spin.valueChanged.connect(self.plot_data)

        self.square_y_cb = QCheckBox("Square Y")
        self.square_y_cb.stateChanged.connect(self.plot_data)

        pg.addWidget(QLabel("Smoothing fraction"), 0, 0)
        pg.addWidget(self.smooth_spin, 0, 1)
        pg.addWidget(QLabel("Peak prominence"), 1, 0)
        pg.addWidget(self.prominence_spin, 1, 1)
        pg.addWidget(QLabel("X scale"), 2, 0)
        pg.addWidget(self.xscale_spin, 2, 1)
        pg.addWidget(self.square_y_cb, 3, 0, 1, 2)
        pg.addWidget(self.show_raw_cb, 4, 0, 1, 2)
        pg.addWidget(self.show_smooth_cb, 5, 0, 1, 2)
        pg.addWidget(self.show_peaks_cb, 6, 0, 1, 2)
        pg.addWidget(self.show_fit_cb, 7, 0, 1, 2)

        fit_layout.addWidget(proc)

        # Fit Settings
        settings = QGroupBox("Fit Settings")
        sg = QGridLayout(settings)

        self.model_combo = QComboBox()
        self.model_combo.addItems([
            "Drude-Tinkham (Maxima Envelope)",
            "Full spectrum - normalized multilayer TMM",
            "Full spectrum - absolute multilayer TMM",
        ])

        self.meff_spin = QDoubleSpinBox()
        self.meff_spin.setRange(0.01, 5.0)
        self.meff_spin.setDecimals(4)
        self.meff_spin.setValue(0.20)

        self.power_checkbox = QCheckBox("Input data are power/intensity")
        self.power_checkbox.setChecked(False)

        # Diagnostic only: the layer matrices use exp(i(wt - kz)), for which
        # the physical Drude sign is 1 + i*w*tau.
        self.drude_sign_combo = QComboBox()
        self.drude_sign_combo.addItems([
            "G0/(1 + iωτ)  (matches the layer matrices)",
            "G0/(1 - iωτ)  (diagnostic)",
        ])
        self.drude_sign_combo.setToolTip(
            "The layer matrices follow Pedrotti's exp(i(ωt - kz)) convention, "
            "so the physical Drude term is G0/(1 + iωτ).\n"
            "Fit once with each sign (layer values fixed) and compare RMSE: "
            "if the wrong sign fits as well, the layer stack is probably off."
        )

        sg.addWidget(QLabel("Model"), 0, 0)
        sg.addWidget(self.model_combo, 0, 1)
        sg.addWidget(QLabel("m*/m0"), 1, 0)
        sg.addWidget(self.meff_spin, 1, 1)
        sg.addWidget(self.power_checkbox, 2, 0, 1, 2)
        sg.addWidget(QLabel("Drude sign"), 3, 0)
        sg.addWidget(self.drude_sign_combo, 3, 1)

        # Full-spectrum models: fit the measured points, not the smoothed
        # curve, so smoothing cannot flatten the Fabry-Perot fringes.
        self.fit_raw_checkbox = QCheckBox("Full spectrum: fit raw data (not smoothed)")
        self.fit_raw_checkbox.setChecked(True)
        self.fit_raw_checkbox.setToolTip(
            "Checked: TMM models are fitted to the measured points.\n"
            "Unchecked: TMM models are fitted to the smoothed curve.\n"
            "Drude-Tinkham always uses the maxima of the smoothed curve."
        )
        sg.addWidget(self.fit_raw_checkbox, 4, 0, 1, 2)

        fit_layout.addWidget(settings)

        # ---------------- Multilayer dielectric stack ----------------
        stack_group = QGroupBox("Layer Stack (front / air side to back / air side)")
        stack_layout = QVBoxLayout(stack_group)

        self.layer_table = QTableWidget(0, 9)
        self.layer_table.setHorizontalHeaderLabels([
            "Layer", "Optical input", "Value", "Fit", "\u00b1%",
            "Thickness", "Unit", "Fit", "\u00b1%"
        ])
        self.layer_table.horizontalHeader().setSectionResizeMode(
            COL_NAME, QHeaderView.Stretch
        )
        for col in range(1, 9):
            self.layer_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents
            )
        self.layer_table.verticalHeader().setVisible(False)
        self.layer_table.setMinimumHeight(260)

        stack_layout.addWidget(self.layer_table, 1)

        stack_buttons = QHBoxLayout()
        add_layer_btn = QPushButton("Add layer")
        remove_layer_btn = QPushButton("Remove selected")
        add_layer_btn.clicked.connect(self.add_layer)
        remove_layer_btn.clicked.connect(self.remove_selected_layer)
        stack_buttons.addWidget(add_layer_btn)
        stack_buttons.addWidget(remove_layer_btn)
        stack_layout.addLayout(stack_buttons)

        sheet_row = QHBoxLayout()
        sheet_row.addWidget(QLabel("2DEG sheet location"))
        self.sheet_location_combo = QComboBox()
        sheet_row.addWidget(self.sheet_location_combo, 1)
        stack_layout.addLayout(sheet_row)

        note = QLabel(
            "2DEG is modeled as an infinitesimally thin Drude sheet G(f); "
            "no 2DEG thickness is required. Tick 'Fit' to let the optimizer "
            "vary a layer's eps_r/n or thickness within \u00b1% of the table value "
            "(full-spectrum models only)."
        )
        note.setWordWrap(True)
        stack_layout.addWidget(note)

        layer_layout.addWidget(stack_group, 1)

        # Default AlGaN/GaN heterostructure. Optical values are editable.
        self.add_layer("GaN cap", "eps_r", 9.5, 3.0, "nm", refresh=False)
        self.add_layer("AlGaN", "eps_r", 9.2, 21.0, "nm", refresh=False)
        self.add_layer("AlN", "eps_r", 8.5, 1.0, "nm", refresh=False)
        self.add_layer("GaN", "eps_r", 9.5, 1.8, "um", refresh=False,
                       fit_thick=True, thick_pct=20.0)
        self.add_layer("Sapphire", "eps_r", 10.0, 430.0, "um", refresh=False,
                       fit_value=True, value_pct=10.0,
                       fit_thick=True, thick_pct=5.0)
        self.refresh_sheet_locations(preferred_after=2)

        # Bounds
        bounds = QGroupBox("Log10 Fit Bounds")
        bg = QGridLayout(bounds)

        self.logg_lo = QDoubleSpinBox()
        self.logg_hi = QDoubleSpinBox()
        self.logt_lo = QDoubleSpinBox()
        self.logt_hi = QDoubleSpinBox()

        for w in [self.logg_lo, self.logg_hi, self.logt_lo, self.logt_hi]:
            w.setRange(-30.0, 10.0)
            w.setDecimals(3)

        self.logg_lo.setValue(-6.0)
        self.logg_hi.setValue(0.0)
        self.logt_lo.setValue(-15.0)
        self.logt_hi.setValue(-10.0)

        bg.addWidget(QLabel("log10(G0) lower"), 0, 0)
        bg.addWidget(self.logg_lo, 0, 1)
        bg.addWidget(QLabel("log10(G0) upper"), 1, 0)
        bg.addWidget(self.logg_hi, 1, 1)
        bg.addWidget(QLabel("log10(tau) lower"), 2, 0)
        bg.addWidget(self.logt_lo, 2, 1)
        bg.addWidget(QLabel("log10(tau) upper"), 3, 0)
        bg.addWidget(self.logt_hi, 3, 1)

        fit_layout.addWidget(bounds)

        # Iterative global + local optimization
        iter_group = QGroupBox("Iterative Fit")
        ig = QGridLayout(iter_group)

        self.global_cb = QCheckBox("Global search first (differential evolution)")
        self.global_cb.setChecked(True)

        self.de_gen_spin = QSpinBox()
        self.de_gen_spin.setRange(5, 5000)
        self.de_gen_spin.setValue(80)

        self.de_points_spin = QSpinBox()
        self.de_points_spin.setRange(200, 100000)
        self.de_points_spin.setValue(3000)

        self.max_rounds_spin = QSpinBox()
        self.max_rounds_spin.setRange(1, 1000)
        self.max_rounds_spin.setValue(25)

        self.rel_tol_spin = QDoubleSpinBox()
        self.rel_tol_spin.setDecimals(10)
        self.rel_tol_spin.setRange(0.0, 1.0)
        self.rel_tol_spin.setValue(1e-6)

        self.target_rmse_spin = QDoubleSpinBox()
        self.target_rmse_spin.setDecimals(6)
        self.target_rmse_spin.setRange(0.0, 10.0)
        self.target_rmse_spin.setValue(0.0)
        self.target_rmse_spin.setToolTip("0 = disabled")

        self.writeback_cb = QCheckBox("Write fitted layer values back to table")
        self.writeback_cb.setChecked(True)

        ig.addWidget(self.global_cb, 0, 0, 1, 2)
        ig.addWidget(QLabel("DE generations"), 1, 0)
        ig.addWidget(self.de_gen_spin, 1, 1)
        ig.addWidget(QLabel("DE max points (decimated)"), 2, 0)
        ig.addWidget(self.de_points_spin, 2, 1)
        ig.addWidget(QLabel("Max refinement rounds"), 3, 0)
        ig.addWidget(self.max_rounds_spin, 3, 1)
        ig.addWidget(QLabel("Stop if rel. SSE change <"), 4, 0)
        ig.addWidget(self.rel_tol_spin, 4, 1)
        ig.addWidget(QLabel("Target RMSE (0 = off)"), 5, 0)
        ig.addWidget(self.target_rmse_spin, 5, 1)
        ig.addWidget(self.writeback_cb, 6, 0, 1, 2)

        fit_layout.addWidget(iter_group)

        run_row = QHBoxLayout()
        self.run_btn = QPushButton("RUN FIT")
        self.run_btn.setMinimumHeight(44)
        self.run_btn.clicked.connect(self.run_fit)
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setMinimumHeight(44)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.request_stop)
        run_row.addWidget(self.run_btn, 3)
        run_row.addWidget(self.stop_btn, 1)
        self.fit_busy = QtWidgets.QProgressBar()
        self.fit_busy.setRange(0, 0)  # moving bar: the fit length is not known in advance
        self.fit_busy.setTextVisible(False)
        self.fit_busy.setMaximumHeight(10)
        self.fit_busy.hide()
        self.fit_status = QLabel("")
        self.fit_status.setStyleSheet("color: #888;")
        stop_key = QtGui.QShortcut(QtGui.QKeySequence("Esc"), self)
        stop_key.setContext(Qt.WidgetWithChildrenShortcut)
        stop_key.activated.connect(lambda: self.stop_btn.isEnabled() and self.request_stop())
        self.stop_btn.setToolTip("Stop the fit and keep the best result so far (Esc)")

        # ---------------- Right side ----------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = MplCanvas()
        self.canvas.setMinimumSize(300, 250)
        right_layout.addWidget(self.canvas, 1)

        # Plot formatting panel
        fmt = QGroupBox("Plot Formatting")
        fg = QGridLayout(fmt)

        self.xlabel_edit = QLineEdit("Frequency [GHz]")
        self.ylabel_edit = QLineEdit("Transmission amplitude")

        self.tick_font_spin = QDoubleSpinBox()
        self.tick_font_spin.setRange(6, 40)
        self.tick_font_spin.setValue(12)

        self.axis_font_spin = QDoubleSpinBox()
        self.axis_font_spin.setRange(6, 40)
        self.axis_font_spin.setValue(12)

        self.linewidth_spin = QDoubleSpinBox()
        self.linewidth_spin.setRange(0.1, 10)
        self.linewidth_spin.setValue(1.0)

        self.spinewidth_spin = QDoubleSpinBox()
        self.spinewidth_spin.setRange(0.1, 5)
        self.spinewidth_spin.setValue(0.8)

        self.xmin_edit = QLineEdit("")
        self.xmax_edit = QLineEdit("")
        self.ymin_edit = QLineEdit("0")
        self.ymax_edit = QLineEdit("1")

        self.major_tick_len = QDoubleSpinBox()
        self.major_tick_len.setRange(0, 20)
        self.major_tick_len.setValue(6)

        self.minor_tick_len = QDoubleSpinBox()
        self.minor_tick_len.setRange(0, 20)
        self.minor_tick_len.setValue(3)

        self.major_tick_w = QDoubleSpinBox()
        self.major_tick_w.setRange(0.1, 5)
        self.major_tick_w.setValue(0.5)

        self.minor_tick_w = QDoubleSpinBox()
        self.minor_tick_w.setRange(0.1, 5)
        self.minor_tick_w.setValue(0.5)

        self.bold_axis_cb = QCheckBox("Bold axis labels")
        self.bold_legend_cb = QCheckBox("Bold legend")
        self.bold_ticks_cb = QCheckBox("Bold tick labels")

        self.legend_loc_combo = QComboBox()
        self.legend_loc_combo.addItems([
            "best", "upper right", "upper left", "lower left",
            "lower right", "right", "center left", "center right",
            "lower center", "upper center", "center"
        ])

        self.save_width_spin = QDoubleSpinBox()
        self.save_width_spin.setRange(1, 30)
        self.save_width_spin.setValue(5)
        self.save_width_spin.setSuffix(" in")

        self.save_height_spin = QDoubleSpinBox()
        self.save_height_spin.setRange(1, 30)
        self.save_height_spin.setValue(4)
        self.save_height_spin.setSuffix(" in")

        self.save_dpi_spin = QDoubleSpinBox()
        self.save_dpi_spin.setRange(72, 2400)
        self.save_dpi_spin.setValue(600)

        rows = [
            ("X Label", self.xlabel_edit),
            ("Y Label", self.ylabel_edit),
            ("Font Size (Ticks)", self.tick_font_spin),
            ("Axis Label Font Size", self.axis_font_spin),
            ("Line Width", self.linewidth_spin),
            ("Spine Width", self.spinewidth_spin),
            ("X Axis Min", self.xmin_edit),
            ("X Axis Max", self.xmax_edit),
            ("Y Axis Min", self.ymin_edit),
            ("Y Axis Max", self.ymax_edit),
            ("Major Tick Length", self.major_tick_len),
            ("Minor Tick Length", self.minor_tick_len),
            ("Major Tick Width", self.major_tick_w),
            ("Minor Tick Width", self.minor_tick_w),
            ("Legend Location", self.legend_loc_combo),
            ("Save Width", self.save_width_spin),
            ("Save Height", self.save_height_spin),
            ("Save DPI", self.save_dpi_spin),
        ]

        for r, (lab, widget) in enumerate(rows):
            fg.addWidget(QLabel(lab), r, 0)
            fg.addWidget(widget, r, 1)

        r = len(rows)
        fg.addWidget(self.bold_axis_cb, r, 0, 1, 2)
        fg.addWidget(self.bold_legend_cb, r + 1, 0, 1, 2)
        fg.addWidget(self.bold_ticks_cb, r + 2, 0, 1, 2)

        plot_btn = QPushButton("Apply Plot Formatting")
        plot_btn.clicked.connect(self.plot_data)
        fg.addWidget(plot_btn, r + 3, 0, 1, 2)

        save_plot_btn = QPushButton("Save Plot")
        save_plot_btn.clicked.connect(self.export_plot)
        fg.addWidget(save_plot_btn, r + 4, 0, 1, 2)

        export_btn = QPushButton("Export Fit Results")
        export_btn.clicked.connect(self.export_results)
        fg.addWidget(export_btn, r + 5, 0, 1, 2)

        tabs = QTabWidget()
        tabs.addTab(self.scrollable(fit_page), "Data && Fit")
        tabs.addTab(self.scrollable(layer_page), "Layer Stack")
        tabs.addTab(self.scrollable(fmt), "Plot Format")

        # Results below the tabs; the vertical splitter lets the user
        # trade space between controls and results.
        self.results = QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setPlaceholderText("Fit results will appear here.")

        results_box = QWidget()
        results_layout = QVBoxLayout(results_box)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.addLayout(run_row)
        results_layout.addWidget(self.fit_busy)
        results_layout.addWidget(self.fit_status)
        results_layout.addWidget(self.results, 1)

        left = QSplitter(Qt.Vertical)
        left.addWidget(tabs)
        left.addWidget(results_box)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # Give the controls their preferred width, but never more than
        # half the window, so the plot always keeps most of the space.
        w = self.width()
        left_w = fit_page.sizeHint().width() + 40
        left_w = int(min(max(left_w, 0.35 * w), 0.5 * w))
        splitter.setSizes([left_w, w - left_w])

    @staticmethod
    def scrollable(widget):
        """Wrap a control page in a scroll area so it never forces the window size."""
        area = QScrollArea()
        area.setWidget(widget)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setMinimumWidth(200)
        return area

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select transmission data",
            "",
            "Data files (*.txt *.csv *.dat);;All files (*)"
        )
        if not path:
            return

        try:
            f, y = load_two_column_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Load error", str(exc))
            return

        self.file_path = path
        self.freq = f
        self.amp = y
        self.file_label.setText(os.path.basename(path))

        self.fit_params = None
        self.fit_curve = None
        self.fit_metrics = None

        self.update_preprocessing()
        self.plot_data()

        self.results.setPlainText(
            f"Loaded: {os.path.basename(path)}\n"
            f"Points: {len(f)}\n"
            f"Frequency range: {f.min():.6g} to {f.max():.6g} GHz\n"
            f"Amplitude range: {y.min():.6g} to {y.max():.6g}"
        )

    def clear_plot(self):
        self.freq = None
        self.amp = None
        self.smooth_amp = None
        self.peak_freq = None
        self.peak_amp = None
        self.fit_curve = None
        self.fit_params = None
        self.fit_metrics = None
        self.file_path = None
        self.file_label.setText("No file loaded")
        self.results.clear()
        self.canvas.ax.clear()
        self.canvas.draw()

    def reprocess_and_plot(self):
        if self.freq is not None:
            self.update_preprocessing()
            self.plot_data()

    def update_preprocessing(self):
        if self.freq is None:
            return

        n = len(self.amp)
        window = choose_savgol_window(n, self.smooth_spin.value())
        polyorder = min(3, window - 1)

        self.smooth_amp = savgol_filter(
            self.amp,
            window_length=window,
            polyorder=polyorder,
            mode="interp"
        )

        peaks, props = find_peaks(
            self.smooth_amp,
            prominence=self.prominence_spin.value()
        )

        self.peak_freq = self.freq[peaks]
        self.peak_amp = self.smooth_amp[peaks]

    def add_layer(
        self, name="New layer", optical_kind="eps_r", value=9.0,
        thickness=1.0, unit="um", refresh=True,
        fit_value=False, value_pct=10.0, fit_thick=False, thick_pct=10.0
    ):
        row = self.layer_table.rowCount()
        self.layer_table.insertRow(row)

        name_edit = QLineEdit(str(name))
        self.layer_table.setCellWidget(row, COL_NAME, name_edit)

        kind_combo = QComboBox()
        kind_combo.addItems(["eps_r", "n"])
        kind_combo.setCurrentText(optical_kind)
        self.layer_table.setCellWidget(row, COL_KIND, kind_combo)

        value_spin = QDoubleSpinBox()
        value_spin.setRange(0.0001, 1000.0)
        value_spin.setDecimals(6)
        value_spin.setValue(float(value))
        self.layer_table.setCellWidget(row, COL_VALUE, value_spin)

        fit_value_cb = QCheckBox()
        fit_value_cb.setChecked(bool(fit_value))
        fit_value_cb.setToolTip("Fit this layer's eps_r / n")
        self.layer_table.setCellWidget(row, COL_FIT_VALUE, fit_value_cb)

        value_pct_spin = QDoubleSpinBox()
        value_pct_spin.setRange(0.01, 99.0)
        value_pct_spin.setDecimals(2)
        value_pct_spin.setValue(float(value_pct))
        value_pct_spin.setToolTip("Fit bound: \u00b1% of the table value")
        self.layer_table.setCellWidget(row, COL_VALUE_PCT, value_pct_spin)

        thickness_spin = QDoubleSpinBox()
        thickness_spin.setRange(0.000001, 1e9)
        thickness_spin.setDecimals(6)
        thickness_spin.setValue(float(thickness))
        self.layer_table.setCellWidget(row, COL_THICK, thickness_spin)

        unit_combo = QComboBox()
        unit_combo.addItems(["nm", "um", "mm"])
        unit_combo.setCurrentText(unit)
        self.layer_table.setCellWidget(row, COL_UNIT, unit_combo)

        fit_thick_cb = QCheckBox()
        fit_thick_cb.setChecked(bool(fit_thick))
        fit_thick_cb.setToolTip("Fit this layer's thickness")
        self.layer_table.setCellWidget(row, COL_FIT_THICK, fit_thick_cb)

        thick_pct_spin = QDoubleSpinBox()
        thick_pct_spin.setRange(0.01, 99.0)
        thick_pct_spin.setDecimals(2)
        thick_pct_spin.setValue(float(thick_pct))
        thick_pct_spin.setToolTip("Fit bound: \u00b1% of the table thickness")
        self.layer_table.setCellWidget(row, COL_THICK_PCT, thick_pct_spin)

        if refresh:
            self.refresh_sheet_locations()

    def remove_selected_layer(self):
        row = self.layer_table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Layer stack", "Select a layer row to remove."
            )
            return
        self.layer_table.removeRow(row)
        self.refresh_sheet_locations()

    def layer_name(self, row):
        w = self.layer_table.cellWidget(row, COL_NAME)
        name = w.text().strip() if w is not None else ""
        return name if name else f"Layer {row + 1}"

    def refresh_sheet_locations(self, preferred_after=None):
        old_after = None
        if hasattr(self, "sheet_location_combo") and self.sheet_location_combo.count():
            old_after = self.sheet_location_combo.currentData()

        self.sheet_location_combo.blockSignals(True)
        self.sheet_location_combo.clear()
        self.sheet_location_combo.addItem("Before first layer", -1)

        for row in range(self.layer_table.rowCount()):
            self.sheet_location_combo.addItem(
                f"After {self.layer_name(row)}", row
            )

        target = preferred_after if preferred_after is not None else old_after
        if target is not None:
            for i in range(self.sheet_location_combo.count()):
                if self.sheet_location_combo.itemData(i) == target:
                    self.sheet_location_combo.setCurrentIndex(i)
                    break

        self.sheet_location_combo.blockSignals(False)

    def get_layer_stack(self):
        """Read the GUI table and return the dielectric stack."""
        layers = []
        tw = self.layer_table.cellWidget

        for row in range(self.layer_table.rowCount()):
            name = self.layer_name(row)
            kind = tw(row, COL_KIND).currentText()
            value = tw(row, COL_VALUE).value()
            thickness = tw(row, COL_THICK).value()
            unit = tw(row, COL_UNIT).currentText()

            n = optical_value_to_n(kind, value)
            d_m = thickness * UNIT_SCALE[unit]

            layers.append({
                "name": name,
                "input_kind": kind,
                "input_value": value,
                "n": n,
                "thickness": thickness,
                "unit": unit,
                "thickness_m": d_m,
                "fit_value": tw(row, COL_FIT_VALUE).isChecked(),
                "value_pct": tw(row, COL_VALUE_PCT).value(),
                "fit_thick": tw(row, COL_FIT_THICK).isChecked(),
                "thick_pct": tw(row, COL_THICK_PCT).value(),
            })

        if not layers:
            raise ValueError("The layer stack is empty. Add at least one dielectric layer.")

        return layers

    def get_sheet_after(self):
        value = self.sheet_location_combo.currentData()
        return int(value) if value is not None else -1

    def tinkham_effective_n(self, layers, sheet_after):
        """
        Choose the dielectric immediately below the 2DEG as the effective
        substrate index for the approximate Tinkham-envelope model.
        """
        next_layer = sheet_after + 1
        if 0 <= next_layer < len(layers):
            return layers[next_layer]["n"]
        return layers[-1]["n"]

    def selected_model(self):
        return self.model_combo.currentText()

    def request_stop(self):
        self.stop_requested = True

    def build_fit_spec(self, layers, mode):
        """
        Build the fit parameter vector:
            [log10(G0), log10(tau), ratio_1, ratio_2, ...]
        Each ratio scales one ticked layer quantity (eps_r/n or thickness)
        relative to its table value, bounded to 1 +/- pct/100.
        Layer quantities are only fitted with the full-spectrum TMM models.
        """
        names = ["log10(G0)", "log10(tau)"]
        lb = [self.logg_lo.value(), self.logt_lo.value()]
        ub = [self.logg_hi.value(), self.logt_hi.value()]
        layer_params = []

        if not mode.startswith("Drude-Tinkham"):
            for i, layer in enumerate(layers):
                if layer["fit_value"]:
                    frac = layer["value_pct"] / 100.0
                    layer_params.append((i, "value"))
                    names.append(f"{layer['name']} {layer['input_kind']}")
                    lb.append(1.0 - frac)
                    ub.append(1.0 + frac)
                if layer["fit_thick"]:
                    frac = layer["thick_pct"] / 100.0
                    layer_params.append((i, "thick"))
                    names.append(f"{layer['name']} thickness")
                    lb.append(1.0 - frac)
                    ub.append(1.0 + frac)

        return names, np.array(lb, dtype=float), np.array(ub, dtype=float), layer_params

    @staticmethod
    def apply_layer_params(layers, layer_params, ratios):
        """Return a copy of the stack with the fitted ratios applied."""
        if not layer_params:
            return layers

        out = [dict(layer) for layer in layers]
        for (i, what), r in zip(layer_params, ratios):
            if what == "value":
                out[i]["input_value"] = layers[i]["input_value"] * r
                out[i]["n"] = optical_value_to_n(
                    out[i]["input_kind"], out[i]["input_value"]
                )
            else:
                out[i]["thickness"] = layers[i]["thickness"] * r
                out[i]["thickness_m"] = layers[i]["thickness_m"] * r
        return out

    def predict(self, freq, p, ctx):
        """Model amplitude for parameter vector p (see build_fit_spec)."""
        g0 = 10.0 ** p[0]
        tau = 10.0 ** p[1]
        layers = self.apply_layer_params(ctx["layers"], ctx["layer_params"], p[2:])
        sheet_after = ctx["sheet_after"]
        mode = ctx["mode"]
        sign = ctx["drude_sign"]

        if mode.startswith("Drude-Tinkham"):
            n_eff = self.tinkham_effective_n(layers, sheet_after)
            y = thin_sheet_transmission(freq, g0, tau, n_eff, sign)

        elif mode.startswith("Full spectrum - normalized"):
            y = normalized_multilayer_transmission(
                freq, g0, tau, layers, sheet_after, sign
            )

        elif mode.startswith("Full spectrum - absolute"):
            y = multilayer_transmission_with_sheet(
                freq, g0, tau, layers, sheet_after, sign
            )

        else:
            raise ValueError("Unknown model mode.")

        if ctx["power"]:
            y = y ** 2

        return np.asarray(y, dtype=float)

    def fit_dataset(self):
        self.update_preprocessing()

        mode = self.selected_model()
        if mode.startswith("Drude-Tinkham"):
            if self.peak_freq is None or len(self.peak_freq) < 3:
                raise ValueError(
                    "Too few maxima were found.\n"
                    "Reduce peak prominence or smoothing and try again."
                )
            return self.peak_freq, self.peak_amp

        # Full-spectrum models are fitted to the raw data or the smoothed curve.
        if self.fit_raw_checkbox.isChecked():
            return self.freq, self.amp
        return self.freq, self.smooth_amp

    def run_fit(self):
        if self.freq is None:
            QMessageBox.warning(self, "No data", "Load a data file first.")
            return

        self.stop_requested = False
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._fit_t0 = time.time()
        self.fit_busy.show()
        self.fit_status.setText("Fitting... (Esc or STOP to stop)")
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            self._run_fit()
        except Exception as exc:
            QMessageBox.critical(self, "Fit error", str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.fit_busy.hide()
            self.fit_status.setText(f"Last fit took {time.time() - self._fit_t0:.1f} s")
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

    def _run_fit(self):
        self.results.setPlainText("Running fit...\nPlease keep this window open.")
        QApplication.processEvents()

        xdata, ydata = self.fit_dataset()
        mode = self.selected_model()
        is_tinkham = mode.startswith("Drude-Tinkham")

        # Cache the dielectric stack once for this fit. Reading Qt widgets
        # inside every residual evaluation is unnecessarily slow.
        layers = self.get_layer_stack()
        names, lb, ub, layer_params = self.build_fit_spec(layers, mode)
        ctx = {
            "mode": mode,
            "layers": layers,
            "layer_params": layer_params,
            "sheet_after": self.get_sheet_after(),
            "power": self.power_checkbox.isChecked(),
            "drude_sign": 1 if self.drude_sign_combo.currentIndex() == 0 else -1,
            "fit_raw": self.fit_raw_checkbox.isChecked(),
        }
        npar = len(lb)

        if np.any(lb >= ub):
            raise ValueError("Each lower bound must be smaller than its upper bound.")

        span = ub - lb

        def clip(p):
            return np.minimum(np.maximum(p, lb + 1e-9 * span), ub - 1e-9 * span)

        # Start at the midpoint of the log bounds and at the table values.
        p_nom = clip(np.concatenate([0.5 * (lb[:2] + ub[:2]), np.ones(npar - 2)]))

        if is_tinkham:
            max_nfev = 4000
            tol = 1e-10
        else:
            max_nfev = 800
            tol = 1e-10

        n_eval = [0]

        def make_residual(x, y):
            def residual(p):
                n_eval[0] += 1
                if n_eval[0] % 25 == 0:
                    self.fit_status.setText(
                        f"Fitting: {n_eval[0]} model evaluations, "
                        f"{time.time() - self._fit_t0:.0f} s  (Esc or STOP to stop)")
                    QApplication.processEvents()
                    if self.stop_requested:
                        raise FitStopped()
                return self.predict(x, p, ctx) - y
            return residual

        residual_full = make_residual(xdata, ydata)

        def sse_of(p):
            r = residual_full(p)
            return float(np.dot(r, r))

        log = []

        def report(msg):
            log.append(msg)
            self.results.setPlainText(
                "Running fit... (press STOP to keep the best result so far)\n\n"
                + "\n".join(log[-40:])
            )
            QApplication.processEvents()

        best = {"p": p_nom, "sse": sse_of(p_nom), "ls": None}

        def consider(p, sse, ls_result=None):
            if sse < best["sse"]:
                best["p"] = np.array(p, dtype=float)
                best["sse"] = sse
                best["ls"] = ls_result

        def rmse_of(sse):
            return np.sqrt(sse / len(ydata))

        report(f"Fitting {npar} parameters to {len(xdata)} points: " + ", ".join(names))
        report(f"Start: RMSE = {rmse_of(best['sse']):.6e}")

        rng = np.random.default_rng()
        stop_reason = "maximum number of rounds reached"
        stopped = False

        try:
            # ---- Stage 1: global search over all parameters ----
            if self.global_cb.isChecked():
                step = max(1, int(np.ceil(len(xdata) / self.de_points_spin.value())))
                xd, yd = xdata[::step], ydata[::step]
                residual_de = make_residual(xd, yd)

                def objective(p):
                    r = residual_de(p)
                    return float(np.dot(r, r))

                gen = [0]

                def de_callback(xk, convergence=None):
                    gen[0] += 1
                    if gen[0] % 5 == 0:
                        rm = np.sqrt(objective(xk) / len(yd))
                        report(f"DE generation {gen[0]}: RMSE (decimated) = {rm:.6e}")
                    return self.stop_requested

                report(f"Global search: differential evolution on {len(xd)} points...")
                de = differential_evolution(
                    objective,
                    list(zip(lb, ub)),
                    maxiter=self.de_gen_spin.value(),
                    popsize=12,
                    tol=1e-8,
                    mutation=(0.5, 1.0),
                    recombination=0.7,
                    polish=False,
                    init="latinhypercube",
                    x0=p_nom,
                    rng=rng,
                    callback=de_callback,
                )
                consider(clip(de.x), sse_of(clip(de.x)))
                report(f"Global search done: RMSE = {rmse_of(best['sse']):.6e}")

                if self.stop_requested:
                    raise FitStopped()

            # ---- Stage 2: repeated local refinement until converged ----
            target = self.target_rmse_spin.value()
            rel_tol = self.rel_tol_spin.value()
            prev_sse = best["sse"]
            quiet_rounds = 0

            for k in range(1, self.max_rounds_spin.value() + 1):
                starts = [best["p"]]

                if k == 1 and not self.global_cb.isChecked():
                    for g, t in ((-3.0, -12.75), (-2.5, -13.0), (-3.5, -13.0),
                                 (-3.0, -13.5), (-3.0, -12.0)):
                        s = best["p"].copy()
                        s[:2] = (g, t)
                        starts.append(clip(s))

                # Random restarts around the current best; the spread
                # shrinks each round to settle into the best basin.
                spread = 0.15 * (0.7 ** (k - 1)) * span
                for _ in range(2):
                    starts.append(clip(best["p"] + rng.normal(size=npar) * spread))

                for s in starts:
                    result = least_squares(
                        residual_full,
                        s,
                        bounds=(lb, ub),
                        x_scale="jac",
                        max_nfev=max_nfev,
                        xtol=tol,
                        ftol=tol,
                        gtol=tol,
                    )
                    consider(result.x, float(np.dot(result.fun, result.fun)), result)

                rel = (prev_sse - best["sse"]) / max(prev_sse, 1e-300)
                report(
                    f"Round {k}: RMSE = {rmse_of(best['sse']):.6e}, "
                    f"rel. SSE change = {rel:.3e}"
                )

                if target > 0 and rmse_of(best["sse"]) <= target:
                    stop_reason = f"target RMSE {target:g} reached"
                    break

                quiet_rounds = quiet_rounds + 1 if rel < rel_tol else 0
                if quiet_rounds >= 2:
                    stop_reason = "converged (SSE stopped decreasing)"
                    break

                prev_sse = best["sse"]

        except FitStopped:
            stopped = True
            stop_reason = "stopped by user (best result so far)"

        self.finish_fit(xdata, ydata, ctx, names, lb, ub, best, stop_reason, stopped, log)

    def finish_fit(self, xdata, ydata, ctx, names, lb, ub, best, stop_reason, stopped, log):
        p = best["p"]
        npar = len(p)
        layer_params = ctx["layer_params"]
        layer_ratios = p[2:]

        log_g0, log_tau = p[0], p[1]
        g0 = 10.0 ** log_g0
        tau = 10.0 ** log_tau

        m_eff = self.meff_spin.value() * M0
        mu_m2 = Q * tau / m_eff
        mu_cm2 = mu_m2 * 1e4

        # G0 [S/square] = q * ns[cm^-2] * mu[cm^2/Vs]
        ns_cm2 = g0 / (Q * mu_cm2)

        r_sheet = 1.0 / g0

        pred_fit = self.predict(xdata, p, ctx)
        residuals = ydata - pred_fit

        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((ydata - np.mean(ydata)) ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
        rmse = np.sqrt(np.mean(residuals ** 2))

        # 1-sigma uncertainties from the Jacobian at the best point.
        sigma = np.full(npar, np.nan)
        corr = None
        ls = best["ls"]
        if ls is not None and len(xdata) > npar:
            J = ls.jac
            cov = np.linalg.pinv(J.T @ J) * ss_res / (len(xdata) - npar)
            sigma = np.sqrt(np.abs(np.diag(cov)))
            with np.errstate(invalid="ignore", divide="ignore"):
                corr = cov / np.outer(sigma, sigma)

        fitted_layers = self.apply_layer_params(ctx["layers"], layer_params, layer_ratios)

        self.fit_params = {
            "log10_G0": log_g0,
            "log10_tau": log_tau,
            "G0_S_per_sq": g0,
            "tau_s": tau,
            "tau_ps": tau * 1e12,
            "mobility_cm2_Vs": mu_cm2,
            "ns_cm2": ns_cm2,
            "Rsheet_ohm_sq": r_sheet,
        }
        for (i, what), r in zip(layer_params, layer_ratios):
            lay = fitted_layers[i]
            if what == "value":
                self.fit_params[f"{lay['name']}_{lay['input_kind']}"] = lay["input_value"]
            else:
                self.fit_params[f"{lay['name']}_thickness_{lay['unit']}"] = lay["thickness"]

        self.fit_metrics = {
            "RMSE": rmse,
            "R2": r2,
            "SSE": ss_res,
            "n_points": len(xdata),
            "success": not stopped,
            "message": stop_reason,
        }

        self.fit_curve = self.predict(self.freq, p, ctx)

        tol_b = 0.01 * (ub - lb)
        at_bound = (np.abs(p - lb) < tol_b) | (np.abs(ub - p) < tol_b)

        ln10 = np.log(10.0)
        lines = [
            "FIT RESULTS",
            "-----------",
            f"Model: {ctx['mode']}",
            "Drude term: G0/(1 {} i*w*tau){}".format(
                "+" if ctx["drude_sign"] > 0 else "-",
                "" if ctx["drude_sign"] > 0
                else "  [DIAGNOSTIC: wrong sign for these matrices]",
            ),
            f"Stop reason: {stop_reason}",
            f"G0 = {g0:.6e} S/square  (\u00b1{ln10 * sigma[0] * 100:.3g} %)",
            f"Rsheet = {r_sheet:.6f} ohm/square",
            f"tau = {tau:.6e} s  (\u00b1{ln10 * sigma[1] * 100:.3g} %)",
            f"tau = {tau*1e12:.6f} ps",
            f"mobility = {mu_cm2:.6f} cm^2/(V s)",
            f"ns = {ns_cm2:.6e} cm^-2",
        ]

        if layer_params:
            lines += ["", "FITTED LAYER PARAMETERS (\u00b11 sigma)", "-----------"]
            for k, ((i, what), r) in enumerate(zip(layer_params, layer_ratios), start=2):
                lay0 = ctx["layers"][i]
                lay = fitted_layers[i]
                if what == "value":
                    v0, v, unit = lay0["input_value"], lay["input_value"], ""
                    label = f"{lay['name']} {lay['input_kind']}"
                else:
                    v0, v, unit = lay0["thickness"], lay["thickness"], " " + lay["unit"]
                    label = f"{lay['name']} thickness"
                flag = "  [AT BOUND]" if at_bound[k] else ""
                lines.append(
                    f"{label} = {v:.6g} \u00b1 {v0 * sigma[k]:.3g}{unit} "
                    f"(start {v0:.6g}{unit}, {100 * (r - 1):+.3f} %){flag}"
                )

        lines += ["", "LAYER STACK (fitted)", "-----------"]

        sheet_after_now = ctx["sheet_after"]
        if sheet_after_now == -1:
            lines.append("[2DEG Drude sheet before first layer]")
        fitted_rows = {i: [] for i, _ in layer_params}
        for i, what in layer_params:
            fitted_rows[i].append(what)
        for i, layer in enumerate(fitted_layers):
            tag = f"  (fit: {', '.join(fitted_rows[i])})" if i in fitted_rows else ""
            lines.append(
                f"{i+1}. {layer['name']}: "
                f"{layer['input_kind']}={layer['input_value']:.6g}, "
                f"n={layer['n'].real:.6g}, "
                f"d={layer['thickness']:.6g} {layer['unit']}{tag}"
            )
            if sheet_after_now == i:
                lines.append("   [2DEG Drude sheet here]")

        lines += [
            "",
            "FIT QUALITY",
            "-----------",
            f"RMSE = {rmse:.6e}",
            f"R^2 = {r2:.8f}",
            f"SSE = {ss_res:.6e}",
            f"points fitted = {len(xdata)}",
            "data fitted = {}".format(
                "detected maxima" if ctx["mode"].startswith("Drude-Tinkham")
                else "raw data" if ctx["fit_raw"] else "smoothed curve"
            ),
            f"parameters fitted = {npar}",
        ]

        if np.any(at_bound):
            lines += [
                "",
                "WARNING:",
                "Parameters close to a bound: "
                + ", ".join(n for n, b in zip(names, at_bound) if b),
                "Widen the bounds (log10 bounds or layer \u00b1%) and refit.",
            ]

        if corr is not None:
            pairs = [
                f"{names[a]} / {names[b]} ({corr[a, b]:+.3f})"
                for a in range(npar) for b in range(a + 1, npar)
                if np.isfinite(corr[a, b]) and abs(corr[a, b]) > 0.95
            ]
            if pairs:
                lines += [
                    "",
                    "WARNING: strongly correlated parameters (|r| > 0.95):",
                    *pairs,
                    "The data cannot separate these well; consider fixing one.",
                ]

        if ctx["mode"].startswith("Drude-Tinkham"):
            lines += [
                "",
                f"Detected maxima = {len(self.peak_freq)}",
                "Peak fitting reduces Fabry-Perot influence, but tau may",
                "still be weakly constrained by amplitude-only data.",
            ]
            if any(l["fit_value"] or l["fit_thick"] for l in ctx["layers"]):
                lines.append(
                    "Layer parameters are not fitted with this model; "
                    "use a full-spectrum TMM model."
                )

        lines += ["", "ITERATION LOG", "-----------", *log]

        if self.writeback_cb.isChecked() and layer_params:
            tw = self.layer_table.cellWidget
            for i, what in layer_params:
                lay = fitted_layers[i]
                if what == "value":
                    tw(i, COL_VALUE).setValue(float(lay["input_value"]))
                else:
                    tw(i, COL_THICK).setValue(float(lay["thickness"]))
            lines.insert(4, "Fitted layer values were written back to the table.")

        self.results.setPlainText("\n".join(lines))
        self.plot_data()

    def _parse_optional_float(self, widget):
        txt = widget.text().strip()
        if txt == "":
            return None
        return float(txt)

    def plot_data(self):
        ax = self.canvas.ax
        ax.clear()

        if self.freq is None:
            ax.set_xlabel("Frequency [GHz]")
            ax.set_ylabel("Transmission")
            self.canvas.draw()
            return

        x = self.freq * self.xscale_spin.value()
        raw = self.amp.copy()
        smooth = self.smooth_amp.copy() if self.smooth_amp is not None else None
        fit = self.fit_curve.copy() if self.fit_curve is not None else None
        peaks_y = self.peak_amp.copy() if self.peak_amp is not None else None

        if self.square_y_cb.isChecked():
            raw = raw ** 2
            if smooth is not None:
                smooth = smooth ** 2
            if fit is not None:
                fit = fit ** 2
            if peaks_y is not None:
                peaks_y = peaks_y ** 2

        lw = self.linewidth_spin.value() if hasattr(self, "linewidth_spin") else 1.0

        if self.show_raw_cb.isChecked():
            ax.plot(x, raw, linewidth=lw, label="Experimental")

        if self.show_smooth_cb.isChecked() and smooth is not None:
            ax.plot(x, smooth, linewidth=lw, label="Smoothed")

        if self.show_peaks_cb.isChecked() and self.peak_freq is not None and len(self.peak_freq):
            ax.plot(
                self.peak_freq * self.xscale_spin.value(),
                peaks_y,
                "o",
                markersize=4,
                label="Maxima"
            )

        if self.show_fit_cb.isChecked() and fit is not None:
            ax.plot(x, fit, linewidth=max(lw, 1.2), linestyle="--", label="Fit")

        xlabel = self.xlabel_edit.text() if hasattr(self, "xlabel_edit") else "Frequency [GHz]"
        ylabel = self.ylabel_edit.text() if hasattr(self, "ylabel_edit") else "Transmission amplitude"
        axis_fs = self.axis_font_spin.value() if hasattr(self, "axis_font_spin") else 12
        tick_fs = self.tick_font_spin.value() if hasattr(self, "tick_font_spin") else 12

        axis_weight = "bold" if hasattr(self, "bold_axis_cb") and self.bold_axis_cb.isChecked() else "normal"
        ax.set_xlabel(xlabel, fontsize=axis_fs, fontweight=axis_weight)
        ax.set_ylabel(ylabel, fontsize=axis_fs, fontweight=axis_weight)

        try:
            xmin = self._parse_optional_float(self.xmin_edit)
            xmax = self._parse_optional_float(self.xmax_edit)
            ymin = self._parse_optional_float(self.ymin_edit)
            ymax = self._parse_optional_float(self.ymax_edit)

            if xmin is not None or xmax is not None:
                ax.set_xlim(left=xmin, right=xmax)
            if ymin is not None or ymax is not None:
                ax.set_ylim(bottom=ymin, top=ymax)
        except ValueError:
            pass

        ax.minorticks_on()

        major_len = self.major_tick_len.value() if hasattr(self, "major_tick_len") else 6
        minor_len = self.minor_tick_len.value() if hasattr(self, "minor_tick_len") else 3
        major_w = self.major_tick_w.value() if hasattr(self, "major_tick_w") else 0.5
        minor_w = self.minor_tick_w.value() if hasattr(self, "minor_tick_w") else 0.5

        ax.tick_params(
            direction="in", which="major",
            labelsize=tick_fs, length=major_len, width=major_w
        )
        ax.tick_params(
            direction="in", which="minor",
            length=minor_len, width=minor_w
        )

        spine_w = self.spinewidth_spin.value() if hasattr(self, "spinewidth_spin") else 0.8
        for spine in ax.spines.values():
            spine.set_linewidth(spine_w)

        if hasattr(self, "bold_ticks_cb") and self.bold_ticks_cb.isChecked():
            for tick in ax.get_xticklabels() + ax.get_yticklabels():
                tick.set_fontweight("bold")

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            loc = self.legend_loc_combo.currentText() if hasattr(self, "legend_loc_combo") else "best"
            leg = ax.legend(frameon=False, loc=loc)
            if hasattr(self, "bold_legend_cb") and self.bold_legend_cb.isChecked():
                for txt in leg.get_texts():
                    txt.set_fontweight("bold")

        self.canvas.draw()

    def export_results(self):
        if self.fit_params is None:
            QMessageBox.warning(self, "No fit", "Run a fit first.")
            return

        default = "drude_fit_results.txt"
        if self.file_path:
            stem = os.path.splitext(os.path.basename(self.file_path))[0]
            default = stem + "_drude_fit_results.txt"

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export fit results",
            default,
            "Text files (*.txt);;CSV files (*.csv);;All files (*)"
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.results.toPlainText())
                f.write("\n\n")
                f.write("FREQUENCY_GHz\tEXPERIMENT\tSMOOTHED\tFIT\n")

                fit = self.fit_curve if self.fit_curve is not None else np.full_like(self.freq, np.nan)
                smooth = self.smooth_amp if self.smooth_amp is not None else np.full_like(self.freq, np.nan)

                for ff, yy, ss, mm in zip(self.freq, self.amp, smooth, fit):
                    f.write(f"{ff:.10e}\t{yy:.10e}\t{ss:.10e}\t{mm:.10e}\n")

            QMessageBox.information(self, "Export complete", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export error", str(exc))

    def export_plot(self):
        if self.freq is None:
            QMessageBox.warning(self, "No data", "Load data first.")
            return

        default = "drude_fit_plot.png"
        if self.file_path:
            stem = os.path.splitext(os.path.basename(self.file_path))[0]
            default = stem + "_drude_fit.png"

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export plot",
            default,
            "PNG image (*.png);;PDF (*.pdf);;SVG (*.svg)"
        )
        if not path:
            return

        try:
            old_size = self.canvas.fig.get_size_inches().copy()
            self.canvas.fig.set_size_inches(self.save_width_spin.value(), self.save_height_spin.value())
            self.canvas.fig.savefig(path, dpi=int(self.save_dpi_spin.value()), bbox_inches="tight")
            self.canvas.fig.set_size_inches(old_size)
            QMessageBox.information(self, "Export complete", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export error", str(exc))


# ---------------------------------------------------------------- two-mirror beam waist
# Gaussian beam formulas from Kasap, Optoelectronics and Photonics: Principles and
# Practices, Sec. 1.1: Eqs. (1.1.7)-(1.1.11), and R(z) from Problem 1.5.
# All lengths in mm. z = distance past the waist (negative = waist still ahead).
C_MM_PER_S = 299792458e3  # speed of light in mm/s


def rayleigh_range(w0, lam):
    """Eq. (1.1.8): zo = pi wo^2 / lambda."""
    return np.pi * w0 ** 2 / lam


def beam_radius(w0, z, lam):
    """Eq. (1.1.9a): 2w = 2wo [1 + (z/zo)^2]^(1/2), returned as the radius w."""
    return w0 * np.sqrt(1.0 + (z / rayleigh_range(w0, lam)) ** 2)


def wavefront_curvature(w0, z, lam):
    """1/R from Problem 1.5: R(z) = z [1 + (zo/z)^2], so 1/R = z / (z^2 + zo^2).
    Written as 1/R so the flat wavefront at the waist (R = infinity) gives 0."""
    z0 = rayleigh_range(w0, lam)
    return z / (z ** 2 + z0 ** 2)


def divergence(w0, lam):
    """Eq. (1.1.7): full far-field divergence 2theta = 4 lambda / (pi 2wo), in rad."""
    return 4 * lam / (np.pi * 2 * w0)


def waist_from_w_and_R(w, inv_R, lam):
    """Waist radius wo and position z for a beam of radius w and curvature 1/R at a plane.
    Solves Eqs. (1.1.8), (1.1.9a) and R(z) for wo and z (with b = pi w^2 / lambda):
        z  = (1/R) / ((1/R)^2 + 1/b^2)
        zo = (1/b) / ((1/R)^2 + 1/b^2),   wo = (lambda zo / pi)^(1/2)."""
    b = np.pi * w ** 2 / lam
    den = inv_R ** 2 + 1.0 / b ** 2
    z = inv_R / den
    z0 = (1.0 / b) / den
    return np.sqrt(lam * z0 / np.pi), z


def mirror(w, inv_R, f):
    """Focusing mirror (thin-lens equivalent, focal length f): w is unchanged and the
    wavefront curvature drops by 1/f. This step is not one of Kasap's equations; it is
    the paraxial focusing rule (Kasap Problem 1.5 matches wavefront and mirror curvature)."""
    return w, inv_R - 1.0 / f


# ---------------------------------------------------------------- the system
def solve_two_mirrors(lam, w_in, d_in, f1, f2, d, m2=1.0):
    """Propagate the input waist through M1 and M2 and locate every waist.
    m2 = beam quality M^2 (Eq. 1.1.11): the real beam follows the formulas with M^2 lambda."""
    lam = m2 * lam

    # Input waist to M1, reflect
    w_m1 = beam_radius(w_in, d_in, lam)
    _, c1 = mirror(w_m1, wavefront_curvature(w_in, d_in, lam), f1)
    w01, z1 = waist_from_w_and_R(w_m1, c1, lam)

    # M1 to M2, reflect
    w_m2 = beam_radius(w01, z1 + d, lam)
    _, c2 = mirror(w_m2, wavefront_curvature(w01, z1 + d, lam), f2)
    w02, z2 = waist_from_w_and_R(w_m2, c2, lam)

    return {
        "lambda_mm": lam / m2,
        "w_on_M1": w_m1,
        "w_on_M2": w_m2,
        # waist formed by M1 (measured from M1; between the mirrors if 0 < x < d)
        "waist1_w0": w01, "waist1_pos_from_M1": -z1, "waist1_z0": rayleigh_range(w01, lam),
        # waist formed by M2 (measured from M2; negative = virtual waist)
        "waist2_w0": w02, "waist2_pos_from_M2": -z2, "waist2_z0": rayleigh_range(w02, lam),
        "waist2_divergence_rad": divergence(w02, lam),
    }


def beam_profile(lam, w_in, d_in, f1, f2, d, d_out, n=800, m2=1.0):
    """Beam radius w(z) along the unfolded path from Eq. (1.1.9a), z = 0 at the input waist."""
    r = solve_two_mirrors(lam, w_in, d_in, f1, f2, d, m2)
    lam = m2 * lam
    z_m1, z_m2 = d_in, d_in + d
    z = np.linspace(0.0, z_m2 + d_out, n)
    w = np.where(z <= z_m1, beam_radius(w_in, z, lam),
                 np.where(z <= z_m2,
                          beam_radius(r["waist1_w0"], z - z_m1 - r["waist1_pos_from_M1"], lam),
                          beam_radius(r["waist2_w0"], z - z_m2 - r["waist2_pos_from_M2"], lam)))
    return z, w, z_m1, z_m2


# Hover help for the Beam Waist tab (rich text so Qt wraps long tooltips)
BW_TIPS = {
    "f1": "<p><b>f1: focal length of mirror M1 (mm)</b></p>"
          "<p>For an off-axis parabolic mirror, enter the <i>reflected</i> (effective) focal length "
          "from the datasheet, e.g. 101.6 mm for a 4-inch OAP.</p>"
          "<p>Effect: M1 converges the collimated beam to the waist between the mirrors, close to "
          "f1 from M1. Waist radius w0 ≈ λ·f1 / (π·w), w = beam radius on M1. A shorter f1 gives "
          "a smaller waist but a shorter depth of focus.</p>",
    "f2": "<p><b>f2: focal length of mirror M2 (mm)</b></p>"
          "<p>Reflected (effective) focal length of M2, the mirror that collects the beam.</p>"
          "<p>Effect: f2 does not change the waist between the mirrors. With d = f1 + f2, M2 "
          "turns the beam back into a collimated beam.</p>",
    "d": "<p><b>d: distance between M1 and M2 (mm)</b></p>"
         "<p>Measured along the beam path, mirror centre to mirror centre.</p>"
         "<p>Effect: d does not change the waist (M1 sets it). It sets how far the beam spreads "
         "again before M2, so it decides whether M2 collects all of it (see \"% collected by "
         "M2\").</p>",
    "d_relay": "<p>Set d = f1 + f2: the waist sits at the focus of both mirrors, so M2 "
               "collects the beam and sends it out collimated.</p>",
    "D": "<p><b>D: mirror diameter (mm)</b></p>"
         "<p>Clear aperture of the mirrors (the same D is used for M1 and M2), e.g. 50.8 mm for a "
         "2-inch mirror.</p>"
         "<p>Effect: D does not change the waist. It is used to check clipping: the results show "
         "the fraction of beam power that lands on each mirror, and the top plot draws the "
         "mirrors at this size.</p>",
    "beam_d": "<p><b>Collimated beam diameter on M1 (mm), at 1/e² intensity</b></p>"
              "<p>Diameter 2w of the collimated beam arriving at M1. If you only know that the "
              "beam fills the mirror, click \"= D\". If you measured the beam, enter the 1/e² "
              "diameter (= 1.7 × the FWHM diameter).</p>"
              "<p>Effect: the beam is collimated, so its waist is on M1 (flat wavefront). A wider "
              "beam focuses to a smaller waist after M1: w0 ≈ λ·f1 / (π·w).</p>",
    "beam_d_btn": "<p>Set the beam diameter equal to the mirror diameter D (beam fills the "
                  "mirror). With 2w = D about 86% of the power lands on the mirror.</p>",
    "m2": "<p><b>M²: beam quality factor</b></p>"
          "<p>1 = ideal Gaussian beam. Real beams have M² > 1 (Kasap Eq. 1.1.10). Use the value "
          "from the source datasheet or from a beam-profile measurement.</p>"
          "<p>Effect: every formula uses M²·λ in place of λ, so focused waists and divergence "
          "both grow by M² (Eq. 1.1.11).</p>",
    "freqs": "<p><b>Frequencies to trace (THz)</b></p>"
             "<p>One or more values separated by commas, e.g. 0.5, 1.0, 1.5. Each one is a curve in "
             "the top plot and a block in the results text. λ = c / f.</p>"
             "<p>Effect: a lower frequency has a longer wavelength, so it gives larger focused "
             "waists and more divergence.</p>",
    "sweep": "<p><b>Frequency range for the bottom plot (THz)</b></p>"
             "<p>The bottom plot shows the waist after M1, the waist after M2 and the position of "
             "the M2 waist across this range.</p>",
    "calc": "<p>Recalculate the results and plots with the values above.</p>",
    "out": "<p><b>Results for each frequency</b></p>"
           "<p><b>beam on M1 / M2</b>: beam radius w (1/e²) on the mirror and the share of power "
           "that lands on it (mirror diameter D).</p>"
           "<p><b>WAIST between mirrors</b>: the focus made by M1. <b>radius w0</b> and "
           "<b>diameter 2w0</b> at 1/e² intensity; <b>FWHM</b> = w0·√(2 ln 2) is the intensity "
           "full width at half maximum. <b>position</b> = distance from M1 along the beam.</p>"
           "<p><b>Rayleigh range z0</b> = π·w0²/λ (Kasap Eq. 1.1.8): within ±z0 of the waist the "
           "beam stays narrower than √2·w0. A sample placed in this depth of focus (2z0) sees "
           "nearly the waist size.</p>"
           "<p><b>full angle 2θ</b> = 4λ / (π·2w0) (Kasap Eq. 1.1.7): how fast the beam spreads "
           "after the focus on its way to M2. A smaller focus spreads faster.</p>",
}


class BeamWaistTab(QWidget):
    """Gaussian beam waist for two parabolic mirrors facing each other.
    Gaussian beam formulas from Kasap, Optoelectronics and Photonics, Sec. 1.1."""

    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        box = QGroupBox("Mirror system (lengths in mm)")
        form = QFormLayout(box)

        def spin(value, lo=0.001, hi=1e5, dec=3):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setValue(value)
            s.setKeyboardTracking(False)
            return s

        self.f1 = spin(101.6)
        self.f2 = spin(101.6)
        self.d = spin(203.2)
        self.D = spin(50.8)
        self.beam_d = spin(50.8)
        self.m2 = spin(1.0, lo=1.0, hi=100.0, dec=2)
        form.addRow("f1, effective focal length M1:", self.f1)
        form.addRow("f2, effective focal length M2:", self.f2)
        form.addRow("d, mirror separation:", self.d)
        b = QPushButton("d = f1 + f2")
        b.clicked.connect(lambda: self.d.setValue(self.f1.value() + self.f2.value()))
        b.setToolTip(BW_TIPS["d_relay"])
        form.addRow("", b)
        form.addRow("D, mirror diameter:", self.D)
        b = QPushButton("= D")
        b.setToolTip(BW_TIPS["beam_d_btn"])
        b.clicked.connect(lambda: self.beam_d.setValue(self.D.value()))
        brow = QHBoxLayout()
        brow.addWidget(self.beam_d)
        brow.addWidget(b)
        form.addRow("2w, beam diameter on M1:", brow)
        form.addRow("M², beam quality (1 = ideal Gaussian):", self.m2)
        left.addWidget(box)
        for w, key in ((self.f1, "f1"), (self.f2, "f2"), (self.d, "d"), (self.D, "D"),
                       (self.m2, "m2")):
            w.setToolTip(BW_TIPS[key])
            form.labelForField(w).setToolTip(BW_TIPS[key])
        self.beam_d.setToolTip(BW_TIPS["beam_d"])
        form.labelForField(brow).setToolTip(BW_TIPS["beam_d"])

        fbox = QGroupBox("Frequency (THz)")
        fform = QFormLayout(fbox)
        self.freqs = QLineEdit("0.5, 1.0, 1.5, 2.0")
        fform.addRow("Frequencies to trace:", self.freqs)
        self.sweep_lo = spin(0.1, dec=2)
        self.sweep_hi = spin(3.0, dec=2)
        fform.addRow("Sweep from:", self.sweep_lo)
        fform.addRow("Sweep to:", self.sweep_hi)
        left.addWidget(fbox)
        for w, key in ((self.freqs, "freqs"), (self.sweep_lo, "sweep"), (self.sweep_hi, "sweep")):
            w.setToolTip(BW_TIPS[key])
            fform.labelForField(w).setToolTip(BW_TIPS[key])

        btn = QPushButton("Calculate")
        btn.clicked.connect(self.calculate)
        btn.setToolTip(BW_TIPS["calc"])
        left.addWidget(btn)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.out.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.out.setToolTip(BW_TIPS["out"])
        left.addWidget(self.out, 1)
        note = QLabel("Each mirror is treated as a thin lens (paraxial). For an off-axis parabolic "
                      "mirror enter the reflected focal length. Astigmatism is not included.")
        note.setWordWrap(True)
        left.addWidget(note)

        lw = QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(460)
        layout.addWidget(lw)

        right = QVBoxLayout()
        self.fig = Figure(figsize=(8, 8))
        self.canvas = FigureCanvasQTAgg(self.fig)
        right.addWidget(NavigationToolbar2QT(self.canvas, self))
        right.addWidget(self.canvas, 1)
        layout.addLayout(right, 1)

        self.calculate()

    def calculate(self):
        try:
            freqs = [float(x) for x in re.split(r"[,\s;]+", self.freqs.text().strip()) if x]
            if not freqs or min(freqs) <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Beam waist", "Enter one or more positive frequencies in THz.")
            return
        f1, f2, d = self.f1.value(), self.f2.value(), self.d.value()
        D = self.D.value()
        # Collimated input: flat wavefront at M1, so the input waist sits on M1
        w_in, d_in = self.beam_d.value() / 2, 0.0
        m2 = self.m2.value()

        def captured(w):
            """Fraction of a Gaussian beam's power inside a mirror of diameter D."""
            return 1 - np.exp(-2 * (D / 2) ** 2 / w ** 2)

        lines = [f"f1 = {f1} mm, f2 = {f2} mm, d = {d} mm, mirror D = {D} mm",
                 f"collimated beam on M1: 1/e² diameter {2 * w_in} mm, M² = {m2:g}"]
        results = []
        for fr in freqs:
            lam = C_MM_PER_S / (fr * 1e12)
            r = solve_two_mirrors(lam, w_in, d_in, f1, f2, d, m2)
            results.append((fr, lam, r))
            w0, x1, z0 = r["waist1_w0"], r["waist1_pos_from_M1"], r["waist1_z0"]
            lines += ["",
                      f"{fr:g} THz  (lambda = {lam * 1e3:.1f} um)",
                      f"  beam on M1: w = {r['w_on_M1']:.3f} mm "
                      f"({100 * captured(r['w_on_M1']):.1f}% of power on M1)",
                      "  WAIST between mirrors:",
                      f"    radius w0          = {w0:.3f} mm",
                      f"    1/e² diameter 2w0  = {2 * w0:.3f} mm",
                      f"    FWHM               = {w0 * np.sqrt(2 * np.log(2)):.3f} mm",
                      f"    position           = {x1:.2f} mm from M1 ({d - x1:.2f} mm before M2)",
                      f"    Rayleigh range z0  = {z0:.2f} mm (depth of focus 2z0 = {2 * z0:.2f} mm)",
                      "  after the focus the beam diverges to M2:",
                      f"    full angle 2θ      = {np.degrees(divergence(w0, m2 * lam)):.2f}° "
                      f"({divergence(w0, m2 * lam) * 1e3:.1f} mrad)",
                      f"  beam on M2: w = {r['w_on_M2']:.3f} mm "
                      f"({100 * captured(r['w_on_M2']):.1f}% collected by M2)"]
            if not 0 < x1 < d:
                lines.append("  WARNING: this waist is not between the mirrors")
        self.out.setPlainText("\n".join(lines))

        self.fig.clear()
        ax1 = self.fig.add_subplot(211)
        ax2 = self.fig.add_subplot(212)
        x_focus = max(r["waist1_pos_from_M1"] for _, _, r in results)
        for fr, lam, r in results:
            z, w, z_m1, z_m2 = beam_profile(lam, w_in, d_in, f1, f2, d, 0.0, m2=m2)
            line, = ax1.plot(z, w, label=f"{fr:g} THz: focus 2w0 = {2 * r['waist1_w0']:.2f} mm "
                                         f"at {r['waist1_pos_from_M1']:.1f} mm")
            ax1.plot(z, -w, color=line.get_color())
        if x_focus < d:
            ax1.axvline(x_focus, color="0.5", ls=":", lw=1)
            ax1.text(x_focus, -D / 2, " focus", color="0.4", va="bottom")
        else:
            # M2 sits before the focus: the beam reaches M2 still converging
            ax1.text(0.5 * d, 0,
                     f"M2 at d = {d:g} mm is closer than the focus of M1 ({x_focus:.1f} mm).\n"
                     f"The beam reaches M2 before it focuses.\n"
                     f"For a focus between the mirrors d must be more than {x_focus:.1f} mm;\n"
                     f"d = f1 + f2 = {f1 + f2:g} mm lets M2 collect and recollimate the beam.",
                     color="red", ha="center", va="center", fontsize=10,
                     bbox=dict(facecolor="white", edgecolor="red"))
        # Mirrors drawn at their real size, so clipping is visible
        for zm, name in ((z_m1, "M1"), (z_m2, "M2")):
            ax1.plot([zm, zm], [-D / 2, D / 2], color="k", lw=4, solid_capstyle="butt")
            ax1.text(zm, D / 2, f" {name}", va="bottom")
        ax1.set_xlabel("distance from M1 (mm)")
        ax1.set_ylabel("beam radius w (mm)")
        ax1.set_title(f"Beam from M1: f1 = {f1} mm, M2 at d = {d} mm")
        ax1.legend(loc="upper center")

        lo, hi = sorted((self.sweep_lo.value(), self.sweep_hi.value()))
        fs = np.linspace(lo, hi, 300)
        diam, pos = [], []
        for fr in fs:
            r = solve_two_mirrors(C_MM_PER_S / (fr * 1e12), w_in, d_in, f1, f2, d, m2)
            diam.append(2 * r["waist1_w0"]); pos.append(r["waist1_pos_from_M1"])
        ax2.plot(fs, diam, label="waist diameter 2w0")
        ax2.set_xlabel("frequency (THz)")
        ax2.set_ylabel("waist diameter 2w0 (mm)")
        ax2.set_title("Focus (waist) formed by M1 vs frequency")
        ax2b = ax2.twinx()
        ax2b.plot(fs, pos, "k:", label="waist distance from M1")
        ax2b.set_ylabel("waist distance from M1 (mm)")
        # A nearly constant position would otherwise show rounding noise
        ax2b.ticklabel_format(axis="y", useOffset=False)
        mid = float(np.mean(pos))
        if np.ptp(pos) < 1e-6 * max(1.0, abs(mid)):
            ax2b.set_ylim(mid - 1, mid + 1)
        h1, l1 = ax2.get_legend_handles_labels()
        h2, l2 = ax2b.get_legend_handles_labels()
        ax2.legend(h1 + h2, l1 + l2, loc="upper right")
        self.fig.tight_layout()
        self.canvas.draw_idle()

# =====================================================================
# S11 plot tab: |S11| (dB) vs frequency from Touchstone files
# =====================================================================
S11_FREQ_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


def read_touchstone_s11(path):
    """Frequencies (Hz), S11 (complex) and reference R of a Touchstone .sNp file.
    Reads the '# <unit> S <RI|MA|DB> R <z>' option line (Touchstone defaults:
    GHZ, MA, R 50). S11 is the first pair of every frequency record."""
    m = re.search(r"\.s(\d+)p$", path, re.I)
    nports = int(m.group(1)) if m else 1
    unit, fmt, z_ref = "GHZ", "MA", 50.0
    values = []
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.split("!", 1)[0].strip()
            if not line or line.startswith("["):
                continue
            if line.startswith("#"):
                tok = line[1:].upper().split()
                if any(t in ("Y", "Z", "H", "G") for t in tok):
                    raise ValueError("only S-parameter files are supported")
                for i, t in enumerate(tok):
                    if t in S11_FREQ_UNITS:
                        unit = t
                    elif t in ("RI", "MA", "DB"):
                        fmt = t
                    elif t == "R" and i + 1 < len(tok):
                        z_ref = float(tok[i + 1])
                continue
            values.extend(float(v) for v in line.split())
    per = 1 + 2 * nports ** 2
    if not values or len(values) % per:
        raise ValueError(f"data does not fit a {nports}-port Touchstone layout")
    data = np.array(values).reshape(-1, per)
    f = data[:, 0] * S11_FREQ_UNITS[unit]
    a, b = data[:, 1], data[:, 2]
    if fmt == "RI":
        s11 = a + 1j * b
    elif fmt == "MA":
        s11 = a * np.exp(1j * np.deg2rad(b))
    else:
        s11 = 10 ** (a / 20) * np.exp(1j * np.deg2rad(b))
    return f, s11, z_ref


# ==================================================================
# APP THEMES (View > Theme)
# ==================================================================
THEMES = ("System", "Light", "Dark")
TEXT_SIZES = {"Small": 0.85, "Normal": 1.0, "Large": 1.2, "Larger": 1.4}
RECENT_MAX = 12
NAV_GROUPS = [
    ("Transmission", ["Raw → Transmission", "Transmission", "Extinction Ratio / IL",
                      "Speed / 3 dB fc", "Period Average / Rise-Fall"]),
    ("Electrical", ["S11 Plot", "C-V Plot", "I-V Plot", "RC / Frequency"]),
    ("Modelling", ["Drude Fit", "Beam Waist (2 mirrors)"]),
]
_SYSTEM_LOOK = {}


def apply_theme(name):
    """Switch the Qt look of the whole app. Graph colours are left alone so saved figures do not change."""
    app = QApplication.instance()
    if not _SYSTEM_LOOK:
        _SYSTEM_LOOK["style"] = app.style().name()
        _SYSTEM_LOOK["palette"] = QtGui.QPalette(app.palette())
    if name == "Light":
        app.setStyle("Fusion")
        app.setPalette(app.style().standardPalette())
    elif name == "Dark":
        app.setStyle("Fusion")
        pal = QtGui.QPalette()
        C = QtGui.QColor
        for role, col in [
                (QtGui.QPalette.Window, C(45, 45, 48)), (QtGui.QPalette.WindowText, C(220, 220, 220)),
                (QtGui.QPalette.Base, C(30, 30, 32)), (QtGui.QPalette.AlternateBase, C(45, 45, 48)),
                (QtGui.QPalette.ToolTipBase, C(60, 60, 64)), (QtGui.QPalette.ToolTipText, C(230, 230, 230)),
                (QtGui.QPalette.Text, C(220, 220, 220)), (QtGui.QPalette.Button, C(55, 55, 58)),
                (QtGui.QPalette.ButtonText, C(220, 220, 220)), (QtGui.QPalette.BrightText, C(255, 80, 80)),
                (QtGui.QPalette.Link, C(90, 160, 255)), (QtGui.QPalette.Highlight, C(42, 110, 200)),
                (QtGui.QPalette.HighlightedText, C(255, 255, 255)),
                (QtGui.QPalette.PlaceholderText, C(140, 140, 140))]:
            pal.setColor(role, col)
        for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
            pal.setColor(QtGui.QPalette.Disabled, role, C(120, 120, 120))
        app.setPalette(pal)
    else:
        app.setStyle(_SYSTEM_LOOK["style"])
        app.setPalette(_SYSTEM_LOOK["palette"])


class _GraphShareKeeper(QtCore.QObject):
    """When a panel splitter gets narrower, shrink the side panels too, so the graph
    keeps at least `share` of the width (by default only the graph would shrink)."""

    def __init__(self, splitter, graph_index, share=0.45):
        super().__init__(splitter)
        self.splitter, self.graph_index, self.share = splitter, graph_index, share
        splitter.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Resize:
            QtCore.QTimer.singleShot(0, self.rebalance)
        return False

    def rebalance(self):
        sizes = self.splitter.sizes()
        total = sum(sizes)
        if total <= 0 or len(sizes) < 2:
            return
        want = int(total * self.share)
        g = self.graph_index
        if sizes[g] >= want:
            return
        sides = [i for i in range(len(sizes)) if i != g]
        side_total = sum(sizes[i] for i in sides)
        room = total - want
        if side_total <= 0:
            return
        for i in sides:
            sizes[i] = max(self.splitter.widget(i).minimumSizeHint().width(),
                           int(sizes[i] * room / side_total))
        sizes[g] = total - sum(sizes[i] for i in sides)
        self.splitter.setSizes(sizes)


class THzPlotComparisonApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("THz Studio")
        self.resize(1400, 850)
        self.files = []
        self.file_entries = []
        self.curves = []    # plotted curves: dicts with key, label, x, y, color
        self.peaks = {}     # curve key -> (x, y) of the selected peak
        self.voltages = {}  # curve key -> voltage text
        self.er_ref_key = None  # reference curve chosen by the user (None = auto)
        self._next_uid = 0      # id for the next loaded file
        self.fc_points = []     # speed tab rows: label, f, vpp, use, sine (fit dict or None)
        self.fc_fit = None      # latest low-pass fit result
        self._fc_last_defaults = ("", "")
        self.pa_results = []    # period-average tab: one analysis dict per capture
        self._pa_last_defaults = ("", "")
        self.s11_data = []   # S11 tab: one dict (path, name, f, S11) per file
        self.rt_ref_paths = []   # raw tab: reference scans (averaged)
        self.rt_samp_paths = []  # raw tab: sample scans
        self.rt_results = []     # raw tab: (name, result dict) per output
        self._rt_last_defaults = ("", "")
        self.initUI()
        self.init_er_tab()
        self.init_fc_tab()
        self.init_pa_tab()
        self.init_s11_tab()
        self.init_rt_tab()
        self.init_cv_tabs()
        self.drude_app = DrudeFitApp()
        self.drude_app.setWindowFlags(Qt.Widget)
        self.tabs.addTab(self.drude_app, "Drude Fit")
        self.beam_waist_tab = BeamWaistTab()
        self.tabs.addTab(self.beam_waist_tab, "Beam Waist (2 mirrors)")
        self.init_menus()
        default = self.default_style_name()
        if default:
            self.load_settings_file(self.style_path(default), quiet=True)
        self.refresh_style_combos(current=default)
        self.plot_data()  # initial empty plot

    def init_cv_tabs(self):
        self.cv_tab = CVPlotTab(
            y_quantity_name="Capacitance",
            y_units=["F", "pF", "nF", "uF"],
            y_unit_to_base_factor={
                "F": 1e12,
                "pF": 1.0,
                "nF": 1e3,
                "uF": 1e6,
            },
            base_unit="pF",
            default_y_unit="F",
            x_keywords=["voltage", "volt", "anodev"],
            y_keywords=["capacitance", "cap"],
            x_fallback_index=2,
            y_fallback_index=0,
            default_title="Measured C-V",
            allow_log_y=False,
        )
        self.iv_tab = CVPlotTab(
            y_quantity_name="Current",
            y_units=["A", "mA", "uA", "nA", "pA"],
            y_unit_to_base_factor={
                "A": 1.0,
                "mA": 1e-3,
                "uA": 1e-6,
                "nA": 1e-9,
                "pA": 1e-12,
            },
            base_unit="A",
            default_y_unit="A",
            x_keywords=["voltage", "volt", "anodev"],
            y_keywords=["current", "anodei", "curr"],
            x_fallback_index=1,
            y_fallback_index=0,
            default_title="Measured I-V",
            allow_log_y=True,
        )
        self.rc_tab = RCPlotTab()
        for tab, title in ((self.cv_tab, "C-V Plot"), (self.iv_tab, "I-V Plot"),
                           (self.rc_tab, "RC / Frequency")):
            self.tabs.addTab(tab, title)
            tab.right_panel.insertLayout(tab.right_panel.count() - 1, self.settings_buttons())

    def closeEvent(self, event):
        self.save_window_state()
        super().closeEvent(event)

    # ---------------------------------------------------------
    # Menu bar, themes and embedded graph toolbars
    # ---------------------------------------------------------
    def init_menus(self):
        self.app_settings = APP_SETTINGS
        theme = self.app_settings.value("view/theme", "System")
        if theme not in THEMES:
            theme = "System"
        apply_theme(theme)
        self.apply_text_size(self.app_settings.value("view/text_size", "Normal"), store=False)
        self.graph_toolbars = []
        self.embed_graph_toolbars()
        self.page_splitters = {}
        for i in range(self.tabs.count()):
            self.make_adaptive(self.tabs.widget(i), self.tabs.tabText(i))
        self.message_history = deque(maxlen=200)
        self.init_status_bar()
        self.init_navigation()
        self.init_openers()
        self.setAcceptDrops(True)
        DIALOG_HOOK["opened"] = self.record_recent

        bar = self.menuBar()
        # File
        m = bar.addMenu("&File")
        self._add_action(m, "Open Data Files...", self.menu_open_data, "Ctrl+O")
        self.recent_menu = m.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self.fill_recent_menu)
        m.addSeparator()
        self._add_action(m, "Save Current Graph...", self.menu_save_graph, "Ctrl+S")
        m.addSeparator()
        self._add_action(m, "Open Session...", self.open_session, "Ctrl+Shift+O")
        self._add_action(m, "Save Session...", self.save_session, "Ctrl+Shift+S")
        m.addSeparator()
        self._add_action(m, "Quit", self.close, "Ctrl+Q")
        # Edit
        m = bar.addMenu("&Edit")
        self._add_action(m, "Copy Graph to Clipboard", self.copy_graph, "Ctrl+Shift+C")
        # View
        m = bar.addMenu("&View")
        tm = m.addMenu("Theme")
        group = QtGui.QActionGroup(self)
        for name in THEMES:
            act = tm.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == theme)
            act.triggered.connect(lambda _=False, n=name: self.set_theme(n))
            group.addAction(act)
        sm = m.addMenu("Text Size")
        group = QtGui.QActionGroup(self)
        current_size = self.app_settings.value("view/text_size", "Normal")
        for name in TEXT_SIZES:
            act = sm.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == current_size)
            act.triggered.connect(lambda _=False, n=name: self.apply_text_size(n))
            group.addAction(act)
        m.addSeparator()
        self.act_nav = self._add_action(m, "Navigation Sidebar", self.set_nav_visible, "Ctrl+B")
        self.act_nav.setCheckable(True)
        self.act_nav.setChecked(self.app_settings.value("view/nav_sidebar", True, type=bool))
        self.set_nav_visible(self.act_nav.isChecked())
        show_tb = self.app_settings.value("view/graph_toolbars", True, type=bool)
        self.act_graph_toolbars = self._add_action(m, "Graph Toolbars", self.set_graph_toolbars_visible)
        self.act_graph_toolbars.setCheckable(True)
        self.act_graph_toolbars.setChecked(show_tb)
        self.set_graph_toolbars_visible(show_tb)
        self.tabs_menu = m.addMenu("Show / Hide Tabs")
        self.tab_actions = {}
        hidden = set(self._hidden_tab_titles())
        for i in range(self.tabs.count()):
            title = self.tabs.tabText(i)
            act = self.tabs_menu.addAction(title.replace("&&", "&").replace("&", "&&"))
            act.setCheckable(True)
            act.setChecked(title not in hidden)
            act.toggled.connect(lambda on, t=title: self.set_tab_shown(t, on))
            self.tab_actions[title] = act
        for title in hidden:
            self.set_tab_shown(title, False, store=False)
        m.addSeparator()
        self._add_action(m, "Message History...", self.show_message_history)
        self._add_action(m, "Full Screen", self.toggle_full_screen, "F11")
        # Go To (one entry per tab)
        m = bar.addMenu("&Go To")
        for i in range(self.tabs.count()):
            key = f"Ctrl+{i + 1}" if i < 9 else None
            title = self.tabs.tabText(i)
            self._add_action(m, title.replace("&&", "&").replace("&", "&&"),
                             lambda _=False, t=title: self.go_to_tab(t), key)
        # Settings (saved styles)
        self.init_settings_menu()
        # Help
        m = bar.addMenu("&Help")
        self._add_action(m, "Keyboard Shortcuts", self.show_shortcuts, "F1")
        self._add_action(m, "About", self.show_about)
        self.restore_window_state()

    def _add_action(self, menu, text, slot, shortcut=None):
        act = menu.addAction(text)
        act.triggered.connect(slot)
        if shortcut:
            act.setShortcut(QtGui.QKeySequence(shortcut))
        return act

    # ---------- status bar and message history ----------
    def init_status_bar(self):
        sb = self.statusBar()
        self.status_label = QLabel("")
        sb.addWidget(self.status_label, 1)
        self.status_timer = QtCore.QTimer(self)
        self.status_timer.setSingleShot(True)
        self.status_timer.timeout.connect(lambda: (self.status_label.setText(""),
                                                   self.status_label.setStyleSheet("")))
        STATUS_SINK["show"] = self.show_status

    def show_status(self, text, seconds=10):
        self.message_history.append(f"{datetime.now():%H:%M:%S}  {text}")
        self.status_label.setText(text)
        self.status_label.setToolTip("All recent messages: View > Message History")
        self.status_label.setStyleSheet("QLabel { background: #2a6ec8; color: white; padding: 1px 6px; }")
        QtCore.QTimer.singleShot(1500, lambda: self.status_label.setStyleSheet(""))
        self.status_timer.start(int(seconds * 1000))

    def show_message_history(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Message History")
        dlg.resize(640, 400)
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit("\n".join(self.message_history) or "No messages yet.")
        txt.setReadOnly(True)
        lay.addWidget(txt)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        dlg.exec()

    # ---------- navigation sidebar and tab visibility ----------
    def init_navigation(self):
        self.nav = QtWidgets.QTreeWidget()
        self.nav.setHeaderHidden(True)
        self.nav.setMinimumWidth(170)
        self.nav.setRootIsDecorated(True)
        self.nav_items = {}
        titles = [self.tabs.tabText(i) for i in range(self.tabs.count())]
        placed = set()
        groups = [(g, [t for t in members if t in titles]) for g, members in NAV_GROUPS]
        groups.append(("Other", [t for t in titles if not any(t in m for _, m in NAV_GROUPS)]))
        bold = QtGui.QFont()
        bold.setBold(True)
        for gname, members in groups:
            if not members:
                continue
            g = QtWidgets.QTreeWidgetItem([gname])
            g.setFlags(Qt.ItemIsEnabled)
            g.setFont(0, bold)
            self.nav.addTopLevelItem(g)
            for t in members:
                it = QtWidgets.QTreeWidgetItem([t.replace("&&", "&")])
                it.setData(0, Qt.UserRole, t)
                g.addChild(it)
                self.nav_items[t] = it
                placed.add(t)
        self.nav.expandAll()
        self.nav.resizeColumnToContents(0)
        nav_w = min(self.nav.sizeHintForColumn(0) + 40, 320)
        self.nav.setMinimumWidth(110)
        self.nav.itemClicked.connect(self._on_nav_clicked)
        self.tabs.currentChanged.connect(self._sync_nav)
        self.takeCentralWidget()
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.addWidget(self.nav)
        self.main_splitter.addWidget(self.tabs)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([min(nav_w, 200), 1400])
        self.setCentralWidget(self.main_splitter)
        self._sync_nav(self.tabs.currentIndex())

    def _tab_index(self, title):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == title:
                return i
        return -1

    def _on_nav_clicked(self, item, _col=0):
        title = item.data(0, Qt.UserRole)
        if title:
            self.tabs.setCurrentIndex(self._tab_index(title))

    def _sync_nav(self, index):
        it = self.nav_items.get(self.tabs.tabText(index)) if hasattr(self, "nav_items") else None
        if it is not None:
            self.nav.setCurrentItem(it)

    def set_nav_visible(self, visible):
        self.nav.setVisible(bool(visible))
        self.tabs.tabBar().setVisible(not visible)  # the sidebar replaces the tab row
        self.app_settings.setValue("view/nav_sidebar", bool(visible))

    def _hidden_tab_titles(self):
        try:
            return list(json.loads(self.app_settings.value("view/hidden_tabs", "[]")))
        except Exception:
            return []

    def set_tab_shown(self, title, shown, store=True):
        i = self._tab_index(title)
        if i < 0:
            return
        if not shown and sum(self.tabs.isTabVisible(k) for k in range(self.tabs.count())) <= 1:
            self.tab_actions[title].setChecked(True)  # keep at least one tab
            return
        self.tabs.setTabVisible(i, shown)
        if title in self.nav_items:
            self.nav_items[title].setHidden(not shown)
        if not shown and self.tabs.currentIndex() == i:
            for k in range(self.tabs.count()):
                if self.tabs.isTabVisible(k):
                    self.tabs.setCurrentIndex(k)
                    break
        if store:
            hidden = [self.tabs.tabText(k) for k in range(self.tabs.count())
                      if not self.tabs.isTabVisible(k)]
            self.app_settings.setValue("view/hidden_tabs", json.dumps(hidden))

    def go_to_tab(self, title):
        if title in self.tab_actions and not self.tab_actions[title].isChecked():
            self.tab_actions[title].setChecked(True)
        self.tabs.setCurrentIndex(self._tab_index(title))

    # ---------- resizable layouts ----------
    def _has_canvas(self, obj):
        if obj is None:
            return False
        if isinstance(obj, QWidget):
            return isinstance(obj, FigureCanvasQTAgg) or bool(obj.findChildren(FigureCanvasQTAgg))
        for i in range(obj.count()):
            it = obj.itemAt(i)
            if self._has_canvas(it.widget() or it.layout()):
                return True
        return False

    def make_adaptive(self, page, key):
        """Make a tab shrink with the window: the row that holds the graph becomes a
        splitter (drag the handles to resize panels) and the side panels scroll."""
        if isinstance(page, QTabWidget):
            for i in range(page.count()):
                self.make_adaptive(page.widget(i), f"{key}/{page.tabText(i)}")
            return
        if page.layout() is not None:
            self._adapt_layout(page.layout(), key)

    def _adapt_layout(self, lay, key, depth=0):
        if depth > 4 or not self._has_canvas(lay):
            return False
        real = [lay.itemAt(i) for i in range(lay.count())
                if lay.itemAt(i).widget() is not None or lay.itemAt(i).layout() is not None]
        canvas_items = [it for it in real if self._has_canvas(it.widget() or it.layout())]
        if isinstance(lay, QHBoxLayout) and len(real) >= 2 and len(canvas_items) == 1:
            self._to_splitter(lay, key)
            return True
        for it in canvas_items:
            sub = it.layout()
            if sub is None and type(it.widget()) in (QWidget, QGroupBox, QFrame):
                sub = it.widget().layout()
            if sub is not None and self._adapt_layout(sub, key, depth + 1):
                return True
        return False

    def _to_splitter(self, lay, key):
        pieces = []
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() is not None:
                w = it.widget()
            elif it.layout() is not None:
                sub = it.layout()
                sub.setParent(None)
                w = QWidget()
                w.setLayout(sub)
            else:
                continue  # spacer / stretch
            pieces.append(w)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        sizes = []
        for w in pieces:
            canvas = self._has_canvas(w)
            if not canvas:
                if not isinstance(w, QScrollArea):
                    sa = QScrollArea()
                    sa.setWidgetResizable(True)
                    sa.setFrameShape(QFrame.NoFrame)
                    sa.setWidget(w)
                    w = sa
                w.setMinimumWidth(min(w.minimumWidth() or 150, 150))
                inner = w.widget()
                hint = inner.sizeHint().width() if inner is not None else 250
                sizes.append(max(220, min(hint + 20, 440)))
            else:
                sizes.append(1000)
            splitter.addWidget(w)
            splitter.setStretchFactor(splitter.count() - 1, 1 if canvas else 0)
        splitter.setSizes(sizes)
        lay.addWidget(splitter)
        self.page_splitters[key] = splitter
        _GraphShareKeeper(splitter, sizes.index(1000))

    # ---------- window state ----------
    def restore_window_state(self):
        geo = self.app_settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        split = self.app_settings.value("window/splitter")
        if split is not None:
            self.main_splitter.restoreState(split)
        for key, sp in self.page_splitters.items():
            state = self.app_settings.value(f"splitters/{key}")
            if state is not None:
                sp.restoreState(state)
        i = self._tab_index(self.app_settings.value("window/tab", ""))
        if i >= 0 and self.tabs.isTabVisible(i):
            self.tabs.setCurrentIndex(i)

    def save_window_state(self):
        self.app_settings.setValue("window/geometry", self.saveGeometry())
        self.app_settings.setValue("window/splitter", self.main_splitter.saveState())
        for key, sp in self.page_splitters.items():
            self.app_settings.setValue(f"splitters/{key}", sp.saveState())
        self.app_settings.setValue("window/tab", self.tabs.tabText(self.tabs.currentIndex()))

    # ---------- text size ----------
    def apply_text_size(self, name, store=True):
        app = QApplication.instance()
        if not hasattr(self, "_base_font"):
            self._base_font = QtGui.QFont(app.font())
        f = QtGui.QFont(self._base_font)
        base = self._base_font.pointSizeF() if self._base_font.pointSizeF() > 0 else 10.0
        f.setPointSizeF(base * TEXT_SIZES.get(name, 1.0))
        app.setFont(f)
        if store:
            self.app_settings.setValue("view/text_size", name)

    # ---------- opening files: dialogs, drag and drop, recent files, sessions ----------
    def init_openers(self):
        """Tab page -> function that opens files through that tab's own Open dialog."""
        def page_of(widget):
            for i in range(self.tabs.count()):
                if self.tabs.widget(i) is widget or self.tabs.widget(i).isAncestorOf(widget):
                    return self.tabs.widget(i)
            return None
        self.openers = {}
        for widget, fn in [(self.canvas, self.add_data_file),
                           (self.fc_canvas, self.add_fc_scope_files),
                           (self.pa_canvas, self.add_pa_files),
                           (self.s11_canvas, self.add_s11_files),
                           (self.cv_tab, self.cv_tab.load_file),
                           (self.iv_tab, self.iv_tab.load_file),
                           (self.rc_tab, self.rc_tab.load_file),
                           (self.drude_app, self.drude_app.load_file),
                           (self.rt_canvas, self.open_rt_files)]:
            page = page_of(widget)
            if page is not None:
                self.openers[page] = fn

    def open_rt_files(self, kind=None):
        """Raw tab: ask whether dropped files are reference or sample scans."""
        if kind is None:
            if DIALOG_HOOK["inject"] is None:
                return self.add_rt_files(self.rt_ref_paths, "Reference")
            menu = QtWidgets.QMenu(self)
            ref = menu.addAction("Add as Reference scans")
            menu.addAction("Add as Sample scans")
            act = menu.exec(QtGui.QCursor.pos())
            if act is None:
                return
            kind = "Reference" if act is ref else "Sample"
        self.add_rt_files(self.rt_ref_paths if kind == "Reference" else self.rt_samp_paths, kind)

    def open_paths_in_tab(self, paths, page=None):
        page = page or self.tabs.currentWidget()
        fn = self.openers.get(page)
        if fn is None:
            self.show_status(f"The '{self.tabs.tabText(self.tabs.indexOf(page))}' tab does not "
                             "take dropped files. Open it from the tab's own buttons.")
            return False
        missing = [p for p in paths if not os.path.isfile(p)]
        paths = [p for p in paths if os.path.isfile(p)]
        if missing:
            QMessageBox.warning(self, "Open", "These files no longer exist:\n" + "\n".join(missing))
        if not paths:
            return False
        if fn == self.drude_app.load_file and len(paths) > 1:
            self.show_status("Drude Fit takes one file; opened the first one.")
            paths = paths[:1]
        self.tabs.setCurrentWidget(page)
        DIALOG_HOOK["inject"] = paths
        try:
            fn()
        finally:
            DIALOG_HOOK["inject"] = None
        return True

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return
        event.acceptProposedAction()
        if os.path.splitext(paths[0])[1].lower() == ".thzsession":
            self.open_session(paths[0])
            return
        self.open_paths_in_tab(paths)

    def record_recent(self, paths):
        page = self.tabs.currentWidget()
        if page not in getattr(self, "openers", {}):
            return
        paths = [p for p in paths if os.path.splitext(p)[1].lower() not in (".json", ".thzsession")]
        if not paths:
            return
        title = self.tabs.tabText(self.tabs.indexOf(page))
        recent = self._recent()
        for p in reversed(paths):
            recent = [r for r in recent if r != [p, title]]
            recent.insert(0, [p, title])
        self.app_settings.setValue("recent/files", json.dumps(recent[:RECENT_MAX]))

    def _recent(self):
        try:
            return [list(r) for r in json.loads(self.app_settings.value("recent/files", "[]"))]
        except Exception:
            return []

    def fill_recent_menu(self):
        self.recent_menu.clear()
        recent = self._recent()
        if not recent:
            a = self.recent_menu.addAction("(no recent files)")
            a.setEnabled(False)
            return
        for path, title in recent:
            a = self.recent_menu.addAction(f"{os.path.basename(path)}   [{title}]")
            a.setToolTip(path)
            a.setStatusTip(path)
            a.triggered.connect(lambda _=False, p=path, t=title: self.open_recent(p, t))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear List").triggered.connect(
            lambda: self.app_settings.setValue("recent/files", "[]"))

    def open_recent(self, path, title):
        i = self._tab_index(title)
        if i < 0:
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Open Recent", f"File not found:\n{path}")
            self.app_settings.setValue("recent/files", json.dumps(
                [r for r in self._recent() if r[0] != path]))
            return
        self.go_to_tab(title)
        self.open_paths_in_tab([path], self.tabs.widget(i))

    def _session_files(self):
        def title_of(widget):
            for i in range(self.tabs.count()):
                if self.tabs.widget(i).isAncestorOf(widget) or self.tabs.widget(i) is widget:
                    return self.tabs.tabText(i)
            return None
        files = {}
        files[title_of(self.canvas)] = [f[0] for f in self.files]
        files[title_of(self.fc_canvas)] = [p["path"] for p in self.fc_points if p.get("path")]
        files[title_of(self.pa_canvas)] = [r["path"] for r in self.pa_results]
        files[title_of(self.s11_canvas)] = [d["path"] for d in self.s11_data]
        for tab in (self.cv_tab, self.iv_tab, self.rc_tab):
            files[title_of(tab)] = list(dict.fromkeys(ds.file_path for ds in tab.datasets))
        if getattr(self.drude_app, "file_path", None):
            files[title_of(self.drude_app)] = [self.drude_app.file_path]
        return {t: v for t, v in files.items() if t and v}

    def _page_of(self, widget):
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) is widget or self.tabs.widget(i).isAncestorOf(widget):
                return self.tabs.widget(i)
        return None

    @staticmethod
    def _tab_sheets(tab):
        sheets = {}
        for ds in tab.datasets:
            if ds.sheet is not None:
                sheets.setdefault(ds.file_path, []).append(ds.sheet)
        return sheets

    def save_session(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Session", "session.thzsession", "THz App Session (*.thzsession)")
        if not path:
            return
        if not path.endswith(".thzsession"):
            path += ".thzsession"
        data = {"version": 1,
                "current_tab": self.tabs.tabText(self.tabs.currentIndex()),
                "files": self._session_files(),
                "raw_reference": list(self.rt_ref_paths),
                "raw_sample": list(self.rt_samp_paths),
                "sheets": {self.tabs.tabText(self.tabs.indexOf(self._page_of(tab))):
                           self._tab_sheets(tab) for tab in (self.cv_tab, self.iv_tab, self.rc_tab)},
                "style": self.get_settings()}
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "Save Session", f"Could not save the session:\n{e}")
            return
        self.show_status(f"Session saved: {path}")

    def open_session(self, path=None):
        if not path:
            DIALOG_HOOK_opened, DIALOG_HOOK["opened"] = DIALOG_HOOK["opened"], None
            try:
                path, _ = QFileDialog.getOpenFileName(
                    self, "Open Session", "", "THz App Session (*.thzsession);;All Files (*)")
            finally:
                DIALOG_HOOK["opened"] = DIALOG_HOOK_opened
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Open Session", f"Could not read the session:\n{e}")
            return
        if data.get("style"):
            self.apply_settings(data["style"])
        missing = []
        for title, paths in data.get("files", {}).items():
            i = self._tab_index(title)
            missing += [p for p in paths if not os.path.isfile(p)]
            paths = [p for p in paths if os.path.isfile(p)]
            if i >= 0 and paths:
                page = self.tabs.widget(i)
                fn = self.openers.get(page)
                if fn is None or fn == self.open_rt_files:
                    continue
                self.tabs.setCurrentWidget(page)
                DIALOG_HOOK["inject"] = paths
                sheet_tab = getattr(fn, "__self__", None)
                if isinstance(sheet_tab, CVPlotTab):
                    sheet_tab.preset_sheets = data.get("sheets", {}).get(title, {})
                try:
                    fn()
                finally:
                    DIALOG_HOOK["inject"] = None
                    if isinstance(sheet_tab, CVPlotTab):
                        sheet_tab.preset_sheets = None
        for key, kind in (("raw_reference", "Reference"), ("raw_sample", "Sample")):
            paths = [p for p in data.get(key, []) if os.path.isfile(p)]
            missing += [p for p in data.get(key, []) if not os.path.isfile(p)]
            if paths:
                DIALOG_HOOK["inject"] = paths
                try:
                    self.open_rt_files(kind)
                finally:
                    DIALOG_HOOK["inject"] = None
        i = self._tab_index(data.get("current_tab", ""))
        if i >= 0:
            self.go_to_tab(data["current_tab"])
        if missing:
            QMessageBox.warning(self, "Open Session",
                                "These files were not found and were skipped:\n" + "\n".join(missing))
        self.show_status(f"Session opened: {os.path.basename(path)}")

    # ---------- graphs ----------
    def embed_graph_toolbars(self):
        """Put the standard Matplotlib toolbar (home, pan, zoom, save) above every graph that has none."""
        have = {id(tb.canvas) for tb in self.findChildren(NavigationToolbar2QT)}
        for canvas in self.findChildren(FigureCanvasQTAgg):
            if id(canvas) in have:
                continue
            parent = canvas.parentWidget()
            box = QWidget()
            if isinstance(parent, QSplitter):
                parent.replaceWidget(parent.indexOf(canvas), box)
            elif parent is None or parent.layout() is None or \
                    parent.layout().replaceWidget(canvas, box) is None:
                box.deleteLater()
                continue
            lay = QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            tb = NavigationToolbar2QT(canvas, box)
            lay.addWidget(tb)
            lay.addWidget(canvas, 1)
            canvas.show()
            self.graph_toolbars.append(tb)

    def set_graph_toolbars_visible(self, visible):
        for tb in self.graph_toolbars:
            tb.setVisible(bool(visible))
        self.app_settings.setValue("view/graph_toolbars", bool(visible))

    def set_theme(self, name):
        apply_theme(name)
        self.app_settings.setValue("view/theme", name)
        # Toolbar icons are drawn for the palette at creation time; redraw them for the new one
        for tb in self.findChildren(NavigationToolbar2QT):
            try:
                for _text, _tip, image, callback in tb.toolitems:
                    if image and callback in tb._actions:
                        tb._actions[callback].setIcon(tb._icon(image + ".png"))
            except Exception:
                pass

    def toggle_full_screen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _current_canvas(self):
        page = self.tabs.currentWidget()
        for canvas in page.findChildren(FigureCanvasQTAgg):
            if canvas.isVisible():
                return canvas
        return None

    def menu_open_data(self):
        page = self.tabs.currentWidget()
        fn = self.openers.get(page)
        if fn is None:  # tab without its own data files: use the Transmission tab
            for i in range(self.tabs.count()):
                if self.tabs.widget(i).isAncestorOf(self.canvas):
                    self.go_to_tab(self.tabs.tabText(i))
                    break
            fn = self.add_data_file
        fn()

    def menu_save_graph(self):
        canvas = self._current_canvas()
        if canvas is None:
            self.show_status("This tab has no graph on screen.")
            return
        NavigationToolbar2QT.save_figure(canvas.toolbar or NavigationToolbar2QT(canvas, None))

    def copy_graph(self):
        canvas = self._current_canvas()
        if canvas is None:
            self.show_status("This tab has no graph on screen.")
            return
        import io
        buf = io.BytesIO()
        canvas.figure.savefig(buf, format="png", dpi=200, bbox_inches="tight",
                              facecolor=canvas.figure.get_facecolor())
        img = QtGui.QImage.fromData(buf.getvalue(), "PNG")
        QApplication.clipboard().setImage(img)
        self.show_status(f"Graph copied to the clipboard ({img.width()} x {img.height()} px).")

    def show_shortcuts(self):
        rows = []
        for menu_act in self.menuBar().actions():
            menu = menu_act.menu()
            stack = [menu]
            while stack:
                mm = stack.pop()
                for a in mm.actions():
                    if a.menu():
                        stack.append(a.menu())
                    elif not a.shortcut().isEmpty():
                        rows.append((a.text().replace("&&", "\x00").replace("&", "").replace("\x00", "&"),
                                     a.shortcut().toString(QtGui.QKeySequence.NativeText)))
        rows += [("Graph: pan / zoom / home", "P / O / H  (click the graph first)"),
                 ("Graph: back / forward view", "Left / Right arrow or C / V"),
                 ("Graph: toggle grid", "G"),
                 ("Drude Fit: stop a running fit", "Esc"),
                 ("Open files", "Drag files from the file manager onto the window")]
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.resize(560, 520)
        lay = QVBoxLayout(dlg)
        table = QTableWidget(len(rows), 2)
        table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, (a, k) in enumerate(rows):
            table.setItem(r, 0, QTableWidgetItem(a))
            table.setItem(r, 1, QTableWidgetItem(k))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        lay.addWidget(table)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        dlg.exec()

    def show_about(self):
        QMessageBox.about(
            self, "About",
            "THz Studio: THz and device measurement analysis\n\n"
            f"Qt {QtCore.qVersion()}, PySide6, Matplotlib {matplotlib.__version__}\n"
            f"Python {sys.version.split()[0]}")

    def init_settings_menu(self):
        menu = self.menuBar().addMenu("Settings")
        for text, slot in [
                ("Export Style to File...", self.save_settings),
                ("Import Style from File...", self.load_settings),
                (None, None),
                ("Clear Startup Default", self.clear_default_style)]:
            if text is None:
                menu.addSeparator()
                continue
            action = menu.addAction(text)
            action.triggered.connect(slot)

    def settings_buttons(self):
        """Saved-style picker shown under each tab's right panel. Picking a style applies it."""
        if not hasattr(self, "style_combos"):
            self.style_combos = []
        box = QGroupBox("Saved Styles (all tabs)")
        layout = QVBoxLayout(box)
        combo = QComboBox()
        combo.setToolTip("Pick a saved style to apply it")
        combo.activated[int].connect(lambda i, c=combo: self.apply_named_style(c))
        self.style_combos.append(combo)
        layout.addWidget(combo)
        row = QHBoxLayout()
        for text, slot, tip in [
                ("Save As...", self.save_named_style, "Save the current settings under a name"),
                ("Set as Default", lambda _, c=combo: self.set_default_style(c),
                 "Load the selected style every time the app starts"),
                ("Delete", lambda _, c=combo: self.delete_named_style(c),
                 "Delete the selected style")]:
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        layout.addLayout(row)
        wrapper = QVBoxLayout()
        wrapper.addWidget(box)
        return wrapper

    def style_path(self, name):
        return os.path.join(STYLES_DIR, name + ".json")

    def style_names(self):
        if not os.path.isdir(STYLES_DIR):
            return []
        return sorted((f[:-5] for f in os.listdir(STYLES_DIR) if f.endswith(".json")),
                      key=str.lower)

    def default_style_name(self):
        try:
            with open(DEFAULT_STYLE_FILE) as f:
                name = f.read().strip()
        except OSError:
            return None
        return name if name and os.path.isfile(self.style_path(name)) else None

    def refresh_style_combos(self, current=None):
        """Fill every tab's style list; current = name to show as selected."""
        default = self.default_style_name()
        for combo in self.style_combos:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(STYLE_PLACEHOLDER, "")
            for name in self.style_names():
                combo.addItem(f"{name}  (default)" if name == default else name, name)
            idx = combo.findData(current) if current else 0
            combo.setCurrentIndex(max(idx, 0))
            combo.blockSignals(False)

    def _selected_style(self, combo):
        name = combo.currentData()
        if not name:
            QMessageBox.information(self, "Saved Styles", "Choose a style from the list first.")
        return name

    def apply_named_style(self, combo):
        name = combo.currentData()
        if name:
            self.load_settings_file(self.style_path(name))
            self.refresh_style_combos(current=name)

    def save_named_style(self):
        current = next((c.currentData() for c in self.style_combos if c.currentData()), "")
        name, ok = QInputDialog.getText(self, "Save Style", "Style name:", text=current)
        name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
        if not ok or not name:
            return
        path = self.style_path(name)
        if os.path.isfile(path) and QMessageBox.question(
                self, "Save Style", f"Replace the saved style '{name}'?") != QMessageBox.Yes:
            return
        try:
            self.write_settings_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save style: {e}")
            return
        self.refresh_style_combos(current=name)

    def set_default_style(self, combo):
        name = self._selected_style(combo)
        if not name:
            return
        if name == self.default_style_name():
            self.clear_default_style()
            return
        os.makedirs(os.path.dirname(DEFAULT_STYLE_FILE), exist_ok=True)
        with open(DEFAULT_STYLE_FILE, "w") as f:
            f.write(name)
        self.refresh_style_combos(current=name)
        QMessageBox.information(self, "Startup Default", f"'{name}' will load when the app starts.")

    def clear_default_style(self):
        if os.path.isfile(DEFAULT_STYLE_FILE):
            os.remove(DEFAULT_STYLE_FILE)
        current = next((c.currentData() for c in self.style_combos if c.currentData()), None)
        self.refresh_style_combos(current=current)
        QMessageBox.information(self, "Startup Default",
                                "The app will start with its built-in settings.")

    def delete_named_style(self, combo):
        name = self._selected_style(combo)
        if not name or QMessageBox.question(
                self, "Delete Style", f"Delete the saved style '{name}'?") != QMessageBox.Yes:
            return
        os.remove(self.style_path(name))
        if name == self.default_style_name() or not self.default_style_name():
            if os.path.isfile(DEFAULT_STYLE_FILE):
                os.remove(DEFAULT_STYLE_FILE)
        self.refresh_style_combos()

    def _settings_widgets(self):
        """name -> widget for every plot/analysis control on all tabs. Per-file
        controls and controls that list loaded data are left out."""
        widgets = {}
        for name, obj in vars(self).items():
            if name in SETTINGS_SKIP:
                continue
            if isinstance(obj, (QLineEdit, QComboBox, QCheckBox)):
                widgets[name] = obj
            elif isinstance(obj, StylePanel):
                for sub, w in vars(obj).items():
                    if isinstance(w, (QLineEdit, QComboBox, QCheckBox)):
                        widgets[f"{name}.{sub}"] = w
            elif isinstance(obj, CVPlotTab):
                for sub, w in vars(obj.style).items():
                    if isinstance(w, (QLineEdit, QComboBox, QCheckBox)):
                        widgets[f"{name}.style.{sub}"] = w
        return widgets

    def get_settings(self):
        values = {}
        for name, w in self._settings_widgets().items():
            if isinstance(w, QLineEdit):
                values[name] = w.text()
            elif isinstance(w, QComboBox):
                values[name] = w.currentText()
            else:
                values[name] = w.isChecked()
        return values

    def apply_settings(self, values):
        """Set the widgets without firing a redraw for each one, then redraw every tab once.
        Returns the names that could not be applied (unknown key or combo option)."""
        skipped = []
        for name, w in self._settings_widgets().items():
            if name not in values:
                continue
            value = values[name]
            w.blockSignals(True)
            if isinstance(w, QLineEdit):
                w.setText(str(value))
            elif isinstance(w, QComboBox):
                idx = w.findText(str(value))
                if idx >= 0:
                    w.setCurrentIndex(idx)
                else:
                    skipped.append(name)
            else:
                w.setChecked(bool(value))
            w.blockSignals(False)

        # Handlers that enable/disable widgets or refit, then redraw
        self.legend_order_list.setEnabled(not self.auto_arrange_legends.isChecked())
        self.on_er_quantity_changed()
        self.refresh_fc_tab()
        if self.pa_results:
            self.reanalyze_pa()
        self.plot_pa()
        self.plot_s11()
        self.compute_rt()
        for tab in (self.cv_tab, self.iv_tab, self.rc_tab):
            tab.update_plot()
        self.plot_data()
        return skipped

    def save_settings(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Plot Settings", "plot_settings.json", "Settings (*.json);;All Files (*)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".json"
        try:
            self.write_settings_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save settings: {e}")

    def load_settings(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Plot Settings", "", "Settings (*.json);;All Files (*)")
        if path:
            self.load_settings_file(path)

    def write_settings_file(self, path):
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"app": "thzstudio", "version": 1, "settings": self.get_settings()},
                      f, indent=2)

    def load_settings_file(self, path, quiet=False):
        try:
            with open(path) as f:
                data = json.load(f)
            values = data["settings"] if "settings" in data else data
            skipped = self.apply_settings(values)
        except Exception as e:
            if not quiet:
                QMessageBox.critical(self, "Load Error", f"Could not load settings: {e}")
            return
        if skipped and not quiet:
            QMessageBox.warning(self, "Load Settings",
                                "These settings had values that are not available and were left "
                                "unchanged:\n" + "\n".join(skipped))

    def initUI(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        central = QWidget()
        self.tabs.addTab(central, "Transmission")
        main_layout = QHBoxLayout(central)

        # Left panel
        left_panel = QVBoxLayout()
        btn_add = QPushButton("Add Plot Data (select one or many)")
        btn_add.clicked.connect(self.add_data_file)
        btn_remove_selected = QPushButton("Remove Selected")
        btn_remove_selected.clicked.connect(self.remove_selected_files)
        btn_select_all = QPushButton("Select All / None")
        btn_select_all.clicked.connect(self.toggle_select_all)
        btn_clear = QPushButton("Clear Plots")
        btn_clear.clicked.connect(self.clear_data)
        left_panel.addWidget(btn_add)
        select_row = QHBoxLayout()
        select_row.addWidget(btn_select_all)
        select_row.addWidget(btn_remove_selected)
        left_panel.addLayout(select_row)
        left_panel.addWidget(btn_clear)
        left_panel.addWidget(QLabel("Loaded Files & Columns:"))

        self.file_scroll_area = QScrollArea()
        self.file_scroll_area.setWidgetResizable(True)
        self.file_list_widget = QWidget()
        self.file_list_layout = QVBoxLayout(self.file_list_widget)
        self.file_list_layout.addStretch()
        self.file_scroll_area.setWidget(self.file_list_widget)
        left_panel.addWidget(self.file_scroll_area)

        # Plot area
        self.fig, self.ax = plt.subplots(figsize=(10, 6), dpi=100)
        self.canvas = FigureCanvas(self.fig)
        plot_area_layout = QVBoxLayout()
        plot_area_layout.addWidget(self.canvas)

        # Hover annotation
        self.annot = self.ax.annotate(
            "", xy=(0, 0), xytext=(15, 15), textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w"),
            arrowprops=dict(arrowstyle="->")
        )
        self.annot.set_visible(False)
        self.canvas.mpl_connect("motion_notify_event", self.on_hover)
        self.canvas.mpl_connect("button_press_event", self.on_click)

        # Peak selection controls (below the plot)
        self.pick_peak_mode = QCheckBox("Pick Peak (click on plot)")
        self.peak_curve_combo = QComboBox()
        self.peak_curve_combo.addItem(ALL_CURVES_TEXT, "")
        self.snap_local_max = QCheckBox("Snap to local max ±")
        self.snap_local_max.setChecked(True)
        self.snap_window = QLineEdit("5")
        self.snap_window.setFixedWidth(40)
        btn_auto_peak = QPushButton("Auto Max")
        btn_auto_peak.setToolTip("Mark the maximum of each curve inside the current X range")
        btn_auto_peak.clicked.connect(self.auto_pick_peaks)
        btn_clear_peaks = QPushButton("Clear Peaks")
        btn_clear_peaks.clicked.connect(self.clear_peaks)

        # Two short rows instead of one long one, so the plot area can get narrow
        peak_row = QHBoxLayout()
        peak_row.addWidget(self.pick_peak_mode)
        peak_row.addWidget(QLabel("Curve:"))
        self.peak_curve_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.peak_curve_combo.setMinimumContentsLength(10)
        peak_row.addWidget(self.peak_curve_combo, 1)
        peak_row2 = QHBoxLayout()
        peak_row2.addWidget(self.snap_local_max)
        peak_row2.addWidget(self.snap_window)
        peak_row2.addWidget(QLabel("pts"))
        peak_row2.addWidget(btn_auto_peak)
        peak_row2.addWidget(btn_clear_peaks)
        peak_row2.addStretch(1)
        plot_area_layout.addLayout(peak_row)
        plot_area_layout.addLayout(peak_row2)

        # Right panel
        right_panel = QVBoxLayout()

        self.xlabel = QLineEdit("Frequency [GHz]")
        self.ylabel = QLineEdit("Transmission")
        self.plot_font_size = QLineEdit("24")
        self.axis_label_font_size = QLineEdit("24")
        self.line_width = QLineEdit("3")
        self.spine_width = QLineEdit("4")
        self.major_tick_length = QLineEdit("6")
        self.minor_tick_length = QLineEdit("3")
        self.major_tick_width = QLineEdit("2")
        self.minor_tick_width = QLineEdit("1")

        self.num_major_ticks_x = QComboBox()
        self.num_major_ticks_x.addItems([str(i) for i in range(2, 11)])
        self.num_major_ticks_x.setCurrentText("5")

        self.num_minor_ticks_x = QComboBox()
        self.num_minor_ticks_x.addItems([str(i) for i in range(0, 6)])
        self.num_minor_ticks_x.setCurrentText("4")

        self.num_major_ticks_y = QComboBox()
        self.num_major_ticks_y.addItems([str(i) for i in range(2, 11)])
        self.num_major_ticks_y.setCurrentText("5")

        self.num_minor_ticks_y = QComboBox()
        self.num_minor_ticks_y.addItems([str(i) for i in range(0, 6)])
        self.num_minor_ticks_y.setCurrentText("4")

        self.x_axis_min = QLineEdit("")
        self.x_axis_max = QLineEdit("")
        self.y_axis_min = QLineEdit("")
        self.y_axis_max = QLineEdit("")

        self.bold_axis_labels = QCheckBox("Bold Axis Labels")
        self.bold_axis_labels.setChecked(True)
        self.bold_legend = QCheckBox("Bold Legend")
        self.bold_legend.setChecked(True)
        self.bold_ticks = QCheckBox("Bold Tick Labels")
        self.bold_ticks.setChecked(True)

        self.save_width = QLineEdit("10")
        self.save_height = QLineEdit("6")
        self.save_dpi = QLineEdit("300")

        # Auto-arrange legends checkbox
        self.auto_arrange_legends = QCheckBox("Auto-Arrange Legends")
        self.auto_arrange_legends.setChecked(True)
        self.auto_arrange_legends.stateChanged.connect(self.on_auto_arrange_changed)

        # Legend order list - editable, drag & drop
        self.legend_order_list = QListWidget()
        self.legend_order_list.setSelectionMode(QListWidget.SingleSelection)
        self.legend_order_list.setDragDropMode(QListWidget.InternalMove)
        self.legend_order_list.itemChanged.connect(self.plot_data)
        self.legend_order_list.setEnabled(False)  # disabled by default (auto arrange on)

        # Legend location dropdown
        self.legend_location = QComboBox()
        self.legend_location.addItems(LEGEND_LOCATION_OPTIONS)
        self.legend_location.currentIndexChanged.connect(self.plot_data)

        self.font_family = make_font_combo()
        rows = [
            ("Font", self.font_family),
            ("X Label", self.xlabel),
            ("Y Label", self.ylabel),
            ("Font Size (Ticks)", self.plot_font_size),
            ("Axis Label Font Size", self.axis_label_font_size),
            ("Line Width", self.line_width),
            ("Spine Width", self.spine_width),
            (None, self.bold_axis_labels),
            (None, self.bold_legend),
            (None, self.bold_ticks),
            ("X Axis Min", self.x_axis_min),
            ("X Axis Max", self.x_axis_max),
            ("Y Axis Min", self.y_axis_min),
            ("Y Axis Max", self.y_axis_max),
            ("Major Tick Length", self.major_tick_length),
            ("Minor Tick Length", self.minor_tick_length),
            ("Major Tick Width", self.major_tick_width),
            ("Minor Tick Width", self.minor_tick_width),
            ("Major Ticks X", self.num_major_ticks_x),
            ("Minor Ticks X Between", self.num_minor_ticks_x),
            ("Major Ticks Y", self.num_major_ticks_y),
            ("Minor Ticks Y Between", self.num_minor_ticks_y),
            (None, self.auto_arrange_legends),
            ("Legend Location", self.legend_location),
            ("Legend Order (drag to reorder, double-click to edit)", self.legend_order_list),
            ("Save Width (in)", self.save_width),
            ("Save Height (in)", self.save_height),
            ("Save DPI", self.save_dpi),
        ]
        sections = QWidget()
        sections.setLayout(build_settings_sections(rows, "transmission"))
        sections_scroll = QScrollArea()
        sections_scroll.setWidgetResizable(True)
        sections_scroll.setFrameShape(QFrame.NoFrame)
        sections_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sections_scroll.setWidget(sections)
        right_panel.addWidget(sections_scroll, 1)

        btn_plot = QPushButton("Plot")
        btn_plot.clicked.connect(self.plot_data)
        btn_save = QPushButton("Save Plot (PNG, JPEG, PDF, SVG, ...)")
        btn_save.clicked.connect(self.save_plot)
        right_panel.addWidget(btn_plot)
        right_panel.addWidget(btn_save)
        right_panel.addLayout(self.settings_buttons())

        main_layout.addLayout(left_panel, 2)
        main_layout.addLayout(plot_area_layout, 5)
        main_layout.addLayout(right_panel, 2)

        # Connect all right panel controls for instant plotting
        for widget in [
            self.xlabel, self.ylabel, self.plot_font_size, self.axis_label_font_size,
            self.line_width, self.spine_width, self.major_tick_length, self.minor_tick_length,
            self.major_tick_width, self.minor_tick_width, self.x_axis_min, self.x_axis_max,
            self.y_axis_min, self.y_axis_max,
            self.bold_axis_labels, self.bold_legend, self.bold_ticks,
            self.num_major_ticks_x, self.num_minor_ticks_x, self.num_major_ticks_y, self.num_minor_ticks_y,
            self.font_family
        ]:
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.plot_data)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.plot_data)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.plot_data)

    def on_auto_arrange_changed(self, state):
        manual = not self.auto_arrange_legends.isChecked()
        self.legend_order_list.setEnabled(manual)
        self.plot_data()

    def add_data_file(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Open Data Files", "",
            "Data Files (*.csv *.txt *.dat);;All Files (*)")
        if not files:
            return
        errors = []
        for file in files:
            try:
                self.load_file(file)
            except Exception as e:
                errors.append(f"{file.split('/')[-1]}: {e}")
        self.plot_data()
        if errors:
            QMessageBox.critical(self, "Load Error",
                                 "These files could not be loaded:\n\n" + "\n".join(errors))

    def load_file(self, file):
        with open(file, 'r') as f:
            lines = f.readlines()

        sep = ',' if file.endswith('.csv') else None

        # np.savetxt writes the header as "# col1 col2", so drop a leading '#'
        first_line = lines[0].strip().lstrip('#').strip()

        # Determine if file has header by checking numeric conversion
        try:
            _ = [float(val) for val in first_line.split(sep) if val]
            no_header = True
        except ValueError:
            no_header = False

        if no_header:
            data = np.loadtxt(file, delimiter=sep)
            num_cols = data.shape[1]
            headers = [f"Col {i}" for i in range(num_cols)]
        else:
            data = np.loadtxt(file, delimiter=sep, skiprows=1)
            ncols = data.shape[1] if data.ndim > 1 else 1
            # Headers may contain spaces ("Frequency (GHz)"), so try tab and
            # multi-space splits before plain whitespace and keep the one that fits
            candidates = [first_line.split(sep)] if sep else []
            candidates += [first_line.split('\t'), re.split(r'\s{2,}', first_line), first_line.split()]
            headers = None
            for cand in candidates:
                cand = [h.strip() for h in cand if h.strip()]
                if len(cand) == ncols:
                    headers = cand
                    break
            if headers is None:
                headers = [h.strip() for h in first_line.split(sep) if h.strip()]
            if len(headers) != ncols:
                raise RuntimeError(
                    f"Number of columns in header ({len(headers)}) doesn't match data ({data.shape[1]})."
                )
        self.files.append((file, data, headers))
        entry = FileEntry(file, self._next_uid, on_change_callback=self.plot_data,
                          on_remove_callback=self.remove_file)
        self._next_uid += 1
        entry.set_columns(headers)
        self.file_entries.append(entry)
        self.file_list_layout.insertWidget(self.file_list_layout.count() - 1, entry)

    def remove_file(self, entry, replot=True):
        i = self.file_entries.index(entry)
        del self.file_entries[i]
        del self.files[i]
        # Forget the peaks and voltages of this file's curves
        prefix = f"{entry.uid}:"
        self.peaks = {k: v for k, v in self.peaks.items() if not k.startswith(prefix)}
        self.voltages = {k: v for k, v in self.voltages.items() if not k.startswith(prefix)}
        if self.er_ref_key and self.er_ref_key.startswith(prefix):
            self.er_ref_key = None
        self.file_list_layout.removeWidget(entry)
        entry.setParent(None)  # hide it now; deleteLater only runs on the next event loop pass
        entry.deleteLater()
        if replot:
            self.plot_data()

    def remove_selected_files(self):
        selected = [e for e in self.file_entries if e.select_box.isChecked()]
        if not selected:
            QMessageBox.information(self, "Remove Selected",
                                    "Tick the box next to a file name to select it.")
            return
        for entry in selected:
            self.remove_file(entry, replot=False)
        self.plot_data()

    def toggle_select_all(self):
        select = not all(e.select_box.isChecked() for e in self.file_entries)
        for e in self.file_entries:
            e.select_box.setChecked(select)

    def clear_data(self):
        self.files = []
        self.file_entries = []
        self.peaks = {}
        self.voltages = {}
        self.er_ref_key = None
        while self.file_list_layout.count() > 1:
            item = self.file_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.legend_order_list.clear()
        self.plot_data()

    def sync_legend_order_list(self, line_labels):
        self.legend_order_list.blockSignals(True)
        self.legend_order_list.clear()
        for label in line_labels:
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
            self.legend_order_list.addItem(item)
        self.legend_order_list.blockSignals(False)

    def plot_data(self):
        self.fig.clf()
        self.ax = self.fig.add_subplot(111)
        self.lines = []
        self.curves = []

        self.annot = self.ax.annotate(
            "", xy=(0, 0), xytext=(15, 15), textcoords="offset points",
            bbox=dict(boxstyle="round", fc="w"),
            arrowprops=dict(arrowstyle="->")
        )
        self.annot.set_visible(False)
        self.canvas.mpl_connect("motion_notify_event", self.on_hover)

        color_idx = 0
        n_colors = len(COLOR_NAMES)

        label_list_for_legend = []

        for (fname, data, headers), entry in zip(self.files, self.file_entries):
            try:
                opts = entry.get()
                x = data[:, opts['x_col']] * opts['x_scale']
                for y_col in opts['y_cols']:
                    y = data[:, y_col]
                    if opts['y_square']:
                        y = y ** 2
                    # Column settings override the file settings when set
                    cs = opts['col_styles'].get(y_col, {})
                    marker = cs.get('marker') or opts['marker']
                    if marker == 'None':
                        marker = None
                    marker_size = cs.get('marker_size') or opts['marker_size']
                    line_style = cs.get('line_style') or opts['line_style']
                    line_width = cs.get('line_width') or self._num(self.line_width, 3)
                    # Use edited y column titles without square brackets
                    col_lbl = opts['headers'][y_col] if y_col < len(opts['headers']) else str(y_col)
                    color = cs.get('color') or opts.get('color')
                    if not color:
                        color = COLOR_NAMES[color_idx % n_colors]
                        color_idx += 1
                    # No square brackets in legend label
                    label = f"{opts['legend']} {col_lbl}"
                    line, = self.ax.plot(
                        x, y,
                        linewidth=line_width,
                        color=color,
                        label=label,
                        marker=marker,
                        markersize=marker_size,
                        linestyle=line_style,
                        picker=5
                    )
                    self.lines.append((line, x, y, opts['legend'], marker, marker_size, color))
                    label_list_for_legend.append(label)
                    self.curves.append({'key': f"{entry.uid}:{y_col}", 'label': label,
                                        'x': x, 'y': y, 'color': color})
            except Exception as e:
                QMessageBox.critical(self, "Plot Error", f"File: {fname}\nError: {str(e)}")
                return

        # Mark selected peaks (labels starting with '_' stay out of the legend)
        for c in self.curves:
            pk = self.peaks.get(c['key'])
            if pk is not None:
                self.ax.plot(pk[0], pk[1], linestyle='None', marker='*', markersize=20,
                             color=c['color'], markeredgecolor='black', label='_peak', zorder=5)

        # Refresh the curve chooser for peak picking, keeping the current choice
        current = self.peak_curve_combo.currentData()
        self.peak_curve_combo.clear()
        self.peak_curve_combo.addItem(ALL_CURVES_TEXT, "")
        for c in self.curves:
            self.peak_curve_combo.addItem(c['label'], c['key'])
        self.peak_curve_combo.setCurrentIndex(max(self.peak_curve_combo.findData(current), 0))

        # Set axis limits if provided
        try:
            if self.x_axis_min.text().strip() and self.x_axis_max.text().strip():
                xmin = float(self.x_axis_min.text())
                xmax = float(self.x_axis_max.text())
                self.ax.set_xlim(xmin, xmax)
            else:
                xmin, xmax = self.ax.get_xlim()
            if self.y_axis_min.text().strip() and self.y_axis_max.text().strip():
                ymin = float(self.y_axis_min.text())
                ymax = float(self.y_axis_max.text())
                self.ax.set_ylim(ymin, ymax)
            else:
                ymin, ymax = self.ax.get_ylim()
        except Exception:
            xmin, xmax = self.ax.get_xlim()
            ymin, ymax = self.ax.get_ylim()

        # Configure ticks x-axis
        try:
            num_major_x = int(self.num_major_ticks_x.currentText())
            major_ticks_x = np.linspace(xmin, xmax, num_major_x)
            self.ax.set_xticks(major_ticks_x)
            num_minor_x = int(self.num_minor_ticks_x.currentText())
            if num_minor_x > 0 and len(major_ticks_x) > 1:
                minor_ticks_x = []
                for i in range(len(major_ticks_x) - 1):
                    start = major_ticks_x[i]
                    end = major_ticks_x[i + 1]
                    step = (end - start) / (num_minor_x + 1)
                    for j in range(1, num_minor_x + 1):
                        minor_ticks_x.append(start + j * step)
                self.ax.set_xticks(minor_ticks_x, minor=True)
            else:
                self.ax.set_xticks([], minor=True)
        except Exception:
            pass

        # Configure ticks y-axis
        try:
            num_major_y = int(self.num_major_ticks_y.currentText())
            major_ticks_y = np.linspace(ymin, ymax, num_major_y)
            self.ax.set_yticks(major_ticks_y)
            num_minor_y = int(self.num_minor_ticks_y.currentText())
            if num_minor_y > 0 and len(major_ticks_y) > 1:
                minor_ticks_y = []
                for i in range(len(major_ticks_y) - 1):
                    start = major_ticks_y[i]
                    end = major_ticks_y[i + 1]
                    step = (end - start) / (num_minor_y + 1)
                    for j in range(1, num_minor_y + 1):
                        minor_ticks_y.append(start + j * step)
                self.ax.set_yticks(minor_ticks_y, minor=True)
            else:
                self.ax.set_yticks([], minor=True)
        except Exception:
            pass

        # Axis labels and styles
        label_weight = 'bold' if self.bold_axis_labels.isChecked() else 'normal'
        self.ax.set_xlabel(self.xlabel.text(), fontsize=int(self.axis_label_font_size.text()), fontweight=label_weight)
        self.ax.set_ylabel(self.ylabel.text(), fontsize=int(self.axis_label_font_size.text()), fontweight=label_weight)

        manual = not self.auto_arrange_legends.isChecked()
        lines_and_labels = [(line, line.get_label()) for line in self.ax.get_lines() if not line.get_label().startswith('_')]
        label_to_line = {label: line for line, label in lines_and_labels}

        if manual:
            # Use the manual user-edited and reordered legend list
            displayed_labels = [self.legend_order_list.item(i).text() for i in range(self.legend_order_list.count())]
            displayed_labels = [lbl for lbl in displayed_labels if lbl in label_to_line]
            # Add any new lines not in list
            new_labels = [lbl for lbl in label_to_line if lbl not in displayed_labels]
            all_labels = displayed_labels + new_labels
            manual_lines = [label_to_line[label] for label in all_labels]
            self.sync_legend_order_list(all_labels)
            used_labels = all_labels
        else:
            # Auto arrange: Use plotted order and update list widget for preview
            used_labels = label_list_for_legend
            manual_lines = [label_to_line[label] for label in used_labels if label in label_to_line]
            self.sync_legend_order_list(used_labels)
            self.legend_order_list.setEnabled(False)

        legend_weight = 'bold' if self.bold_legend.isChecked() else 'normal'
        loc_str = self.legend_location.currentText()
        if loc_str in OUTSIDE_LEGEND_MAP:
            params = OUTSIDE_LEGEND_MAP[loc_str]
            leg = self.ax.legend(manual_lines, used_labels, fontsize=int(self.plot_font_size.text()) - 2, frameon=False,
                        loc=params["loc"], bbox_to_anchor=params["bbox_to_anchor"], borderaxespad=0.)
        else:
            leg = self.ax.legend(manual_lines, used_labels, fontsize=int(self.plot_font_size.text()) - 2, frameon=False,
                                 loc=loc_str)

        for text in leg.get_texts():
            text.set_fontweight(legend_weight)

        # Tick styles
        self.ax.tick_params(axis='both', which='major', direction='in',
                            length=float(self.major_tick_length.text()) if self.major_tick_length.text() else 6,
                            width=float(self.major_tick_width.text()) if self.major_tick_width.text() else 2,
                            labelsize=int(self.plot_font_size.text()) if self.plot_font_size.text() else 24)
        self.ax.tick_params(axis='both', which='minor', direction='in',
                            length=float(self.minor_tick_length.text()) if self.minor_tick_length.text() else 3,
                            width=float(self.minor_tick_width.text()) if self.minor_tick_width.text() else 1)

        tick_weight = 'bold' if self.bold_ticks.isChecked() else 'normal'
        for label in self.ax.get_xticklabels() + self.ax.get_yticklabels():
            label.set_fontweight(tick_weight)

        # Spine width
        for spine in self.ax.spines.values():
            spine.set_linewidth(float(self.spine_width.text()) if self.spine_width.text() else 4)

        apply_font(self.fig, self.font_family.currentText())
        self.fig.tight_layout()
        self.canvas.draw()
        self.refresh_er_tab()

    # ---------- Peak selection ----------

    def _snap_index(self, y, idx):
        if not self.snap_local_max.isChecked():
            return idx
        try:
            w = max(0, int(self.snap_window.text()))
        except ValueError:
            w = 0
        lo, hi = max(0, idx - w), min(len(y), idx + w + 1)
        return lo + int(np.nanargmax(y[lo:hi]))

    def on_click(self, event):
        if getattr(self.canvas.toolbar, "mode", ""):
            return  # pan / zoom is active on the graph toolbar
        if (not self.pick_peak_mode.isChecked() or event.inaxes != self.ax
                or event.button != 1 or not self.curves):
            return
        target = self.peak_curve_combo.currentData()
        if target:
            curve = next((c for c in self.curves if c['key'] == target), None)
            if curve is None:
                return
            # Chosen curve: take the point closest in X to the click
            idx = int(np.nanargmin(np.abs(curve['x'] - event.xdata)))
        else:
            # No curve chosen: take the point nearest the click on screen
            best = None
            for c in self.curves:
                pts = self.ax.transData.transform(np.column_stack([c['x'], c['y']]))
                d = np.hypot(pts[:, 0] - event.x, pts[:, 1] - event.y)
                i = int(np.nanargmin(d))
                if best is None or d[i] < best[0]:
                    best = (d[i], c, i)
            _, curve, idx = best
        idx = self._snap_index(curve['y'], idx)
        self.peaks[curve['key']] = (float(curve['x'][idx]), float(curve['y'][idx]))
        self.plot_data()

    def auto_pick_peaks(self):
        """Take the maximum Y inside the current X axis range for the chosen curve (or all)."""
        xmin, xmax = self.ax.get_xlim()
        target = self.peak_curve_combo.currentData()
        for c in self.curves:
            if target and c['key'] != target:
                continue
            mask = (c['x'] >= xmin) & (c['x'] <= xmax) & np.isfinite(c['y'])
            if not mask.any():
                continue
            idxs = np.flatnonzero(mask)
            idx = idxs[int(np.argmax(c['y'][idxs]))]
            self.peaks[c['key']] = (float(c['x'][idx]), float(c['y'][idx]))
        self.plot_data()

    def clear_peaks(self):
        target = self.peak_curve_combo.currentData()
        if target:
            self.peaks.pop(target, None)
        else:
            self.peaks = {}
        self.plot_data()

    # ---------- Extinction ratio tab ----------

    def init_er_tab(self):
        er_tab = QWidget()
        self.tabs.addTab(er_tab, "Extinction Ratio / IL")
        layout = QHBoxLayout(er_tab)

        # Left: table of peaks and voltages
        left = QVBoxLayout()
        left.addWidget(QLabel("Selected peaks (edit the Voltage column):"))
        self.er_table = QTableWidget(0, 6)
        self.er_table.setHorizontalHeaderLabels(
            ["Curve", "V (V)", "Peak X", "Peak T", "ER", "IL"])
        header = self.er_table.horizontalHeader()
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        self.er_table.itemChanged.connect(self.on_er_table_changed)
        left.addWidget(self.er_table)

        # Center: plot
        self.er_fig, self.er_ax = plt.subplots(figsize=(8, 6), dpi=100)
        self.er_canvas = FigureCanvas(self.er_fig)

        # Right: settings (same plot options as the Transmission tab, in a scroll area)
        self.er_quantity = QComboBox()
        self.er_quantity.addItems([QTY_ER, QTY_IL, QTY_BOTH])
        self.er_reference = QComboBox()
        self.er_definition = QComboBox()
        self.er_definition.addItems(ER_DEFINITIONS)
        self.er_scale = QComboBox()
        self.er_scale.addItems(ER_SCALES)

        self.er_title = QLineEdit("")
        self.er_font_family = make_font_combo()
        self.er_xlabel = QLineEdit("Voltage [V]")
        self.er_ylabel = QLineEdit("Extinction Ratio")
        self.er_add_db_unit = QCheckBox("Add [dB] to Y Label")
        self.er_add_db_unit.setChecked(True)
        self.er_font_size = QLineEdit("24")
        self.er_axis_label_font_size = QLineEdit("24")
        self.er_title_font_size = QLineEdit("24")
        self.er_legend_font_size = QLineEdit("22")

        self.er_marker = QComboBox()
        self.er_marker.addItems([name for name, _ in MARKER_OPTIONS])
        self.er_marker.setCurrentText("Circle")
        self.er_marker_size = QLineEdit("10")
        self.er_line_style = QComboBox()
        self.er_line_style.addItems(["None"] + [name for name, _ in LINE_STYLE_OPTIONS])
        self.er_line_style.setCurrentText("Solid")
        self.er_line_width = QLineEdit("3")
        self.er_color = QComboBox()
        self.er_color.addItems(COLOR_NAMES)
        self.er_spine_width = QLineEdit("4")

        self.er_xmin = QLineEdit("")
        self.er_xmax = QLineEdit("")
        self.er_ymin = QLineEdit("")
        self.er_ymax = QLineEdit("")

        self.er_major_tick_length = QLineEdit("6")
        self.er_minor_tick_length = QLineEdit("3")
        self.er_major_tick_width = QLineEdit("2")
        self.er_minor_tick_width = QLineEdit("1")
        # "Auto" lets matplotlib choose round tick values
        self.er_num_major_ticks_x = QComboBox()
        self.er_num_major_ticks_x.addItems(["Auto"] + [str(i) for i in range(2, 11)])
        self.er_num_minor_ticks_x = QComboBox()
        self.er_num_minor_ticks_x.addItems([str(i) for i in range(0, 6)])
        self.er_num_minor_ticks_x.setCurrentText("4")
        self.er_num_major_ticks_y = QComboBox()
        self.er_num_major_ticks_y.addItems(["Auto"] + [str(i) for i in range(2, 11)])
        self.er_num_minor_ticks_y = QComboBox()
        self.er_num_minor_ticks_y.addItems([str(i) for i in range(0, 6)])
        self.er_num_minor_ticks_y.setCurrentText("4")

        self.er_bold_axis_labels = QCheckBox("Bold Axis Labels")
        self.er_bold_axis_labels.setChecked(True)
        self.er_bold_title = QCheckBox("Bold Title")
        self.er_bold_title.setChecked(True)
        self.er_bold_legend = QCheckBox("Bold Legend")
        self.er_bold_legend.setChecked(True)
        self.er_bold_ticks = QCheckBox("Bold Tick Labels")
        self.er_bold_ticks.setChecked(True)

        self.er_show_legend = QCheckBox("Show Legend")
        self.er_legend_label = QLineEdit("Extinction Ratio")
        self.er_legend_location = QComboBox()
        self.er_legend_location.addItems(LEGEND_LOCATION_OPTIONS)

        # Right Y axis (IL), used only in "ER (left) + IL (right)" mode
        self.er2_ylabel = QLineEdit("Insertion Loss")
        self.er2_legend_label = QLineEdit("Insertion Loss")
        self.er2_marker = QComboBox()
        self.er2_marker.addItems([name for name, _ in MARKER_OPTIONS])
        self.er2_marker.setCurrentText("Square")
        self.er2_marker_size = QLineEdit("10")
        self.er2_line_style = QComboBox()
        self.er2_line_style.addItems(["None"] + [name for name, _ in LINE_STYLE_OPTIONS])
        self.er2_line_style.setCurrentText("Dashed")
        self.er2_line_width = QLineEdit("3")
        self.er2_color = QComboBox()
        self.er2_color.addItems(COLOR_NAMES)
        self.er2_color.setCurrentText("red")
        self.er2_ymin = QLineEdit("")
        self.er2_ymax = QLineEdit("")
        self.er2_num_major_ticks_y = QComboBox()
        self.er2_num_major_ticks_y.addItems(["Auto"] + [str(i) for i in range(2, 11)])
        self.er2_num_minor_ticks_y = QComboBox()
        self.er2_num_minor_ticks_y.addItems([str(i) for i in range(0, 6)])
        self.er2_num_minor_ticks_y.setCurrentText("4")
        self.er2_color_axes = QCheckBox("Color each Y axis like its line")
        self.er2_color_axes.setChecked(True)

        self.er_save_width = QLineEdit("10")
        self.er_save_height = QLineEdit("6")
        self.er_save_dpi = QLineEdit("300")

        grid = QtWidgets.QGridLayout()
        rows = [
            ("Plot Quantity", self.er_quantity),
            ("Reference (T_off)", self.er_reference),
            ("ER Definition", self.er_definition),
            ("Scale", self.er_scale),
            ("Font", self.er_font_family),
            ("Plot Title", self.er_title),
            ("X Label", self.er_xlabel),
            ("Y Label (left)", self.er_ylabel),
            (None, self.er_add_db_unit),
            ("Font Size (Ticks)", self.er_font_size),
            ("Axis Label Font Size", self.er_axis_label_font_size),
            ("Title Font Size", self.er_title_font_size),
            ("Legend Font Size", self.er_legend_font_size),
            ("Marker", self.er_marker),
            ("Marker Size", self.er_marker_size),
            ("Line Style", self.er_line_style),
            ("Line Width", self.er_line_width),
            ("Color", self.er_color),
            ("Spine Width", self.er_spine_width),
            ("X Axis Min", self.er_xmin),
            ("X Axis Max", self.er_xmax),
            ("Y Axis Min", self.er_ymin),
            ("Y Axis Max", self.er_ymax),
            ("Major Tick Length", self.er_major_tick_length),
            ("Minor Tick Length", self.er_minor_tick_length),
            ("Major Tick Width", self.er_major_tick_width),
            ("Minor Tick Width", self.er_minor_tick_width),
            ("Major Ticks X", self.er_num_major_ticks_x),
            ("Minor Ticks X Between", self.er_num_minor_ticks_x),
            ("Major Ticks Y", self.er_num_major_ticks_y),
            ("Minor Ticks Y Between", self.er_num_minor_ticks_y),
            (None, self.er_bold_axis_labels),
            (None, self.er_bold_title),
            (None, self.er_bold_legend),
            (None, self.er_bold_ticks),
            (None, self.er_show_legend),
            ("Legend Label (left)", self.er_legend_label),
            ("Legend Location", self.er_legend_location),
            ("Save Width (in)", self.er_save_width),
            ("Save Height (in)", self.er_save_height),
            ("Save DPI", self.er_save_dpi),
            (None, QLabel("<b>Right Y axis: IL</b> (ER + IL mode only)")),
            ("Right Y Label", self.er2_ylabel),
            ("Right Legend Label", self.er2_legend_label),
            ("Right Marker", self.er2_marker),
            ("Right Marker Size", self.er2_marker_size),
            ("Right Line Style", self.er2_line_style),
            ("Right Line Width", self.er2_line_width),
            ("Right Color", self.er2_color),
            ("Right Y Axis Min", self.er2_ymin),
            ("Right Y Axis Max", self.er2_ymax),
            ("Right Major Ticks Y", self.er2_num_major_ticks_y),
            ("Right Minor Ticks Y Between", self.er2_num_minor_ticks_y),
            (None, self.er2_color_axes),
        ]
        for r, (text, widget) in enumerate(rows):
            if text is None:
                grid.addWidget(widget, r, 0, 1, 2)
            else:
                grid.addWidget(QLabel(text), r, 0)
                grid.addWidget(widget, r, 1)

        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        btn_copy_style = QPushButton("Copy Style from Transmission Tab")
        btn_copy_style.clicked.connect(self.copy_style_from_transmission)
        settings_layout.addWidget(btn_copy_style)
        settings_layout.addLayout(grid)
        settings_layout.addStretch()
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings)

        right = QVBoxLayout()
        right.addWidget(settings_scroll)
        btn_plot = QPushButton("Plot")
        btn_plot.clicked.connect(self.refresh_er_tab)
        btn_save = QPushButton("Save Plot (PNG, JPEG, PDF, SVG, ...)")
        btn_save.clicked.connect(self.save_er_plot)
        btn_export = QPushButton("Export ER / IL Data (CSV)")
        btn_export.clicked.connect(self.export_er_csv)
        right.addWidget(btn_plot)
        right.addWidget(btn_save)
        right.addWidget(btn_export)
        right.addLayout(self.settings_buttons())

        layout.addLayout(left, 3)
        layout.addWidget(self.er_canvas, 5)
        layout.addLayout(right, 3)

        # Options that change the ER values rebuild the table; the rest only redraw
        self.er_reference.currentIndexChanged.connect(self.on_er_reference_changed)
        self.er_definition.currentIndexChanged.connect(self.refresh_er_tab)
        self.er_scale.currentIndexChanged.connect(self.refresh_er_tab)
        self.er_quantity.currentIndexChanged.connect(self.on_er_quantity_changed)
        for widget in self.findChildren(QWidget):
            if widget.parent() is not settings or widget in (
                    self.er_quantity, self.er_reference, self.er_definition, self.er_scale):
                continue
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.plot_er)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.plot_er)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.plot_er)
        for widget in self._er2_widgets():
            widget.setEnabled(False)  # enabled in "ER (left) + IL (right)" mode

    def on_er_quantity_changed(self):
        qty = self.er_quantity.currentText()
        is_il = qty == QTY_IL
        # Reference and ER definition only apply to the extinction ratio
        self.er_reference.setEnabled(not is_il)
        self.er_definition.setEnabled(not is_il)
        for widget in self._er2_widgets():
            widget.setEnabled(qty == QTY_BOTH)
        # Swap labels that are still at their defaults; keep labels the user typed
        defaults = set(QTY_DEFAULT_LABELS.values())
        for edit in (self.er_ylabel, self.er_legend_label):
            if edit.text() in defaults:
                edit.blockSignals(True)
                edit.setText(QTY_DEFAULT_LABELS[qty])
                edit.blockSignals(False)
        self.refresh_er_tab()

    def _er2_widgets(self):
        return [self.er2_ylabel, self.er2_legend_label, self.er2_marker, self.er2_marker_size,
                self.er2_line_style, self.er2_line_width, self.er2_color, self.er2_ymin,
                self.er2_ymax, self.er2_num_major_ticks_y, self.er2_num_minor_ticks_y,
                self.er2_color_axes]

    def copy_style_from_transmission(self):
        """Copy fonts, line, tick, bold, legend and save settings from the Transmission tab."""
        self._copy_transmission_style(
            [self.er_font_size, self.er_axis_label_font_size, self.er_line_width,
             self.er_spine_width, self.er_major_tick_length, self.er_minor_tick_length,
             self.er_major_tick_width, self.er_minor_tick_width,
             self.er_num_major_ticks_x, self.er_num_minor_ticks_x,
             self.er_num_major_ticks_y, self.er_num_minor_ticks_y,
             self.er_bold_axis_labels, self.er_bold_legend, self.er_bold_ticks,
             self.er_legend_location, self.er_font_family,
             self.er_save_width, self.er_save_height, self.er_save_dpi],
            self.er_legend_font_size, self.plot_er)

    def _copy_transmission_style(self, targets, legend_size_edit, replot):
        """Copy the Transmission tab's style widgets onto targets (same order as sources)."""
        sources = [
            self.plot_font_size, self.axis_label_font_size, self.line_width,
            self.spine_width, self.major_tick_length, self.minor_tick_length,
            self.major_tick_width, self.minor_tick_width,
            self.num_major_ticks_x, self.num_minor_ticks_x,
            self.num_major_ticks_y, self.num_minor_ticks_y,
            self.bold_axis_labels, self.bold_legend, self.bold_ticks,
            self.legend_location, self.font_family,
            self.save_width, self.save_height, self.save_dpi,
        ]
        for src, dst in zip(sources, targets):
            dst.blockSignals(True)
            if isinstance(src, QLineEdit):
                dst.setText(src.text())
            elif isinstance(src, QCheckBox):
                dst.setChecked(src.isChecked())
            else:
                dst.setCurrentText(src.currentText())
            dst.blockSignals(False)
        # Tab 1 uses the tick font size minus 2 for the legend
        legend_size_edit.blockSignals(True)
        legend_size_edit.setText(str(self._num(self.plot_font_size, 24, int) - 2))
        legend_size_edit.blockSignals(False)
        replot()

    def compute_er_rows(self):
        """One row per plotted curve: label, key, voltage text, peak (x, y),
        'er' and 'il' (None where undefined)."""
        ref_key = self.er_reference.currentData()
        ref_pk = self.peaks.get(ref_key) if ref_key else None
        on_over_off = self.er_definition.currentText() == ER_ON_OFF
        scale = self.er_scale.currentText()
        factor = 20 if scale == ER_DB_AMPLITUDE else 10
        rows = []
        for c in self.curves:
            key = c['key']
            if key not in self.voltages:
                self.voltages[key] = guess_voltage(c['label'])
            pk = self.peaks.get(key)
            er = il = None
            if pk is not None:
                # IL = -20·log10(T_on) (or -10·log10); the linear scale shows T_on itself
                if scale == ER_LINEAR:
                    il = pk[1]
                elif pk[1] > 0:
                    il = -factor * np.log10(pk[1])
                if ref_pk is not None:
                    num, den = (pk[1], ref_pk[1]) if on_over_off else (ref_pk[1], pk[1])
                    if den != 0:
                        er = num / den
                        if scale != ER_LINEAR:
                            er = factor * np.log10(er) if er > 0 else None
            rows.append({'label': c['label'], 'key': key, 'voltage': self.voltages[key],
                         'peak': pk, 'er': er, 'il': il})
        return rows

    def refresh_er_tab(self):
        # Reference choices: curves that have a peak. Unless the user chose one,
        # default to the 0 V curve, then to the first curve.
        self.er_reference.blockSignals(True)
        self.er_reference.clear()
        for c in self.curves:
            if c['key'] in self.peaks:
                self.er_reference.addItem(c['label'], c['key'])
        idx = self.er_reference.findData(self.er_ref_key) if self.er_ref_key else -1
        if idx < 0:
            for i in range(self.er_reference.count()):
                v = self.voltages.get(self.er_reference.itemData(i)) or guess_voltage(self.er_reference.itemText(i))
                try:
                    if float(v) == 0:
                        idx = i
                        break
                except ValueError:
                    pass
        self.er_reference.setCurrentIndex(max(idx, 0) if self.er_reference.count() else -1)
        self.er_reference.blockSignals(False)

        rows = self.compute_er_rows()
        self.er_table.blockSignals(True)
        self.er_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            pk = row['peak']
            cells = [
                row['label'],
                row['voltage'],
                f"{pk[0]:.4g}" if pk else "",
                f"{pk[1]:.4g}" if pk else "",
                f"{row['er']:.4g}" if row['er'] is not None else "",
                f"{row['il']:.4g}" if row['il'] is not None else "",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col != 1:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                if col == 0:
                    item.setData(QtCore.Qt.UserRole, row['key'])
                self.er_table.setItem(r, col, item)
        self.er_table.blockSignals(False)
        self.plot_er()

    def on_er_reference_changed(self):
        self.er_ref_key = self.er_reference.currentData()
        self.refresh_er_tab()

    def on_er_table_changed(self, item):
        if item.column() != 1:
            return
        key = self.er_table.item(item.row(), 0).data(QtCore.Qt.UserRole)
        self.voltages[key] = item.text().strip()
        # Rebuild after the edit finishes, not inside the table's own signal
        QtCore.QTimer.singleShot(0, self.refresh_er_tab)

    def er_points(self, kind=None):
        """(voltage, value, label) sorted by voltage, for rows with a numeric voltage.
        kind is 'er' or 'il'; by default the single quantity chosen in Plot Quantity."""
        if kind is None:
            kind = 'il' if self.er_quantity.currentText() == QTY_IL else 'er'
        pts = []
        for row in self.compute_er_rows():
            if row[kind] is None:
                continue
            try:
                v = float(row['voltage'])
            except ValueError:
                continue
            pts.append((v, row[kind], row['label']))
        return sorted(pts)

    @staticmethod
    def _num(widget, default, cast=float):
        try:
            return cast(widget.text())
        except ValueError:
            return default

    def _set_er_ticks(self, ax, axis, n_major_combo, n_minor_combo):
        """Evenly spaced major ticks between the axis limits (like the Transmission tab),
        or matplotlib's round values when the major count is 'Auto'."""
        n_minor = int(n_minor_combo.currentText())
        set_ticks = ax.set_xticks if axis == 'x' else ax.set_yticks
        get_lim = ax.get_xlim if axis == 'x' else ax.get_ylim
        if n_major_combo.currentText() == "Auto":
            loc = ax.xaxis if axis == 'x' else ax.yaxis
            if n_minor > 0:
                loc.set_minor_locator(AutoMinorLocator(n_minor + 1))
            else:
                set_ticks([], minor=True)
            return
        lo, hi = get_lim()
        major = np.linspace(lo, hi, int(n_major_combo.currentText()))
        set_ticks(major)
        minor = []
        for start, end in zip(major[:-1], major[1:]):
            step = (end - start) / (n_minor + 1)
            minor.extend(start + j * step for j in range(1, n_minor + 1))
        set_ticks(minor, minor=True)

    def _plot_er_series(self, ax, pts, marker_combo, size_edit, style_combo, width_edit,
                        color_combo, label_edit):
        marker = MARKER_NAME_TO_SYMBOL.get(marker_combo.currentText(), 'o')
        ls = style_combo.currentText()
        line, = ax.plot([p[0] for p in pts], [p[1] for p in pts],
                        marker=None if marker == 'None' else marker,
                        markersize=self._num(size_edit, 10),
                        linestyle='None' if ls == 'None' else LINE_STYLE_NAME_TO_SYMBOL[ls],
                        linewidth=self._num(width_edit, 3),
                        color=color_combo.currentText(),
                        label=label_edit.text())
        return line

    def _db_label(self, text):
        if (self.er_add_db_unit.isChecked() and self.er_scale.currentText() != ER_LINEAR
                and "dB" not in text):
            text += " [dB]"
        return text

    def plot_er(self):
        self.er_fig.clf()
        self.er_ax = self.er_fig.add_subplot(111)
        ax = self.er_ax
        both = self.er_quantity.currentText() == QTY_BOTH
        pts = self.er_points('er' if both else None)
        pts2 = self.er_points('il') if both else []
        ax2 = ax.twinx() if both else None

        lines = []
        if pts:
            lines.append(self._plot_er_series(
                ax, pts, self.er_marker, self.er_marker_size, self.er_line_style,
                self.er_line_width, self.er_color, self.er_legend_label))
        if pts2:
            lines.append(self._plot_er_series(
                ax2, pts2, self.er2_marker, self.er2_marker_size, self.er2_line_style,
                self.er2_line_width, self.er2_color, self.er2_legend_label))
        if not lines:
            ax.text(0.5, 0.5, "Pick peaks on the Transmission tab\nand enter a voltage for each curve",
                    transform=ax.transAxes, ha='center', va='center', fontsize=12, color='gray')

        if self.er_xmin.text().strip() and self.er_xmax.text().strip():
            ax.set_xlim(self._num(self.er_xmin, None), self._num(self.er_xmax, None))
        if self.er_ymin.text().strip() and self.er_ymax.text().strip():
            ax.set_ylim(self._num(self.er_ymin, None), self._num(self.er_ymax, None))
        if ax2 is not None and self.er2_ymin.text().strip() and self.er2_ymax.text().strip():
            ax2.set_ylim(self._num(self.er2_ymin, None), self._num(self.er2_ymax, None))

        try:
            self._set_er_ticks(ax, 'x', self.er_num_major_ticks_x, self.er_num_minor_ticks_x)
            self._set_er_ticks(ax, 'y', self.er_num_major_ticks_y, self.er_num_minor_ticks_y)
            if ax2 is not None:
                self._set_er_ticks(ax2, 'y', self.er2_num_major_ticks_y, self.er2_num_minor_ticks_y)
        except Exception:
            pass

        # Title and axis labels
        label_weight = 'bold' if self.er_bold_axis_labels.isChecked() else 'normal'
        label_size = self._num(self.er_axis_label_font_size, 24, int)
        ax.set_xlabel(self.er_xlabel.text(), fontsize=label_size, fontweight=label_weight)
        ax.set_ylabel(self._db_label(self.er_ylabel.text()), fontsize=label_size, fontweight=label_weight)
        if ax2 is not None:
            ax2.set_ylabel(self._db_label(self.er2_ylabel.text()), fontsize=label_size,
                           fontweight=label_weight)
        if self.er_title.text().strip():
            ax.set_title(self.er_title.text(),
                         fontsize=self._num(self.er_title_font_size, 24, int),
                         fontweight='bold' if self.er_bold_title.isChecked() else 'normal')

        # Legend: one box holding both lines, drawn on the top axes so it is not hidden
        if lines and self.er_show_legend.isChecked():
            legend_ax = ax2 if ax2 is not None else ax
            legend_size = self._num(self.er_legend_font_size, 22, int)
            loc_str = self.er_legend_location.currentText()
            labels = [l.get_label() for l in lines]
            if loc_str in OUTSIDE_LEGEND_MAP:
                params = OUTSIDE_LEGEND_MAP[loc_str]
                leg = legend_ax.legend(lines, labels, fontsize=legend_size, frameon=False,
                                       loc=params["loc"], bbox_to_anchor=params["bbox_to_anchor"],
                                       borderaxespad=0.)
            else:
                leg = legend_ax.legend(lines, labels, fontsize=legend_size, frameon=False, loc=loc_str)
            legend_weight = 'bold' if self.er_bold_legend.isChecked() else 'normal'
            for text in leg.get_texts():
                text.set_fontweight(legend_weight)

        # Ticks and spines
        tick_weight = 'bold' if self.er_bold_ticks.isChecked() else 'normal'
        for a in [ax] + ([ax2] if ax2 is not None else []):
            a.tick_params(axis='both', which='major', direction='in',
                          length=self._num(self.er_major_tick_length, 6),
                          width=self._num(self.er_major_tick_width, 2),
                          labelsize=self._num(self.er_font_size, 24, int))
            a.tick_params(axis='both', which='minor', direction='in',
                          length=self._num(self.er_minor_tick_length, 3),
                          width=self._num(self.er_minor_tick_width, 1))
            for label in a.get_xticklabels() + a.get_yticklabels():
                label.set_fontweight(tick_weight)
            for spine in a.spines.values():
                spine.set_linewidth(self._num(self.er_spine_width, 4))

        # Color each Y axis (label, ticks, spine) like its line
        if ax2 is not None and self.er2_color_axes.isChecked():
            for a, side, color in ((ax, 'left', self.er_color.currentText()),
                                   (ax2, 'right', self.er2_color.currentText())):
                a.yaxis.label.set_color(color)
                a.tick_params(axis='y', which='both', colors=color)
                ax2.spines[side].set_color(color)

        apply_font(self.er_fig, self.er_font_family.currentText())
        self.er_fig.tight_layout()
        self.er_canvas.draw()

    def save_er_plot(self):
        self.save_figure(self.er_fig, self.er_canvas, self.er_save_width,
                         self.er_save_height, self.er_save_dpi)

    def export_er_csv(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export ER Data", "", "CSV Files (*.csv)")
        if not file_path:
            return
        suffix = {ER_LINEAR: "", ER_DB_POWER: "_dB_10log10",
                  ER_DB_AMPLITUDE: "_dB_20log10"}[self.er_scale.currentText()]
        er_header = "ER" + suffix
        il_header = "T_on" if self.er_scale.currentText() == ER_LINEAR else "IL" + suffix
        try:
            with open(file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"# Reference: {self.er_reference.currentText()}",
                                 f"Definition: {self.er_definition.currentText()}",
                                 f"Scale: {self.er_scale.currentText()}"])
                writer.writerow(["Curve", "Voltage_V", "Peak_X", "Peak_T", er_header, il_header])
                for row in self.compute_er_rows():
                    pk = row['peak']
                    writer.writerow([row['label'], row['voltage'],
                                     pk[0] if pk else "", pk[1] if pk else "",
                                     row['er'] if row['er'] is not None else "",
                                     row['il'] if row['il'] is not None else ""])
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Could not export data: {e}")

    # ---------- Speed / 3 dB cutoff tab ----------

    def init_fc_tab(self):
        fc_tab = QWidget()
        self.tabs.addTab(fc_tab, "Speed / 3 dB fc")
        layout = QHBoxLayout(fc_tab)

        # Left: loading, the Vpp-vs-frequency table and fit results
        left = QVBoxLayout()
        btn_add_scope = QPushButton("Add Scope Captures (sine fit each)")
        btn_add_scope.clicked.connect(self.add_fc_scope_files)
        btn_add_table = QPushButton("Load Frequency vs Vpp CSV")
        btn_add_table.clicked.connect(self.add_fc_table_file)
        btn_remove = QPushButton("Remove Selected")
        btn_remove.clicked.connect(self.remove_fc_rows)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.clear_fc)
        self.fc_n_periods = QLineEdit("5")
        self.fc_n_periods.setFixedWidth(50)
        btn_refit = QPushButton("Refit Sines")
        btn_refit.clicked.connect(self.refit_fc_sines)

        left.addWidget(btn_add_scope)
        left.addWidget(btn_add_table)
        periods_row = QHBoxLayout()
        periods_row.addWidget(QLabel("Periods used in sine fit:"))
        periods_row.addWidget(self.fc_n_periods)
        periods_row.addWidget(btn_refit)
        left.addLayout(periods_row)
        edit_row = QHBoxLayout()
        edit_row.addWidget(btn_remove)
        edit_row.addWidget(btn_clear)
        left.addLayout(edit_row)

        left.addWidget(QLabel("Points (untick 'Use' to leave a point out of the fit):"))
        self.fc_table = QTableWidget(0, 5)
        self.fc_table.setHorizontalHeaderLabels(["Use", "Source", "f (Hz)", "Vpp (V)", "Gain (dB)"])
        header = self.fc_table.horizontalHeader()
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.fc_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.fc_table.itemChanged.connect(self.on_fc_table_changed)
        self.fc_table.itemSelectionChanged.connect(self.on_fc_selection_changed)
        left.addWidget(self.fc_table, 1)

        self.fc_results = QLabel("Load scope captures or a frequency vs Vpp CSV.")
        self.fc_results.setWordWrap(True)
        self.fc_results.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        left.addWidget(self.fc_results)

        # Center: plot
        self.fc_fig, self.fc_ax = plt.subplots(figsize=(8, 6), dpi=100)
        self.fc_canvas = FigureCanvas(self.fc_fig)

        # Right: settings (same plot options as the other tabs, in a scroll area)
        self.fc_view = QComboBox()
        self.fc_view.addItems([FC_VIEW_RESPONSE, FC_VIEW_SINE])
        self.fc_y_quantity = QComboBox()
        self.fc_y_quantity.addItems([FC_Y_GAIN, FC_Y_VPP, FC_Y_NORM])
        self.fc_v0_mode = QComboBox()
        self.fc_v0_mode.addItems([FC_V0_FIT, FC_V0_LOWEST])
        self.fc_freq_unit = QComboBox()
        self.fc_freq_unit.addItems(list(FC_FREQ_UNITS))
        self.fc_freq_unit.setCurrentText("MHz")
        self.fc_log_x = QCheckBox("Log Frequency Axis")
        self.fc_log_x.setChecked(True)
        self.fc_mark_3db = QCheckBox("Mark −3 dB Point")
        self.fc_mark_3db.setChecked(True)

        self.fc_title = QLineEdit("")
        self.fc_font_family = make_font_combo()
        self.fc_xlabel = QLineEdit("")
        self.fc_ylabel = QLineEdit("")
        self.fc_font_size = QLineEdit("24")
        self.fc_axis_label_font_size = QLineEdit("24")
        self.fc_title_font_size = QLineEdit("24")
        self.fc_legend_font_size = QLineEdit("22")

        self.fc_marker = QComboBox()
        self.fc_marker.addItems([name for name, _ in MARKER_OPTIONS])
        self.fc_marker.setCurrentText("Circle")
        self.fc_marker_size = QLineEdit("10")
        self.fc_line_style = QComboBox()
        self.fc_line_style.addItems(["None"] + [name for name, _ in LINE_STYLE_OPTIONS])
        self.fc_line_width = QLineEdit("2")
        self.fc_color = QComboBox()
        self.fc_color.addItems(COLOR_NAMES)

        self.fc_fit_line_style = QComboBox()
        self.fc_fit_line_style.addItems([name for name, _ in LINE_STYLE_OPTIONS])
        self.fc_fit_line_width = QLineEdit("3")
        self.fc_fit_color = QComboBox()
        self.fc_fit_color.addItems(COLOR_NAMES)
        self.fc_fit_color.setCurrentText("red")
        self.fc_spine_width = QLineEdit("4")

        self.fc_xmin = QLineEdit("")
        self.fc_xmax = QLineEdit("")
        self.fc_ymin = QLineEdit("")
        self.fc_ymax = QLineEdit("")

        self.fc_major_tick_length = QLineEdit("6")
        self.fc_minor_tick_length = QLineEdit("3")
        self.fc_major_tick_width = QLineEdit("2")
        self.fc_minor_tick_width = QLineEdit("1")
        # "Auto" lets matplotlib choose round tick values (always used on a log axis)
        self.fc_num_major_ticks_x = QComboBox()
        self.fc_num_major_ticks_x.addItems(["Auto"] + [str(i) for i in range(2, 11)])
        self.fc_num_minor_ticks_x = QComboBox()
        self.fc_num_minor_ticks_x.addItems([str(i) for i in range(0, 6)])
        self.fc_num_minor_ticks_x.setCurrentText("4")
        self.fc_num_major_ticks_y = QComboBox()
        self.fc_num_major_ticks_y.addItems(["Auto"] + [str(i) for i in range(2, 11)])
        self.fc_num_minor_ticks_y = QComboBox()
        self.fc_num_minor_ticks_y.addItems([str(i) for i in range(0, 6)])
        self.fc_num_minor_ticks_y.setCurrentText("4")

        self.fc_bold_axis_labels = QCheckBox("Bold Axis Labels")
        self.fc_bold_axis_labels.setChecked(True)
        self.fc_bold_title = QCheckBox("Bold Title")
        self.fc_bold_title.setChecked(True)
        self.fc_bold_legend = QCheckBox("Bold Legend")
        self.fc_bold_legend.setChecked(True)
        self.fc_bold_ticks = QCheckBox("Bold Tick Labels")
        self.fc_bold_ticks.setChecked(True)

        self.fc_show_legend = QCheckBox("Show Legend")
        self.fc_show_legend.setChecked(True)
        self.fc_legend_label = QLineEdit("Measured")
        self.fc_fit_legend_label = QLineEdit("Single-pole fit")
        self.fc_legend_location = QComboBox()
        self.fc_legend_location.addItems(LEGEND_LOCATION_OPTIONS)

        self.fc_save_width = QLineEdit("10")
        self.fc_save_height = QLineEdit("6")
        self.fc_save_dpi = QLineEdit("300")

        grid = QtWidgets.QGridLayout()
        rows = [
            ("Plot View", self.fc_view),
            ("Y Quantity", self.fc_y_quantity),
            ("V0 Reference", self.fc_v0_mode),
            ("Frequency Unit", self.fc_freq_unit),
            (None, self.fc_log_x),
            (None, self.fc_mark_3db),
            ("Font", self.fc_font_family),
            ("Plot Title", self.fc_title),
            ("X Label", self.fc_xlabel),
            ("Y Label", self.fc_ylabel),
            ("Font Size (Ticks)", self.fc_font_size),
            ("Axis Label Font Size", self.fc_axis_label_font_size),
            ("Title Font Size", self.fc_title_font_size),
            ("Legend Font Size", self.fc_legend_font_size),
            ("Data Marker", self.fc_marker),
            ("Data Marker Size", self.fc_marker_size),
            ("Data Line Style", self.fc_line_style),
            ("Data Line Width", self.fc_line_width),
            ("Data Color", self.fc_color),
            ("Fit Line Style", self.fc_fit_line_style),
            ("Fit Line Width", self.fc_fit_line_width),
            ("Fit Color", self.fc_fit_color),
            ("Spine Width", self.fc_spine_width),
            ("X Axis Min", self.fc_xmin),
            ("X Axis Max", self.fc_xmax),
            ("Y Axis Min", self.fc_ymin),
            ("Y Axis Max", self.fc_ymax),
            ("Major Tick Length", self.fc_major_tick_length),
            ("Minor Tick Length", self.fc_minor_tick_length),
            ("Major Tick Width", self.fc_major_tick_width),
            ("Minor Tick Width", self.fc_minor_tick_width),
            ("Major Ticks X", self.fc_num_major_ticks_x),
            ("Minor Ticks X Between", self.fc_num_minor_ticks_x),
            ("Major Ticks Y", self.fc_num_major_ticks_y),
            ("Minor Ticks Y Between", self.fc_num_minor_ticks_y),
            (None, self.fc_bold_axis_labels),
            (None, self.fc_bold_title),
            (None, self.fc_bold_legend),
            (None, self.fc_bold_ticks),
            (None, self.fc_show_legend),
            ("Data Legend Label", self.fc_legend_label),
            ("Fit Legend Label", self.fc_fit_legend_label),
            ("Legend Location", self.fc_legend_location),
            ("Save Width (in)", self.fc_save_width),
            ("Save Height (in)", self.fc_save_height),
            ("Save DPI", self.fc_save_dpi),
        ]
        for r, (text, widget) in enumerate(rows):
            if text is None:
                grid.addWidget(widget, r, 0, 1, 2)
            else:
                grid.addWidget(QLabel(text), r, 0)
                grid.addWidget(widget, r, 1)

        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        btn_copy_style = QPushButton("Copy Style from Transmission Tab")
        btn_copy_style.clicked.connect(self.copy_style_to_fc)
        settings_layout.addWidget(btn_copy_style)
        settings_layout.addLayout(grid)
        settings_layout.addStretch()
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings)

        right = QVBoxLayout()
        right.addWidget(settings_scroll)
        btn_plot = QPushButton("Fit && Plot")
        btn_plot.clicked.connect(self.refresh_fc_tab)
        btn_save = QPushButton("Save Plot (PNG, JPEG, PDF, SVG, ...)")
        btn_save.clicked.connect(self.save_fc_plot)
        btn_export = QPushButton("Export Points && Fit (CSV)")
        btn_export.clicked.connect(self.export_fc_csv)
        right.addWidget(btn_plot)
        right.addWidget(btn_save)
        right.addWidget(btn_export)
        right.addLayout(self.settings_buttons())

        layout.addLayout(left, 3)
        layout.addWidget(self.fc_canvas, 5)
        layout.addLayout(right, 3)

        # The V0 choice changes the fit; everything else only redraws
        self.fc_v0_mode.currentIndexChanged.connect(self.refresh_fc_tab)
        for widget in settings.findChildren(QWidget):
            if widget.parent() is not settings or widget is self.fc_v0_mode:
                continue
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.plot_fc)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.plot_fc)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.plot_fc)
        self.plot_fc()

    def copy_style_to_fc(self):
        self._copy_transmission_style(
            [self.fc_font_size, self.fc_axis_label_font_size, self.fc_fit_line_width,
             self.fc_spine_width, self.fc_major_tick_length, self.fc_minor_tick_length,
             self.fc_major_tick_width, self.fc_minor_tick_width,
             self.fc_num_major_ticks_x, self.fc_num_minor_ticks_x,
             self.fc_num_major_ticks_y, self.fc_num_minor_ticks_y,
             self.fc_bold_axis_labels, self.fc_bold_legend, self.fc_bold_ticks,
             self.fc_legend_location, self.fc_font_family,
             self.fc_save_width, self.fc_save_height, self.fc_save_dpi],
            self.fc_legend_font_size, self.plot_fc)

    def add_fc_scope_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Oscilloscope Captures", "", "CSV Files (*.csv);;All Files (*)")
        if not paths:
            return
        n_periods = self._num(self.fc_n_periods, 5)
        errors = []
        for path in paths:
            try:
                t, v = read_scope_csv(path)
                sine = fit_sine(t, v, n_periods)
                sine['raw'] = (t, v)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                continue
            self.fc_points.append({'label': os.path.basename(path), 'f': sine['f'],
                                   'vpp': sine['vpp'], 'use': True, 'sine': sine,
                                   'path': path})
        if errors:
            QMessageBox.warning(self, "Sine Fit", "Some files were skipped:\n" + "\n".join(errors))
        self.refresh_fc_tab()

    def add_fc_table_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Frequency vs Vpp CSV", "", "CSV Files (*.csv);;All Files (*)")
        if not path:
            return
        try:
            rows = read_freq_vpp_csv(path)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Could not read {path}:\n{e}")
            return
        name = os.path.basename(path)
        for i, (f, vpp) in enumerate(rows, start=1):
            self.fc_points.append({'label': f"{name} #{i}", 'f': f, 'vpp': vpp,
                                   'use': True, 'sine': None})
        self.refresh_fc_tab()

    def refit_fc_sines(self):
        n_periods = self._num(self.fc_n_periods, 5)
        for p in self.fc_points:
            if p['sine'] is None:
                continue
            t, v = p['sine']['raw']
            try:
                sine = fit_sine(t, v, n_periods)
            except Exception:
                continue
            sine['raw'] = (t, v)
            p.update(f=sine['f'], vpp=sine['vpp'], sine=sine)
        self.refresh_fc_tab()

    def remove_fc_rows(self):
        rows = sorted({i.row() for i in self.fc_table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.fc_points[r]
        self.refresh_fc_tab()

    def clear_fc(self):
        self.fc_points = []
        self.refresh_fc_tab()

    def fc_used_points(self):
        """(f, vpp) arrays of the ticked points with positive values, sorted by frequency."""
        pts = sorted((p['f'], p['vpp']) for p in self.fc_points
                     if p['use'] and p['f'] > 0 and p['vpp'] > 0)
        if not pts:
            return np.array([]), np.array([])
        f, v = zip(*pts)
        return np.array(f), np.array(v)

    def compute_fc_fit(self):
        """Fit the ticked points; None when fewer than 2 points are available."""
        f, vpp = self.fc_used_points()
        if len(f) < 2:
            return None
        v0_fixed = vpp[0] if self.fc_v0_mode.currentText() == FC_V0_LOWEST else None
        fit = fit_lowpass(f, vpp, v0_fixed)
        # Direct estimate from the data, relative to the lowest-frequency point
        fit['f3db_interp'] = interpolate_3db(f, vpp, vpp[0])
        return fit

    def refresh_fc_tab(self):
        try:
            self.fc_fit = self.compute_fc_fit()
        except Exception as e:
            self.fc_fit = None
            self.fc_results.setText(f"Fit failed: {e}")

        v0 = self.fc_fit['v0'] if self.fc_fit else None
        self.fc_table.blockSignals(True)
        self.fc_table.setRowCount(len(self.fc_points))
        for r, p in enumerate(self.fc_points):
            use = QTableWidgetItem()
            use.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled
                         | QtCore.Qt.ItemIsSelectable)
            use.setCheckState(QtCore.Qt.Checked if p['use'] else QtCore.Qt.Unchecked)
            self.fc_table.setItem(r, 0, use)
            gain = 20 * np.log10(p['vpp'] / v0) if v0 and p['vpp'] > 0 else None
            cells = [p['label'], f"{p['f']:.6g}", f"{p['vpp']:.5g}",
                     f"{gain:.2f}" if gain is not None else ""]
            for col, text in enumerate(cells, start=1):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.fc_table.setItem(r, col, item)
        self.fc_table.blockSignals(False)

        if self.fc_fit:
            unit = self.fc_freq_unit.currentText()
            scale = FC_FREQ_UNITS[unit]
            fit = self.fc_fit
            lines = [
                f"<b>f<sub>c</sub> (−3 dB, single-pole fit) = {fit['fc'] / scale:.4g}"
                f" ± {fit['fc_err'] / scale:.2g} {unit}</b>",
                f"V0 = {fit['v0']:.5g} V"
                + (f" ± {fit['v0_err']:.2g} V" if fit['v0_err'] else " (fixed)"),
                f"RMS fit residual = {fit['rms_db']:.2f} dB",
            ]
            if fit['f3db_interp'] is not None:
                lines.append(f"Interpolated −3 dB crossing (re lowest-f point) = "
                             f"{fit['f3db_interp'] / scale:.4g} {unit}")
            else:
                lines.append("Data never fall 3 dB below the lowest-frequency point.")
            self.fc_results.setText("<br>".join(lines))
        elif len(self.fc_points) < 2 and not self.fc_results.text().startswith("Fit failed"):
            self.fc_results.setText("Need at least 2 ticked points to fit f<sub>c</sub>.")
        self.plot_fc()

    def on_fc_table_changed(self, item):
        if item.column() != 0:
            return
        self.fc_points[item.row()]['use'] = item.checkState() == QtCore.Qt.Checked
        # Refit after the edit finishes, not inside the table's own signal
        QtCore.QTimer.singleShot(0, self.refresh_fc_tab)

    def on_fc_selection_changed(self):
        if self.fc_view.currentText() == FC_VIEW_SINE:
            self.plot_fc()

    def _fc_selected_sine(self):
        """Selected row's sine fit, or the first capture with one."""
        rows = [i.row() for i in self.fc_table.selectedIndexes()]
        candidates = [self.fc_points[r] for r in rows] + self.fc_points
        return next((p for p in candidates if p['sine'] is not None), None)

    def _fc_default_labels(self):
        if self.fc_view.currentText() == FC_VIEW_SINE:
            p = self._fc_selected_sine()
            t_unit = time_unit_for(1 / p['f'])[0] if p else "s"
            return f"Time [{t_unit}]", "Voltage [V]"
        y = {FC_Y_VPP: "Vpp [V]", FC_Y_GAIN: "Gain [dB]", FC_Y_NORM: "Vpp / V0"}
        return f"Frequency [{self.fc_freq_unit.currentText()}]", y[self.fc_y_quantity.currentText()]

    def _fc_update_default_labels(self):
        """Follow view/unit changes in the axis labels, unless the user typed their own."""
        new = self._fc_default_labels()
        for edit, old, text in zip((self.fc_xlabel, self.fc_ylabel), self._fc_last_defaults, new):
            if edit.text() in ("", old):
                edit.blockSignals(True)
                edit.setText(text)
                edit.blockSignals(False)
        self._fc_last_defaults = new

    def _fc_style(self, marker_combo, size_edit, style_combo, width_edit, color_combo):
        marker = MARKER_NAME_TO_SYMBOL.get(marker_combo.currentText(), 'o') if marker_combo else 'None'
        ls = style_combo.currentText()
        return dict(marker=None if marker == 'None' else marker,
                    markersize=self._num(size_edit, 10) if size_edit else None,
                    linestyle='None' if ls == 'None' else LINE_STYLE_NAME_TO_SYMBOL[ls],
                    linewidth=self._num(width_edit, 2),
                    color=color_combo.currentText())

    def _plot_fc_response(self, ax):
        lines = []
        scale = FC_FREQ_UNITS[self.fc_freq_unit.currentText()]
        qty = self.fc_y_quantity.currentText()
        fit = self.fc_fit
        f, vpp = self.fc_used_points()
        v0 = fit['v0'] if fit else (vpp[0] if len(vpp) else 1.0)

        def to_y(v):
            if qty == FC_Y_GAIN:
                return 20 * np.log10(v / v0)
            return v / v0 if qty == FC_Y_NORM else v

        data_style = self._fc_style(self.fc_marker, self.fc_marker_size, self.fc_line_style,
                                    self.fc_line_width, self.fc_color)
        if len(f):
            line, = ax.plot(f / scale, to_y(vpp), label=self.fc_legend_label.text(), **data_style)
            lines.append(line)
        unused = [(p['f'], p['vpp']) for p in self.fc_points
                  if not p['use'] and p['f'] > 0 and p['vpp'] > 0]
        if unused:
            uf, uv = map(np.array, zip(*unused))
            line, = ax.plot(uf / scale, to_y(uv), linestyle='None', marker=data_style['marker'] or 'o',
                            markersize=data_style['markersize'], markerfacecolor='none',
                            color='gray', label="Not used in fit")
            lines.append(line)

        if fit:
            f_all = np.concatenate([f, [fit['fc']]])
            f_dense = np.geomspace(f_all.min() / 2, f_all.max() * 2, 400)
            model = fit['v0'] / np.sqrt(1 + (f_dense / fit['fc']) ** 2)
            fit_style = self._fc_style(None, None, self.fc_fit_line_style,
                                       self.fc_fit_line_width, self.fc_fit_color)
            line, = ax.plot(f_dense / scale, to_y(model), label=self.fc_fit_legend_label.text(),
                            **fit_style)
            lines.append(line)
            if self.fc_mark_3db.isChecked():
                y3 = to_y(fit['v0'] / np.sqrt(2))
                ax.axhline(y3, color='gray', linestyle=':', linewidth=1.5)
                ax.axvline(fit['fc'] / scale, color='gray', linestyle=':', linewidth=1.5)
                ax.plot([fit['fc'] / scale], [y3], marker='o', markersize=8,
                        color=fit_style['color'], linestyle='None')
                ax.annotate(f"$f_c$ = {fit['fc'] / scale:.3g} {self.fc_freq_unit.currentText()}",
                            xy=(fit['fc'] / scale, y3), xytext=(10, 10), textcoords="offset points",
                            fontsize=self._num(self.fc_legend_font_size, 22, int) - 4)

        if self.fc_log_x.isChecked():
            ax.set_xscale('log')
        if not lines:
            ax.text(0.5, 0.5, "Add scope captures (each is sine-fitted)\nor load a frequency vs Vpp CSV",
                    transform=ax.transAxes, ha='center', va='center', fontsize=12, color='gray')
        return lines

    def _plot_fc_sine(self, ax):
        p = self._fc_selected_sine()
        if p is None:
            ax.text(0.5, 0.5, "Add scope captures and select one in the table",
                    transform=ax.transAxes, ha='center', va='center', fontsize=12, color='gray')
            return []
        s = p['sine']
        t_scale = time_unit_for(1 / s['f'])[1]
        data_style = self._fc_style(self.fc_marker, self.fc_marker_size, self.fc_line_style,
                                    self.fc_line_width, self.fc_color)
        data_style['markersize'] = max(1, data_style['markersize'] / 3)  # dense samples
        d, = ax.plot(s['t'] / t_scale, s['v'], label=self.fc_legend_label.text(), **data_style)
        t_dense = np.linspace(s['t'].min(), s['t'].max(), 2000)
        fit_style = self._fc_style(None, None, self.fc_fit_line_style,
                                   self.fc_fit_line_width, self.fc_fit_color)
        unit = self.fc_freq_unit.currentText()
        m, = ax.plot(t_dense / t_scale, sine_model(t_dense, s['A'], s['f'], s['phi'], s['offset']),
                     label=f"Fit: Vpp = {s['vpp']:.4g} V, f = {s['f'] / FC_FREQ_UNITS[unit]:.4g} {unit}",
                     **fit_style)
        return [d, m]

    def plot_fc(self):
        self._fc_update_default_labels()
        self.fc_fig.clf()
        self.fc_ax = self.fc_fig.add_subplot(111)
        ax = self.fc_ax
        sine_view = self.fc_view.currentText() == FC_VIEW_SINE
        lines = self._plot_fc_sine(ax) if sine_view else self._plot_fc_response(ax)

        if self.fc_xmin.text().strip() and self.fc_xmax.text().strip():
            ax.set_xlim(self._num(self.fc_xmin, None), self._num(self.fc_xmax, None))
        if self.fc_ymin.text().strip() and self.fc_ymax.text().strip():
            ax.set_ylim(self._num(self.fc_ymin, None), self._num(self.fc_ymax, None))

        try:
            if sine_view or not self.fc_log_x.isChecked():
                self._set_er_ticks(ax, 'x', self.fc_num_major_ticks_x, self.fc_num_minor_ticks_x)
            self._set_er_ticks(ax, 'y', self.fc_num_major_ticks_y, self.fc_num_minor_ticks_y)
        except Exception:
            pass

        label_weight = 'bold' if self.fc_bold_axis_labels.isChecked() else 'normal'
        label_size = self._num(self.fc_axis_label_font_size, 24, int)
        ax.set_xlabel(self.fc_xlabel.text(), fontsize=label_size, fontweight=label_weight)
        ax.set_ylabel(self.fc_ylabel.text(), fontsize=label_size, fontweight=label_weight)
        if self.fc_title.text().strip():
            ax.set_title(self.fc_title.text(),
                         fontsize=self._num(self.fc_title_font_size, 24, int),
                         fontweight='bold' if self.fc_bold_title.isChecked() else 'normal')

        if lines and self.fc_show_legend.isChecked():
            legend_size = self._num(self.fc_legend_font_size, 22, int)
            loc_str = self.fc_legend_location.currentText()
            labels = [l.get_label() for l in lines]
            if loc_str in OUTSIDE_LEGEND_MAP:
                params = OUTSIDE_LEGEND_MAP[loc_str]
                leg = ax.legend(lines, labels, fontsize=legend_size, frameon=False,
                                loc=params["loc"], bbox_to_anchor=params["bbox_to_anchor"],
                                borderaxespad=0.)
            else:
                leg = ax.legend(lines, labels, fontsize=legend_size, frameon=False, loc=loc_str)
            legend_weight = 'bold' if self.fc_bold_legend.isChecked() else 'normal'
            for text in leg.get_texts():
                text.set_fontweight(legend_weight)

        tick_weight = 'bold' if self.fc_bold_ticks.isChecked() else 'normal'
        ax.tick_params(axis='both', which='major', direction='in',
                       length=self._num(self.fc_major_tick_length, 6),
                       width=self._num(self.fc_major_tick_width, 2),
                       labelsize=self._num(self.fc_font_size, 24, int))
        ax.tick_params(axis='both', which='minor', direction='in',
                       length=self._num(self.fc_minor_tick_length, 3),
                       width=self._num(self.fc_minor_tick_width, 1))
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_fontweight(tick_weight)
        for spine in ax.spines.values():
            spine.set_linewidth(self._num(self.fc_spine_width, 4))

        apply_font(self.fc_fig, self.fc_font_family.currentText())
        try:
            self.fc_fig.tight_layout()
        except Exception:
            pass
        self.fc_canvas.draw()

    def save_fc_plot(self):
        self.save_figure(self.fc_fig, self.fc_canvas, self.fc_save_width,
                         self.fc_save_height, self.fc_save_dpi)

    def export_fc_csv(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Speed Data", "", "CSV Files (*.csv)")
        if not file_path:
            return
        fit = self.fc_fit
        try:
            with open(file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                if fit:
                    writer.writerow(["# Model: Vpp(f) = V0 / sqrt(1 + (f/fc)^2), fitted in dB",
                                     f"V0 reference: {self.fc_v0_mode.currentText()}"])
                    writer.writerow(["# fc_Hz", fit['fc'], "fc_err_Hz", fit['fc_err'],
                                     "V0_V", fit['v0'], "V0_err_V", fit['v0_err'],
                                     "rms_residual_dB", fit['rms_db'],
                                     "f3dB_interpolated_Hz",
                                     fit['f3db_interp'] if fit['f3db_interp'] is not None else ""])
                writer.writerow(["Source", "Used", "Frequency_Hz", "Vpp_V", "Vpp_err_V",
                                 "Gain_dB_re_V0", "Fit_Vpp_V"])
                for p in self.fc_points:
                    gain = model = ""
                    if fit and p['vpp'] > 0:
                        gain = 20 * np.log10(p['vpp'] / fit['v0'])
                        model = fit['v0'] / np.sqrt(1 + (p['f'] / fit['fc']) ** 2)
                    writer.writerow([p['label'], int(p['use']), p['f'], p['vpp'],
                                     p['sine']['vpp_err'] if p['sine'] else "", gain, model])
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Could not export data: {e}")

    # ---------- Period average / rise-fall tab ----------

    def init_pa_tab(self):
        pa_tab = QWidget()
        self.tabs.addTab(pa_tab, "Period Average / Rise-Fall")
        layout = QHBoxLayout(pa_tab)

        # Left: captures, analysis options, per-file results
        left = QVBoxLayout()
        btn_add = QPushButton("Add Scope Captures (select one or many)")
        btn_add.clicked.connect(self.add_pa_files)
        btn_remove = QPushButton("Remove Selected")
        btn_remove.clicked.connect(self.remove_pa_rows)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.clear_pa)
        left.addWidget(btn_add)
        edit_row = QHBoxLayout()
        edit_row.addWidget(btn_remove)
        edit_row.addWidget(btn_clear)
        left.addLayout(edit_row)

        self.pa_freq = QLineEdit("")
        self.pa_freq.setPlaceholderText("auto (FFT)")
        self.pa_freq.setToolTip("Known signal frequency; applies to every file. Blank = detect.")
        self.pa_fmin = QLineEdit("1000")
        self.pa_fmax = QLineEdit("")
        self.pa_fmax.setPlaceholderText("fs/2")
        self.pa_refine = QCheckBox("Refine period (edge crossings)")
        self.pa_refine.setChecked(True)
        self.pa_fit = QCheckBox("Fit rise/fall edges (logistic)")
        self.pa_fit.setChecked(True)
        self.pa_abs = QCheckBox("Use |amplitude| (abs of C1)")
        self.pa_abs.setChecked(True)
        self.pa_abs.setToolTip("Analyse and plot |C1|. Rise/fall then refer to the magnitude.")
        self.pa_abs.stateChanged.connect(self.reanalyze_pa)
        self.pa_edge_frac = QLineEdit("0.45")
        self.pa_edge_frac.setToolTip("Edge fit window half-width as a fraction of the shorter plateau")
        options = QtWidgets.QGridLayout()
        for r, (text, widget) in enumerate([
                ("Frequency [Hz]", self.pa_freq),
                ("Min Freq for Auto [Hz]", self.pa_fmin),
                ("Max Freq for Auto [Hz]", self.pa_fmax),
                (None, self.pa_refine),
                (None, self.pa_fit),
                (None, self.pa_abs),
                ("Edge Window Fraction", self.pa_edge_frac)]):
            if text is None:
                options.addWidget(widget, r, 0, 1, 2)
            else:
                options.addWidget(QLabel(text), r, 0)
                options.addWidget(widget, r, 1)
        left.addLayout(options)
        btn_analyze = QPushButton("Re-analyze All")
        btn_analyze.clicked.connect(self.reanalyze_pa)
        left.addWidget(btn_analyze)

        left.addWidget(QLabel("Results (select a row to plot it):"))
        self.pa_table = QTableWidget(0, 8)
        self.pa_table.setHorizontalHeaderLabels(
            ["File", "f (Hz)", "Periods", "Rise 10–90 (ns)", "Fall 10–90 (ns)",
             "Rise 20–80 (ns)", "Fall 20–80 (ns)", "R² rise / fall"])
        header = self.pa_table.horizontalHeader()
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        self.pa_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.pa_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pa_table.itemSelectionChanged.connect(self.on_pa_selection_changed)
        left.addWidget(self.pa_table, 1)

        self.pa_report = QtWidgets.QPlainTextEdit()
        self.pa_report.setReadOnly(True)
        self.pa_report.setPlaceholderText("Edge fit report of the selected capture.")
        left.addWidget(self.pa_report, 1)

        # Center: plot
        self.pa_fig, self.pa_ax = plt.subplots(figsize=(8, 6), dpi=100)
        self.pa_canvas = FigureCanvas(self.pa_fig)

        # Right: view options + the standard formatting panel
        self.pa_view = QComboBox()
        self.pa_view.addItems([PA_VIEW_AVG, PA_VIEW_RISE, PA_VIEW_FALL, PA_VIEW_RECORD,
                               PA_VIEW_OVERLAY, PA_VIEW_COMPARE])
        self.pa_time_unit = QComboBox()
        self.pa_time_unit.addItems(["Auto", "ns", "µs", "ms", "s"])
        self.pa_avg_color = QComboBox()
        self.pa_avg_color.addItems(COLOR_NAMES)
        self.pa_rise_color = QComboBox()
        self.pa_rise_color.addItems(COLOR_NAMES)
        self.pa_rise_color.setCurrentText("red")
        self.pa_fall_color = QComboBox()
        self.pa_fall_color.addItems(COLOR_NAMES)
        self.pa_fall_color.setCurrentText("green")
        self.pa_show_periods = QCheckBox("Show Individual Periods")
        self.pa_show_periods.setChecked(True)
        self.pa_show_std = QCheckBox("Show ±1 std Band")
        self.pa_show_std.setChecked(True)
        self.pa_mark_10_90 = QCheckBox("Show 10% / 90% Dotted Lines")
        self.pa_mark_10_90.setChecked(True)
        self.pa_marker_size = QLineEdit("4")
        self.pa_style = StylePanel(self)

        rows = [
            ("Plot View", self.pa_view),
            ("Time Unit", self.pa_time_unit),
            ("Average Color", self.pa_avg_color),
            ("Rise Fit Color", self.pa_rise_color),
            ("Fall Fit Color", self.pa_fall_color),
            (None, self.pa_show_periods),
            (None, self.pa_show_std),
            (None, self.pa_mark_10_90),
            ("Data Marker Size (edge zoom)", self.pa_marker_size),
        ] + self.pa_style.rows()
        grid = build_settings_sections(rows, "pa")

        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        btn_copy_style = QPushButton("Copy Style from Transmission Tab")
        btn_copy_style.clicked.connect(lambda: self._copy_transmission_style(
            self.pa_style.copy_targets(), self.pa_style.legend_font_size, self.plot_pa))
        settings_layout.addWidget(btn_copy_style)
        settings_layout.addLayout(grid)
        settings_layout.addStretch()
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings)

        right = QVBoxLayout()
        right.addWidget(settings_scroll)
        btn_plot = QPushButton("Plot")
        btn_plot.clicked.connect(self.plot_pa)
        btn_save = QPushButton("Save Plot (PNG, JPEG, PDF, SVG, ...)")
        btn_save.clicked.connect(lambda: self.save_figure(
            self.pa_fig, self.pa_canvas, self.pa_style.save_width,
            self.pa_style.save_height, self.pa_style.save_dpi))
        btn_export_avg = QPushButton("Export Averaged Period (CSV)")
        btn_export_avg.clicked.connect(self.export_pa_average)
        btn_export_fit = QPushButton("Export Edge Fit Report (TXT)")
        btn_export_fit.clicked.connect(self.export_pa_report)
        btn_export_summary = QPushButton("Export Results Table (CSV, all files)")
        btn_export_summary.clicked.connect(self.export_pa_summary)
        for b in (btn_plot, btn_save, btn_export_avg, btn_export_fit, btn_export_summary):
            right.addWidget(b)
        right.addLayout(self.settings_buttons())

        layout.addLayout(left, 3)
        layout.addWidget(self.pa_canvas, 5)
        layout.addLayout(right, 3)

        for widget in settings.findChildren(QWidget):
            if widget.parent() is not settings:
                continue
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.plot_pa)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.plot_pa)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.plot_pa)
        self.plot_pa()

    def _pa_options(self):
        return dict(freq=self._num(self.pa_freq, None) if self.pa_freq.text().strip() else None,
                    fmin=self._num(self.pa_fmin, 1e3),
                    fmax=self._num(self.pa_fmax, None) if self.pa_fmax.text().strip() else None,
                    refine=self.pa_refine.isChecked(), fit=self.pa_fit.isChecked(),
                    edge_frac=self._num(self.pa_edge_frac, 0.45),
                    use_abs=self.pa_abs.isChecked())

    def _analyze_pa_paths(self, paths):
        results, errors = [], []
        opts = self._pa_options()
        for path in paths:
            try:
                results.append(analyze_period_capture(path, **opts))
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        if errors:
            QMessageBox.warning(self, "Period Average", "Some files were skipped:\n" + "\n".join(errors))
        return results

    def add_pa_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Oscilloscope Captures", "", "CSV Files (*.csv);;All Files (*)")
        if not paths:
            return
        first_new = len(self.pa_results)
        self.pa_results += self._analyze_pa_paths(paths)
        self.refresh_pa_table(select=first_new)

    def reanalyze_pa(self):
        row = self._pa_selected_row()
        self.pa_results = self._analyze_pa_paths([r['path'] for r in self.pa_results])
        self.refresh_pa_table(select=row)

    def remove_pa_rows(self):
        for r in sorted({i.row() for i in self.pa_table.selectedIndexes()}, reverse=True):
            del self.pa_results[r]
        self.refresh_pa_table(select=0)

    def clear_pa(self):
        self.pa_results = []
        self.refresh_pa_table()

    def refresh_pa_table(self, select=None):
        self.pa_table.blockSignals(True)
        self.pa_table.setRowCount(len(self.pa_results))
        for r, res in enumerate(self.pa_results):
            rise, fall = res['rise'], res['fall']

            def ns(edge, key):
                return f"{edge[key] * 1e9:.2f}" if edge else ""
            cells = [res['name'], f"{res['f0']:.6g}", str(res['n_periods']),
                     ns(rise, 'time_10_90'), ns(fall, 'time_10_90'),
                     ns(rise, 'time_20_80'), ns(fall, 'time_20_80'),
                     f"{rise['r2']:.4f} / {fall['r2']:.4f}" if rise else (res['fit_error'] or "")]
            for col, text in enumerate(cells):
                self.pa_table.setItem(r, col, QTableWidgetItem(text))
        self.pa_table.blockSignals(False)
        if select is not None and self.pa_results:
            self.pa_table.selectRow(min(select, len(self.pa_results) - 1))
        self.on_pa_selection_changed()

    def _pa_selected_row(self):
        rows = [i.row() for i in self.pa_table.selectedIndexes()]
        return rows[0] if rows else 0

    def _pa_selected(self):
        return self.pa_results[self._pa_selected_row()] if self.pa_results else None

    def on_pa_selection_changed(self):
        res = self._pa_selected()
        self.pa_report.setPlainText(pa_report_text(res) if res else "")
        self.plot_pa()

    def _pa_time_unit(self, span):
        unit = self.pa_time_unit.currentText()
        if unit == "Auto":
            return time_unit_for(span)
        return unit, {"ns": 1e-9, "µs": 1e-6, "ms": 1e-3, "s": 1.0}[unit]

    def _pa_default_labels(self, t_unit):
        view = self.pa_view.currentText()
        if view == PA_VIEW_COMPARE:
            return "Signal Frequency [kHz]", "Transition Time 10–90% [ns]"
        y = "|Amplitude| (C1) [V]" if self.pa_abs.isChecked() else "Amplitude (C1) [V]"
        if view == PA_VIEW_RECORD:
            return f"Time [{t_unit}]", y
        return f"Time within One Period [{t_unit}]", y

    def _pa_update_default_labels(self, t_unit):
        """Follow view/unit changes in the axis labels, unless the user typed their own."""
        new = self._pa_default_labels(t_unit)
        for edit, old, text in zip((self.pa_style.xlabel, self.pa_style.ylabel),
                                   self._pa_last_defaults, new):
            if edit.text() in ("", old):
                edit.blockSignals(True)
                edit.setText(text)
                edit.blockSignals(False)
        self._pa_last_defaults = new

    def plot_pa(self):
        self.pa_fig.clf()
        self.pa_ax = self.pa_fig.add_subplot(111)
        ax = self.pa_ax
        res = self._pa_selected()
        view = self.pa_view.currentText()
        lw = self._num(self.pa_style.line_width, 3)
        lines = []

        if view == PA_VIEW_COMPARE:
            t_name = "ns"
            pts = sorted((r['f0'], r['rise'], r['fall']) for r in self.pa_results if r['rise'])
            for idx, (color, label) in ((1, (self.pa_rise_color, "Rise (10–90%)")),
                                        (2, (self.pa_fall_color, "Fall (10–90%)"))):
                if pts:
                    line, = ax.plot([p[0] / 1e3 for p in pts], [p[idx]['time_10_90'] * 1e9 for p in pts],
                                    marker='o', markersize=10, linewidth=lw,
                                    color=color.currentText(), label=label)
                    lines.append(line)
        elif res is None:
            t_name = "µs"
        else:
            period = res['period']
            edge = res['rise'] if view == PA_VIEW_RISE else res['fall'] if view == PA_VIEW_FALL else None
            span = 2 * edge['half'] if edge else (res['t'][-1] if view == PA_VIEW_RECORD else period)
            t_name, ts = self._pa_time_unit(span)
            tau = res['tau']

            if view == PA_VIEW_AVG:
                if self.pa_show_periods.isChecked():
                    for k, seg in enumerate(res['segments']):
                        line, = ax.plot(tau / ts, seg, color="0.8", lw=0.6)
                        if k == 0:
                            line.set_label("Individual periods")
                            lines.append(line)
                if self.pa_show_std.isChecked():
                    lines.append(ax.fill_between(tau / ts, res['avg'] - res['std'],
                                                 res['avg'] + res['std'], alpha=0.2,
                                                 color=self.pa_avg_color.currentText(),
                                                 label="±1 std"))
                line, = ax.plot(tau / ts, res['avg'], color=self.pa_avg_color.currentText(), lw=lw,
                                label=f"Average of {res['n_periods']} periods")
                lines.append(line)
                for d, color in ((res['rise'], self.pa_rise_color), (res['fall'], self.pa_fall_color)):
                    if d is None:
                        continue
                    tw = np.linspace(max(0.0, d['t0_local'] - d['half']),
                                     min(period, d['t0_local'] + d['half']), 400)
                    line, = ax.plot(tw / ts, logistic(tw, d['y0'], d['amp'], d['t0_local'], d['k']),
                                    color=color.currentText(), lw=lw,
                                    label=f"{d['kind'].capitalize()} fit: "
                                          f"{d['time_10_90'] * 1e9:.1f} ns (10–90%)")
                    lines.append(line)
                    if self.pa_mark_10_90.isChecked():
                        for lvl in (d['v10'], d['v90']):
                            ax.axhline(lvl, color=color.currentText(), ls=":", lw=1.5)
                if not (self.pa_style.xmin.text().strip() and self.pa_style.xmax.text().strip()):
                    ax.set_xlim(0, period / ts)

            elif view in (PA_VIEW_RISE, PA_VIEW_FALL):
                if edge is None:
                    ax.text(0.5, 0.5, res['fit_error'] or "Edge fitting is off",
                            transform=ax.transAxes, ha='center', va='center', fontsize=12, color='gray')
                else:
                    color = (self.pa_rise_color if edge is res['rise'] else self.pa_fall_color).currentText()
                    line, = ax.plot(edge['tt_local'] / ts, edge['yy'], '.', color=self.pa_avg_color.currentText(),
                                    ms=self._num(self.pa_marker_size, 4), label="Averaged data")
                    lines.append(line)
                    tw = np.linspace(edge['tt_local'][0], edge['tt_local'][-1], 600)
                    line, = ax.plot(tw / ts, logistic(tw, edge['y0'], edge['amp'], edge['t0_local'], edge['k']),
                                    color=color, lw=lw,
                                    label=f"Logistic fit: {edge['time_10_90'] * 1e9:.2f} ns (10–90%), "
                                          f"R² = {edge['r2']:.4f}")
                    lines.append(line)
                    if self.pa_mark_10_90.isChecked():
                        for lvl, tx, tag in ((edge['v10'], edge['t10_local'], "10%"),
                                             (edge['v90'], edge['t90_local'], "90%")):
                            ax.axhline(lvl, color=color, ls=":", lw=1.5)
                            ax.annotate(tag, (tx / ts, lvl), textcoords="offset points", xytext=(6, 6),
                                        fontsize=self._num(self.pa_style.legend_font_size, 22, int) - 4)

            elif view == PA_VIEW_RECORD:
                line, = ax.plot(res['t'] / ts, res['y'], lw=max(0.5, lw / 3),
                                color=self.pa_avg_color.currentText(), label="Full record")
                lines.append(line)
                for i in range(res['n_periods'] + 1):
                    ax.axvline(i * period / ts, color=self.pa_rise_color.currentText(), ls="--", lw=1.5)

            elif view == PA_VIEW_OVERLAY:
                for i, seg in enumerate(res['segments']):
                    line, = ax.plot(tau / ts, seg, lw=max(0.7, lw / 2), label=f"Period {i + 1}")
                    lines.append(line)
                if not (self.pa_style.xmin.text().strip() and self.pa_style.xmax.text().strip()):
                    ax.set_xlim(0, period / ts)

        if not lines and view != PA_VIEW_RISE and view != PA_VIEW_FALL:
            ax.text(0.5, 0.5, "Add oscilloscope captures (TIME, C1 columns)",
                    transform=ax.transAxes, ha='center', va='center', fontsize=12, color='gray')

        self._pa_update_default_labels(t_name)
        self.pa_style.apply(self.pa_fig, ax, lines)
        self.pa_canvas.draw()

    def export_pa_average(self):
        res = self._pa_selected()
        if res is None:
            return
        default = os.path.splitext(res['path'])[0] + "_avg_period.csv"
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Averaged Period", default, "CSV Files (*.csv)")
        if not file_path:
            return
        try:
            np.savetxt(file_path, np.column_stack([res['tau'], res['avg'], res['std']]),
                       delimiter=",", header="time_s,amplitude_avg,amplitude_std", comments="")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Could not export data: {e}")

    def export_pa_report(self):
        res = self._pa_selected()
        if res is None:
            return
        default = os.path.splitext(res['path'])[0] + "_edge_fit.txt"
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Edge Fit Report", default, "Text Files (*.txt)")
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write(pa_report_text(res))
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Could not export data: {e}")

    def export_pa_summary(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Results Table", "", "CSV Files (*.csv)")
        if not file_path:
            return
        try:
            with open(file_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["File", "Frequency_Hz", "Period_us", "N_periods",
                                 "Rise_10_90_ns", "Fall_10_90_ns", "Rise_20_80_ns", "Fall_20_80_ns",
                                 "R2_rise", "R2_fall", "Level_low", "Level_high"])
                for res in self.pa_results:
                    rise, fall = res['rise'], res['fall']
                    edge_cols = ["", "", "", "", "", "", "", ""]
                    if rise:
                        edge_cols = [rise['time_10_90'] * 1e9, fall['time_10_90'] * 1e9,
                                     rise['time_20_80'] * 1e9, fall['time_20_80'] * 1e9,
                                     rise['r2'], fall['r2'],
                                     0.5 * (rise['level_low'] + fall['level_low']),
                                     0.5 * (rise['level_high'] + fall['level_high'])]
                    writer.writerow([res['name'], res['f0'], res['period'] * 1e6,
                                     res['n_periods']] + edge_cols)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Could not export data: {e}")

    def init_s11_tab(self):
        s11_tab = QWidget()
        self.tabs.addTab(s11_tab, "S11 Plot")
        layout = QHBoxLayout(s11_tab)

        # Left: loaded files
        left = QVBoxLayout()
        btn_add = QPushButton("Add S1P / S2P Files")
        btn_add.clicked.connect(self.add_s11_files)
        btn_remove = QPushButton("Remove Selected")
        btn_remove.clicked.connect(self.remove_s11_files)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.clear_s11)
        left.addWidget(btn_add)
        edit_row = QHBoxLayout()
        edit_row.addWidget(btn_remove)
        edit_row.addWidget(btn_clear)
        left.addLayout(edit_row)
        self.s11_list = QListWidget()
        self.s11_list.setSelectionMode(QListWidget.ExtendedSelection)
        left.addWidget(self.s11_list, 1)

        # Center: plot
        self.s11_fig, self.s11_ax = plt.subplots(figsize=(8, 6), dpi=100)
        self.s11_canvas = FigureCanvas(self.s11_fig)

        # Right: the standard formatting panel
        self.s11_style = StylePanel(self, "Frequency (GHz)", "|S$_{11}$| (dB)")
        grid = build_settings_sections(self.s11_style.rows(), "s11")
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        settings_layout.addLayout(grid)
        settings_layout.addStretch()
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings)

        right = QVBoxLayout()
        right.addWidget(settings_scroll)
        btn_plot = QPushButton("Plot")
        btn_plot.clicked.connect(self.plot_s11)
        btn_save = QPushButton("Save Plot (PNG, JPEG, PDF, SVG, ...)")
        btn_save.clicked.connect(lambda: self.save_figure(
            self.s11_fig, self.s11_canvas, self.s11_style.save_width,
            self.s11_style.save_height, self.s11_style.save_dpi))
        right.addWidget(btn_plot)
        right.addWidget(btn_save)
        right.addLayout(self.settings_buttons())

        layout.addLayout(left, 2)
        layout.addWidget(self.s11_canvas, 5)
        layout.addLayout(right, 3)

        for widget in settings.findChildren(QWidget):
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.plot_s11)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.plot_s11)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.plot_s11)
        self.plot_s11()

    def add_s11_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Touchstone Files", "",
            "Touchstone (*.s1p *.s2p *.S1P *.S2P);;All Files (*)")
        errors = []
        for path in paths:
            try:
                f, s11, _ = read_touchstone_s11(path)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                continue
            name = os.path.basename(path)
            self.s11_data.append(dict(path=path, name=name, f=f, S11=s11))
            self.s11_list.addItem(name)
        if errors:
            QMessageBox.warning(self, "S11 Plot", "Some files were skipped:\n" + "\n".join(errors))
        self.plot_s11()

    def remove_s11_files(self):
        for r in sorted({self.s11_list.row(i) for i in self.s11_list.selectedItems()}, reverse=True):
            self.s11_list.takeItem(r)
            del self.s11_data[r]
        self.plot_s11()

    def clear_s11(self):
        self.s11_data = []
        self.s11_list.clear()
        self.plot_s11()

    def plot_s11(self):
        self.s11_fig.clf()
        ax = self.s11_ax = self.s11_fig.add_subplot(111)
        lw = self._num(self.s11_style.line_width, 3)
        lines = []
        for d in self.s11_data:
            with np.errstate(divide="ignore"):
                db = 20 * np.log10(np.abs(d['S11']))
            line, = ax.plot(d['f'] / 1e9, db, lw=lw, label=os.path.splitext(d['name'])[0])
            lines.append(line)
        if not lines:
            ax.text(0.5, 0.5, "Add Touchstone files (.s1p / .s2p) to plot |S11|",
                    transform=ax.transAxes, ha='center', va='center', fontsize=12, color='gray')
        self.s11_style.apply(self.s11_fig, ax, lines)
        self.s11_canvas.draw()

    def init_rt_tab(self):
        rt_tab = QWidget()
        # First tab: raw scans come before the Transmission tab in the workflow
        self.tabs.insertTab(0, rt_tab, "Raw → Transmission")
        self.tabs.setCurrentIndex(1)
        layout = QHBoxLayout(rt_tab)

        # Left: reference and sample scans, options, output
        left = QVBoxLayout()
        self.rt_ref_list = QListWidget()
        self.rt_samp_list = QListWidget()
        for title, lst, add, clear in [
                ("Reference scans (averaged):", self.rt_ref_list,
                 lambda: self.add_rt_files(self.rt_ref_paths, "Reference"),
                 lambda: self.clear_rt_files(self.rt_ref_paths)),
                ("Sample scans:", self.rt_samp_list,
                 lambda: self.add_rt_files(self.rt_samp_paths, "Sample"),
                 lambda: self.clear_rt_files(self.rt_samp_paths))]:
            left.addWidget(QLabel(title))
            lst.setSelectionMode(QListWidget.ExtendedSelection)
            left.addWidget(lst, 1)
            row = QHBoxLayout()
            btn_add = QPushButton("Add...")
            btn_add.clicked.connect(add)
            btn_remove = QPushButton("Remove Selected")
            btn_remove.clicked.connect(lambda _, l=lst: self.remove_rt_files(l))
            btn_clear = QPushButton("Clear")
            btn_clear.clicked.connect(clear)
            for b in (btn_add, btn_remove, btn_clear):
                row.addWidget(b)
            left.addLayout(row)

        self.rt_mode = QComboBox()
        self.rt_mode.addItems([RT_MODE_AVG, RT_MODE_EACH])
        self.rt_mode.setToolTip("Average all sample scans into one transmission, or make one "
                                "transmission per sample scan (e.g. a voltage series)")
        self.rt_env_window = QLineEdit("30")
        self.rt_env_window.setToolTip("Sliding window (points) of the RMS envelope")
        self.rt_mean_window = QLineEdit("500")
        self.rt_mean_window.setToolTip("Window (points) of the moving mean of the transmission")
        self.rt_save_mean = QCheckBox("Also save moving mean column")
        self.rt_add_to_tx = QCheckBox("Add saved file(s) to Transmission tab")
        self.rt_add_to_tx.setChecked(True)
        options = QtWidgets.QGridLayout()
        for r, (text, widget) in enumerate([
                ("Sample Files", self.rt_mode),
                ("Envelope Window [pts]", self.rt_env_window),
                ("Moving Mean Window [pts]", self.rt_mean_window),
                (None, self.rt_save_mean),
                (None, self.rt_add_to_tx)]):
            if text is None:
                options.addWidget(widget, r, 0, 1, 2)
            else:
                options.addWidget(QLabel(text), r, 0)
                options.addWidget(widget, r, 1)
        left.addLayout(options)
        btn_save_t = QPushButton("Save Transmission (TXT)...")
        btn_save_t.setToolTip("Tab-separated frequency and transmission, readable by the "
                              "Transmission tab")
        btn_save_t.clicked.connect(self.save_rt)
        left.addWidget(btn_save_t)
        self.rt_mode.currentIndexChanged.connect(self.compute_rt)
        for edit in (self.rt_env_window, self.rt_mean_window):
            edit.textChanged.connect(self.compute_rt)

        # Center: plot and which output to show
        self.rt_fig, self.rt_ax = plt.subplots(figsize=(8, 6), dpi=100)
        self.rt_canvas = FigureCanvas(self.rt_fig)
        self.rt_result_combo = QComboBox()
        self.rt_result_combo.currentIndexChanged.connect(self.plot_rt)
        center = QVBoxLayout()
        center.addWidget(self.rt_canvas, 1)
        show_row = QHBoxLayout()
        show_row.addWidget(QLabel("Show:"))
        show_row.addWidget(self.rt_result_combo, 1)
        center.addLayout(show_row)

        # Right: view options + the standard formatting panel
        self.rt_view = QComboBox()
        self.rt_view.addItems([RT_VIEW_T, RT_VIEW_PC, RT_VIEW_ENV])
        self.rt_show_mean = QCheckBox("Show Moving Mean")
        self.rt_show_mean.setChecked(True)
        self.rt_t_color = QComboBox()
        self.rt_t_color.addItems(COLOR_NAMES)
        self.rt_mean_color = QComboBox()
        self.rt_mean_color.addItems(COLOR_NAMES)
        self.rt_mean_color.setCurrentText("red")
        self.rt_ref_color = QComboBox()
        self.rt_ref_color.addItems(COLOR_NAMES)
        self.rt_samp_color = QComboBox()
        self.rt_samp_color.addItems(COLOR_NAMES)
        self.rt_samp_color.setCurrentText("red")
        self.rt_env_color = QComboBox()
        self.rt_env_color.addItems(COLOR_NAMES)
        self.rt_env_color.setCurrentText("black")
        self.rt_marker_size = QLineEdit("3")
        self.rt_style = StylePanel(self)
        self.rt_style.line_width.setText("1")

        rows = [
            ("Plot View", self.rt_view),
            (None, self.rt_show_mean),
            ("Transmission Color", self.rt_t_color),
            ("Moving Mean Color", self.rt_mean_color),
            ("Reference Color", self.rt_ref_color),
            ("Sample Color", self.rt_samp_color),
            ("Envelope Color", self.rt_env_color),
            ("Marker Size", self.rt_marker_size),
        ] + self.rt_style.rows()
        grid = build_settings_sections(rows, "rt")

        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        btn_copy_style = QPushButton("Copy Style from Transmission Tab")
        btn_copy_style.clicked.connect(lambda: self._copy_transmission_style(
            self.rt_style.copy_targets(), self.rt_style.legend_font_size, self.plot_rt))
        settings_layout.addWidget(btn_copy_style)
        settings_layout.addLayout(grid)
        settings_layout.addStretch()
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings)

        right = QVBoxLayout()
        right.addWidget(settings_scroll)
        btn_plot = QPushButton("Plot")
        btn_plot.clicked.connect(self.compute_rt)
        btn_save = QPushButton("Save Plot (PNG, JPEG, PDF, SVG, ...)")
        btn_save.clicked.connect(lambda: self.save_figure(
            self.rt_fig, self.rt_canvas, self.rt_style.save_width,
            self.rt_style.save_height, self.rt_style.save_dpi))
        right.addWidget(btn_plot)
        right.addWidget(btn_save)
        right.addLayout(self.settings_buttons())

        layout.addLayout(left, 3)
        layout.addLayout(center, 5)
        layout.addLayout(right, 3)

        for widget in settings.findChildren(QWidget):
            if widget.parent() is not settings:
                continue
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.plot_rt)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.plot_rt)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.plot_rt)
        self.plot_rt()

    def add_rt_files(self, paths, kind):
        files, _ = QFileDialog.getOpenFileNames(
            self, f"Open {kind} Scans", "", "Data Files (*.txt *.dat *.csv);;All Files (*)")
        if files:
            paths.extend(f for f in files if f not in paths)
            self.compute_rt()

    def remove_rt_files(self, lst):
        paths = self.rt_ref_paths if lst is self.rt_ref_list else self.rt_samp_paths
        for r in sorted({lst.row(i) for i in lst.selectedItems()}, reverse=True):
            del paths[r]
        self.compute_rt()

    def clear_rt_files(self, paths):
        paths.clear()
        self.compute_rt()

    @staticmethod
    def _rt_name(path):
        """Output name from a scan file: '0v_0001.txt' -> '0v'."""
        return re.sub(r'_\d+$', '', os.path.splitext(os.path.basename(path))[0])

    def compute_rt(self):
        """Read the scans and recompute every output; errors go to the plot area."""
        for lst, paths in ((self.rt_ref_list, self.rt_ref_paths),
                           (self.rt_samp_list, self.rt_samp_paths)):
            lst.clear()
            for p in paths:
                item = QListWidgetItem(os.path.basename(p))
                item.setToolTip(p)
                lst.addItem(item)
        self.rt_results = []
        self.rt_error = None
        if self.rt_ref_paths and self.rt_samp_paths:
            env_n = max(2, self._num(self.rt_env_window, 30, int))
            mean_n = max(1, self._num(self.rt_mean_window, 500, int))
            try:
                ref = average_scans(self.rt_ref_paths)
                if self.rt_mode.currentText() == RT_MODE_AVG:
                    groups = [(self._rt_name(self.rt_samp_paths[0]), self.rt_samp_paths)]
                else:
                    groups = [(self._rt_name(p), [p]) for p in self.rt_samp_paths]
                    names = [g[0] for g in groups]
                    if len(set(names)) < len(names):  # e.g. 0v_0001 and 0v_0002
                        groups = [(os.path.splitext(os.path.basename(p))[0], [p])
                                  for p in self.rt_samp_paths]
                for name, paths in groups:
                    self.rt_results.append(
                        (name, raw_transmission(ref, average_scans(paths), env_n, mean_n)))
            except Exception as e:
                self.rt_results = []
                self.rt_error = str(e)

        current = self.rt_result_combo.currentText()
        self.rt_result_combo.blockSignals(True)
        self.rt_result_combo.clear()
        self.rt_result_combo.addItems([name for name, _ in self.rt_results])
        idx = self.rt_result_combo.findText(current)
        self.rt_result_combo.setCurrentIndex(max(idx, 0))
        self.rt_result_combo.blockSignals(False)
        self.plot_rt()

    def _rt_update_default_labels(self):
        """Follow view changes in the axis labels, unless the user typed their own."""
        y = "Transmission" if self.rt_view.currentText() == RT_VIEW_T else "THz Photocurrent (nA)"
        new = ("Frequency (GHz)", y)
        for edit, old, text in zip((self.rt_style.xlabel, self.rt_style.ylabel),
                                   self._rt_last_defaults, new):
            if edit.text() in ("", old):
                edit.blockSignals(True)
                edit.setText(text)
                edit.blockSignals(False)
        self._rt_last_defaults = new

    def plot_rt(self):
        self.rt_fig.clf()
        self.rt_ax = self.rt_fig.add_subplot(111)
        ax = self.rt_ax
        view = self.rt_view.currentText()
        lw = self._num(self.rt_style.line_width, 1)
        ms = self._num(self.rt_marker_size, 3)
        lines = []
        i = self.rt_result_combo.currentIndex()
        res = self.rt_results[i][1] if 0 <= i < len(self.rt_results) else None

        if res is None:
            if getattr(self, "rt_error", None):
                msg = f"Could not compute transmission:\n{self.rt_error}"
            else:
                msg = "Add reference and sample photocurrent scans\n(frequency, current columns)"
            ax.text(0.5, 0.5, msg, transform=ax.transAxes, ha='center', va='center',
                    fontsize=12, color='gray')
        elif view == RT_VIEW_T:
            line, = ax.plot(res['x'], res['T'], '.-', markersize=ms, linewidth=lw,
                            color=self.rt_t_color.currentText(), label="Calculated")
            lines.append(line)
            if self.rt_show_mean.isChecked():
                line, = ax.plot(res['x'], res['T_mean'], linewidth=max(lw, 2),
                                color=self.rt_mean_color.currentText(), label="Moving Mean")
                lines.append(line)
        elif view == RT_VIEW_PC:
            env = self.rt_env_color.currentText()
            for x, y, hi, color, kind in (
                    (res['xr'], res['yr'], res['ref_hi'], self.rt_ref_color, "Reference"),
                    (res['xs'], res['ys'], res['samp_hi'], self.rt_samp_color, "Sample")):
                line, = ax.plot(x, y, linewidth=lw, color=color.currentText(), label=f"{kind} PC")
                lines.append(line)
                line, = ax.plot(x, hi, linewidth=lw, color=env, label=f"{kind} Envelope")
                lines.append(line)
        else:
            for x, hi, lo, color, kind in (
                    (res['xr'], res['ref_hi'], res['ref_lo'], self.rt_ref_color, "Reference"),
                    (res['xs'], res['samp_hi'], res['samp_lo'], self.rt_samp_color, "Sample")):
                c = color.currentText()
                line, = ax.plot(x, hi, '.', markersize=ms, color=c, label=f"{kind} upper")
                lines.append(line)
                line, = ax.plot(x, lo, '.', markersize=ms, color=c, alpha=0.5, label=f"{kind} lower")
                lines.append(line)

        self._rt_update_default_labels()
        self.rt_style.apply(self.rt_fig, ax, lines)
        self.rt_canvas.draw()

    def _rt_write(self, path, res):
        cols = [res['x'], res['T']]
        header = "Frequency[GHz]\tTransmission"
        if self.rt_save_mean.isChecked():
            cols.append(res['T_mean'])
            header += "\tTransmission_MovMean"
        # Header without spaces so the Transmission tab reads one name per column
        np.savetxt(path, np.column_stack(cols), delimiter="\t", fmt="%.8g",
                   header=header, comments="")

    def save_rt(self):
        if not self.rt_results:
            QMessageBox.information(self, "Save Transmission",
                                    "Add reference and sample scans first.")
            return
        folder = os.path.dirname(self.rt_samp_paths[0])
        if len(self.rt_results) == 1:
            name, res = self.rt_results[0]
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Transmission", os.path.join(folder, name + ".txt"),
                "Text Files (*.txt);;All Files (*)")
            if not path:
                return
            if not os.path.splitext(path)[1]:
                path += ".txt"
            targets = [(path, res)]
        else:
            folder = QFileDialog.getExistingDirectory(self, "Folder for Transmission Files", folder)
            if not folder:
                return
            targets = [(os.path.join(folder, name + ".txt"), res) for name, res in self.rt_results]
            existing = [os.path.basename(p) for p, _ in targets if os.path.exists(p)]
            if existing and QMessageBox.question(
                    self, "Save Transmission",
                    "Replace these files?\n" + "\n".join(existing)) != QMessageBox.Yes:
                return

        saved = []
        try:
            for path, res in targets:
                self._rt_write(path, res)
                saved.append(path)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save {path}: {e}")
        if saved and self.rt_add_to_tx.isChecked():
            errors = []
            for path in saved:
                try:
                    self.load_file(path)
                except Exception as e:
                    errors.append(f"{os.path.basename(path)}: {e}")
            self.plot_data()
            if errors:
                QMessageBox.warning(self, "Transmission Tab", "\n".join(errors))

    def on_hover(self, event):
        vis = self.annot.get_visible()
        if event.inaxes == self.ax:
            for line, x, y, legend, marker, marker_size, color in self.lines:
                cont, ind = line.contains(event)
                if cont:
                    idx = ind["ind"][0]
                    self.annot.xy = (x[idx], y[idx])
                    text = f"{legend}\nX: {x[idx]:.3g}\nY: {y[idx]:.3g}"
                    self.annot.set_text(text)
                    self.annot.get_bbox_patch().set_facecolor(color)
                    self.annot.get_bbox_patch().set_alpha(0.8)
                    self.annot.set_visible(True)
                    self.canvas.draw_idle()
                    return
        if vis:
            self.annot.set_visible(False)
            self.canvas.draw_idle()

    def save_plot(self):
        self.save_figure(self.fig, self.canvas, self.save_width,
                         self.save_height, self.save_dpi)

    def save_figure(self, fig, canvas, width_edit, height_edit, dpi_edit):
        """Save fig at the requested size. The format comes from the typed extension,
        or from the chosen file type when no extension is typed."""
        filters = ";;".join(f for f, _ in SAVE_FORMATS)
        last = getattr(self, "_last_save_filter", SAVE_FORMATS[0][0])
        file_path, chosen = QFileDialog.getSaveFileName(self, "Save Plot", "", filters, last)
        if not file_path:
            return
        self._last_save_filter = chosen
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in SAVE_EXTENSIONS:
            ext = dict(SAVE_FORMATS).get(chosen, ".png")
            file_path += ext

        old_size = fig.get_size_inches().copy()
        try:
            width = float(width_edit.text())
            height = float(height_edit.text())
            dpi = int(dpi_edit.text())
            # forward=False: resize only the saved figure, not the on-screen canvas
            fig.set_size_inches(width, height, forward=False)
            fig.tight_layout()
            kwargs = {}
            if ext in (".jpg", ".jpeg"):
                kwargs["pil_kwargs"] = {"quality": 95}
            fig.savefig(file_path, dpi=dpi, **kwargs)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save image: {e}")
        finally:
            fig.set_size_inches(old_size, forward=False)
            fig.tight_layout()
            canvas.draw()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = THzPlotComparisonApp()
    window.show()
    sys.exit(app.exec())
