"""hrv_core/breathing.py на синтетике: известная частота на одной оси + шум
на остальных + всплеск движения большой амплитуды в середине.

Метод (интерполяция 10 Гц → полосовой Баттерворт 0.10-0.45 Гц → фаза
Гильберта) сверен на живых прогонах с нажатиями человека на каждый вдох
(0.3-1.3% расхождения по числу циклов, см. модульный docstring
hrv_core/breathing.py) — здесь проверяется код, а не сам метод."""

import unittest

import numpy as np

from hrv_core.breathing import analyze_breathing, MOTION_REJECT_FACTOR


def _accel_samples(
    duration_sec: float,
    fs: float,
    breath_hz: float,
    *,
    carrier_axis: int = 2,  # 0=X, 1=Y, 2=Z
    breath_amp_mg: float = 15.0,
    other_noise_mg: float = 2.0,
    burst_at_sec: tuple[float, float] | None = None,
    burst_amp_mg: float = 160.0,
    t0: float = 1_700_000_000.0,
    seed: int = 0,
) -> list[tuple[float, int, int, int]]:
    """(ts, x, y, z) — как отдаёт hrv_core.db.load_accel_samples: ts — секунды
    эпохи, x/y/z — мг. fs специально не круглая (как реальный поток PMD)."""
    rng = np.random.default_rng(seed)
    n = int(duration_sec * fs)
    t = np.arange(n) / fs
    axes = [
        1000.0 + rng.normal(0, other_noise_mg, size=n),
        500.0 + rng.normal(0, other_noise_mg, size=n),
        200.0 + rng.normal(0, other_noise_mg, size=n),
    ]
    axes[carrier_axis] = axes[carrier_axis] + breath_amp_mg * np.sin(2 * np.pi * breath_hz * t)
    if burst_at_sec is not None:
        # Смоделировать реальное движение (не белый шум): почти вся его
        # энергия у резкого рывка лежит в единицы Гц и ниже, то есть частично
        # ВНУТРИ полосы дыхания 0.10-0.45 Гц — белый шум там же лежит плоско
        # и почти полностью вырезается фильтром, не тестируя браковку вообще.
        lo, hi = burst_at_sec
        mask = (t >= lo) & (t < hi)
        burst = burst_amp_mg * np.sin(2 * np.pi * 0.25 * (t[mask] - lo))
        for k in range(3):
            axes[k] = axes[k].copy()
            axes[k][mask] += burst
    ts = t0 + t
    rows = list(zip(
        ts.tolist(),
        axes[0].round().astype(int).tolist(),
        axes[1].round().astype(int).tolist(),
        axes[2].round().astype(int).tolist(),
    ))
    return rows


