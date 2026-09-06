"""Оценка дыхания по окнам: движение в одном окне не должно портить остальные.

breathing_from_acc считала пик спектра по всему прогону разом — движение
(сесть, поправиться) на порядок превышает дыхательную амплитуду и забирает
пик себе (живые прогоны 2026-09-06: 28.0 и 3.0 цикл/мин вместо 17-19 и
10-12 по спокойным участкам). Синтетика здесь: чистый синус дыхания + короткий
всплеск большой амплитуды в одном окне.
"""

import unittest

import numpy as np

from hrv_core.pmd import breathing_from_acc


def _breath_samples(
    duration_sec: float,
    fs: float,
    breath_hz: float,
    *,
    breath_amp_mg: float = 15.0,
    burst_at_sec: tuple[float, float] | None = None,
    burst_amp_mg: float = 160.0,
) -> list[tuple[int, int, int]]:
    n = int(duration_sec * fs)
    t = np.arange(n) / fs
    x = 1000.0 + breath_amp_mg * np.sin(2 * np.pi * breath_hz * t)
    if burst_at_sec is not None:
        lo, hi = burst_at_sec
        mask = (t >= lo) & (t < hi)
        rng = np.random.default_rng(0)
        x = x.copy()
        x[mask] += rng.normal(0, burst_amp_mg, size=mask.sum())
    y = np.full(n, 500.0)
    z = np.full(n, 200.0)
    return list(zip(x.round().astype(int), y.round().astype(int), z.round().astype(int)))


class BreathingWindowsTests(unittest.TestCase):
    def test_quiet_signal_estimates_true_breath_rate(self):
        """Без движения все окна спокойные, итог — частота синуса."""
        fs = 25.0
        breath_cpm = 18.0
        samples = _breath_samples(180.0, fs, breath_cpm / 60.0)

        br = breathing_from_acc(samples, fs)

        self.assertIsNotNone(br)
        self.assertEqual(br["n_quiet"], br["n_windows"])
        self.assertTrue(all(not w["rejected"] for w in br["windows"]))
        self.assertAlmostEqual(br["cpm_median"], breath_cpm, delta=br["bin_cpm"])

    def test_motion_burst_window_is_rejected_and_excluded_from_estimate(self):
        """Всплеск движения в середине прогона бракует окна, которые его содержат,
        но не портит итоговую оценку по спокойным окнам."""
        fs = 25.0
        breath_cpm = 18.0
        # Всплеск сосредоточен вокруг t=90s — окно [60-120) его полностью
        # содержит, окна [0-60) и [120-180) его не видят вовсе.
        samples = _breath_samples(
            180.0, fs, breath_cpm / 60.0,
            burst_at_sec=(85.0, 95.0),
        )

        br = breathing_from_acc(samples, fs, window_sec=60.0, step_sec=60.0)

        self.assertIsNotNone(br)
        self.assertEqual(br["n_windows"], 3)
        rejected_ranges = [
            (w["t_start_sec"], w["t_end_sec"]) for w in br["windows"] if w["rejected"]
        ]
        self.assertEqual(rejected_ranges, [(60.0, 120.0)])
        self.assertEqual(br["n_quiet"], 2)
        # Итоговая оценка — по спокойным окнам, значит совпадает с частотой синуса,
        # а не искажена всплеском.
        self.assertAlmostEqual(br["cpm_median"], breath_cpm, delta=br["bin_cpm"])

    def test_too_little_data_returns_none_not_a_number(self):
        """Меньше 20с данных — как и раньше, None, а не сомнительное число."""
        fs = 25.0
        samples = _breath_samples(5.0, fs, 18.0 / 60.0)
        self.assertIsNone(breathing_from_acc(samples, fs))


if __name__ == "__main__":
    unittest.main()
