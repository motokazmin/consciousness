"""Сверка дыхания: дамп акселерометра (`pmd_check --dump`) против отметок вдохов.

Отвечает на вопрос ADR-003: измеряет ли акселерометр H10 дыхание или движение
вообще. Сверяется не итоговое число циклов, а **каждый цикл со своим**: между
двумя соседними нажатиями прибор обязан насчитать ровно 1.00 цикла. Итоговый
счёт этого не показывает — он одинаково выглядит и когда каналы идут в ногу, и
когда прибор добирает лишние пики в одном месте и теряет в другом.

    python -m research.tools.breath_compare ЗАПИСЬ.csv ОТМЕТКИ.marks

Привязка по стенным часам: `first_frame_wall_time` из шапки дампа и `time.time()`
в файле отметок (`research/tools/breath_marks.py`) — одни и те же часы.

Цикл прибора считается по фазе аналитического сигнала (Гильберт), а не по
пикам: подъём и спад грудной клетки несимметричны, детектор пиков дробит вдох
на два. На прогоне 2026-09-06 разница между способами — 5 циклов из 38.
"""

from __future__ import annotations

import csv
import re
import sys

import numpy as np
from scipy.signal import butter, detrend, filtfilt, hilbert

FS_GRID = 10.0                 # равномерная сетка для фильтрации, Гц
BANDS = ((0.08, 0.70), (0.10, 0.45), (0.12, 0.40))   # 4.8-42, 6-27, 7-24 цикл/мин
MAIN_BAND = (0.10, 0.45)


def load_dump(path: str):
    with open(path) as f:
        header = f.readline()
        rows = [
            (int(r["frame_idx"]), int(r["sample_idx"]), int(r["device_ts_ns"] or 0),
             int(r["x"]), int(r["y"]), int(r["z"]))
            for r in csv.DictReader(f)
        ]
    wall = float(re.search(r"first_frame_wall_time=([\d.]+)", header).group(1))
    fs = float(re.search(r"measured_hz=([\d.]+)", header).group(1))
    a = np.array(rows, dtype=float)
    fi, si, dts, x, y, z = a.T
    frames = np.unique(fi)
    ts = {int(f): dts[fi == f][0] for f in frames}
    # Метка кадра относится к его последнему отсчёту; t=0 — этот момент у
    # первого кадра, он же `first_frame_wall_time`.
    n_per = int(round(len(a) / len(frames)))
    t = np.array([(ts[int(p)] - ts[int(frames[0])]) / 1e9 + (q - (n_per - 1)) / fs
                  for p, q in zip(fi, si)])
    return t, {"X": x, "Y": y, "Z": z}, fs, wall, header.strip()


def band_signal(t, sig, grid, band):
    b, a = butter(2, [band[0] / (FS_GRID / 2), band[1] / (FS_GRID / 2)], btype="band")
    return filtfilt(b, a, detrend(np.interp(grid, t, sig)))


def cycles(phase_grid, grid, lo, hi) -> float:
    return float(np.diff(np.interp([lo, hi], grid, phase_grid))[0] / (2 * np.pi))


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    t, axes, fs, wall, header = load_dump(sys.argv[1])
    marks = np.array([float(l) for l in open(sys.argv[2]) if not l.startswith("#")]) - wall
    print(header)
    grid = np.arange(t[0], t[-1], 1 / FS_GRID)
    win = marks[-1] - marks[0]
    print(f"\nотметок {len(marks)} ({len(marks) - 1} интервалов) за {win:.1f} c "
          f"→ {60 * (len(marks) - 1) / win:.1f} вдох/мин")

    print("\nциклов прибора в окне отметок (по фазе):")
    print(f"{'ось':>4} " + " ".join(f"{b[0] * 60:.0f}-{b[1] * 60:.0f}".rjust(8) for b in BANDS)
          + f" {'ампл p75, мг':>13}")
    best, best_amp = None, -1.0
    for name, sig in axes.items():
        vals = []
        for band in BANDS:
            v = band_signal(t, sig, grid, band)
            vals.append(cycles(np.unwrap(np.angle(hilbert(v))), grid, marks[0], marks[-1]))
        v = band_signal(t, sig, grid, MAIN_BAND)
        m = (grid >= marks[0]) & (grid <= marks[-1])
        amp = float(np.percentile(np.abs(v[m]), 75))
        print(f"{name:>4} " + " ".join(f"{x:8.1f}" for x in vals) + f" {amp:13.1f}")
        if amp > best_amp:
            best, best_amp = name, amp
    print(f"сигнал несёт ось {best} (наибольшая амплитуда в дыхательной полосе)")

    v = band_signal(t, axes[best], grid, MAIN_BAND)
    ph = np.unwrap(np.angle(hilbert(v)))
    at_marks = np.interp(marks, grid, ph)
    r = float(np.abs(np.mean(np.exp(1j * (at_marks % (2 * np.pi))))))
    print(f"\nкучность фазы нажатий R={r:.2f} "
          f"(1.0 — все нажатия в одной точке волны, 0 — вразнобой)")

    per = np.diff(at_marks) / (2 * np.pi)
    print("циклов прибора между соседними нажатиями (норма 1.00):")
    for i in range(0, len(per), 8):
        print("   " + " ".join(f"{x:5.2f}" for x in per[i:i + 8]))
    bad = np.where((per < 0.7) | (per > 1.3))[0]
    print(f"сумма {per.sum():.2f} против {len(per)} нажатий "
          f"(расхождение {per.sum() - len(per):+.2f} цикла); "
          f"вне [0.7, 1.3]: {len(bad)} — интервалы {[int(i) + 1 for i in bad]}")

    print(f"\nход частоты по 30-с окнам ({'по нажатиям':>12} {'по прибору':>11}):")
    for s in np.arange(marks[0], marks[-1] - 29, 30):
        n = int(np.sum((marks >= s) & (marks < s + 30)))
        print(f"  {s:6.0f}–{s + 30:6.0f} c {n * 2:12.1f} "
              f"{cycles(ph, grid, s, s + 30) * 2:11.1f}")


if __name__ == "__main__":
    main()