class BreathingAnalysisTests(unittest.TestCase):
    def test_carrier_axis_chosen_by_data_not_assumed(self):
        """Дыхание на Y (не X, не Z по умолчанию) — ось выбирается по p75
        модуля отфильтрованного сигнала, а не назначается заранее."""
        samples = _accel_samples(180.0, 25.5, 18.0 / 60.0, carrier_axis=1)
        res = analyze_breathing(samples)
        self.assertIsNotNone(res)
        self.assertEqual(res["axis"], "Y")
        self.assertEqual(res["summary"]["axis"], "Y")

    def test_cycle_count_by_phase_matches_known_frequency(self):
        """Число циклов = Δфазы / 2π должно совпасть с числом периодов синуса
        за всю запись (с запасом на краевые эффекты фильтра/Гильберта)."""
        duration = 180.0
        breath_cpm = 16.6
        breath_hz = breath_cpm / 60.0
        samples = _accel_samples(duration, 25.5, breath_hz, carrier_axis=2)

        res = analyze_breathing(samples)
        self.assertIsNotNone(res)

        expected_cycles = duration * breath_hz
        # Допуск ýже, чем полный период — считает именно число циклов, а не
        # среднюю частоту "в целом".
        self.assertLess(abs(res["total_cycles"] - expected_cycles), 1.0)

        # Частота для графиков — медиана по годным окнам, тоже по фазе.
        self.assertIsNotNone(res["summary"]["cpm_median"])
        self.assertAlmostEqual(res["summary"]["cpm_median"], breath_cpm, delta=1.0)

    def test_instantaneous_rate_series_tracks_known_frequency(self):
        duration = 120.0
        breath_cpm = 14.0
        samples = _accel_samples(duration, 25.5, breath_cpm / 60.0, carrier_axis=0)

        res = analyze_breathing(samples)
        self.assertIsNotNone(res)

        # Отрезаем края (сглаживание/фильтр краевые эффекты) — середина ряда
        # должна плотно облегать истинную частоту.
        rate = res["rate_cpm"]
        mid = rate[len(rate) // 4 : -len(rate) // 4]
        self.assertAlmostEqual(float(np.median(mid)), breath_cpm, delta=1.0)

    def test_motion_burst_window_is_rejected(self):
        """Всплеск движения (амплитуда на порядок больше дыхания) в середине
        прогона бракует накрывающие его окна, не портя оценку по спокойным."""
        duration = 180.0
        breath_cpm = 18.0
        samples = _accel_samples(
            duration, 25.5, breath_cpm / 60.0, carrier_axis=2,
            # 20с внутри окна [60,120) — больше четверти окна (порог, при
            # котором p75 вообще "видит" всплеск), но два соседних окна
            # ([30,90) и [90,150)) он задевает лишь на треть от четверти —
            # они должны остаться годными.
            burst_at_sec=(80.0, 100.0),
        )

        res = analyze_breathing(samples)
        self.assertIsNotNone(res)

        windows = res["windows"]
        self.assertTrue(any(w["rejected"] for w in windows))
        self.assertTrue(any(not w["rejected"] for w in windows))
        rejected_amp = max(w["amp_mg"] for w in windows if w["rejected"])
        quiet_amps = [w["amp_mg"] for w in windows if not w["rejected"]]
        self.assertGreater(rejected_amp, MOTION_REJECT_FACTOR * float(np.median(quiet_amps)))

        # Итоговая медиана — по спокойным окнам, не искажена всплеском.
        self.assertAlmostEqual(res["summary"]["cpm_median"], breath_cpm, delta=1.5)
        self.assertLess(res["summary"]["good_fraction"], 1.0)

    def test_posture_change_picks_axis_per_posture_and_keeps_quiet_windows(self):
        """Поворот посреди записи: вектор силы тяжести уходит с Z на Y, дыхание
        — с Z на Y, размах падает вчетверо. Ось выбирается в каждой позе,
        крупная волна без движения не бракуется (раньше брак шёл по размеру
        волны, и поза на спине выглядела как движение)."""
        fs = 25.5
        rng = np.random.default_rng(1)
        dur = 360.0
        n = int(dur * fs)
        t = np.arange(n) / fs
        breath = np.sin(2 * np.pi * (18.0 / 60.0) * t)
        first = t < dur / 2
        x = 50.0 + rng.normal(0, 1.0, n)
        y = np.where(first, 30.0, 980.0) + rng.normal(0, 1.0, n)
        z = np.where(first, 980.0, 30.0) + rng.normal(0, 1.0, n)
        z = z + np.where(first, 24.0 * breath, 0.0)   # на спине — крупная волна на Z
        y = y + np.where(first, 0.0, 6.0 * breath)    # на боку — мелкая на Y
        samples = list(zip((1_700_000_000.0 + t).tolist(),
                           x.round().astype(int).tolist(),
                           y.round().astype(int).tolist(),
                           z.round().astype(int).tolist()))
        res = analyze_breathing(samples)
        self.assertIsNotNone(res)
        real = [p for p in res["postures"] if not p["transition"]]
        self.assertEqual([p["axis"] for p in real], ["Z", "Y"])
        # Брак — только у окон, накрывающих поворот, не у крупной волны на спине
        rejected = [w for w in res["windows"] if w["rejected"]]
        turn = 1_700_000_000.0 + dur / 2
        self.assertTrue(all(w["t_start"] < turn < w["t_end"] for w in rejected))
        self.assertAlmostEqual(res["summary"]["cpm_median"], 18.0, delta=1.0)
        # Частота у стыка поз замаскирована, а не показывает рывок
        i = int(np.searchsorted(res["t"], turn))
        self.assertTrue(np.isnan(res["rate_cpm"][i]))

    def test_too_little_data_returns_none(self):
        samples = _accel_samples(1.0, 25.5, 18.0 / 60.0)
        self.assertIsNone(analyze_breathing(samples))


if __name__ == "__main__":
    unittest.main()
