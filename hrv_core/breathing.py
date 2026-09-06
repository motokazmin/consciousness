"""Дыхание из акселерометра PMD — фаза Гильберта, не спектр.

Метод не выбирался наугад: сверен вручную с нажатиями человека на каждый
вдох на живых прогонах (см. `research/`, сюда не заглядываем — только
результат), расхождение по числу циклов 0.3–1.3%. Конвейер:

1. Отсчёты (x, y, z, мг) интерполируются на равномерную сетку `RESAMPLE_HZ`
   (реальная частота потока ~25.5 Гц, не круглая — важна интерполяция, а не
   децимация).
2. Полосовой Баттерворт 2-го порядка `BAND_LOW_HZ`–`BAND_HIGH_HZ`
   (6–27 цикл/мин), нулевой фазы (`filtfilt`), после `detrend`.
3. Несущая ось — та из трёх, у которой p75 модуля отфильтрованного сигнала
   наибольший (не назначается заранее: выбирает то, как ремень сидит на
   груди). На живых прогонах это Z (22.5 мг после нагрузки, 1.7 мг в покое)
   против 0.4–4.3 мг у остальных осей.
4. Фаза несущей — `np.unwrap(np.angle(hilbert(v)))`. Число циклов между
   двумя моментами = разность фаз / 2π. Пики намеренно не ищутся: подъём и
   спад грудной клетки несимметричны, детектор пиков дробит вдох надвое и
   завышает счёт на ~13%.
5. Мгновенная частота — производная фазы, в цикл/мин, сглаженная скользящим
   средним по окну `RATE_SMOOTH_SEC`.
6. Границы циклов — моменты, где фаза проходит через 0 по модулю 2π (для
   будущего счёта вдохов, не используется графиками).
7. Качество — по окнам `QUALITY_WINDOW_SEC`/`QUALITY_STEP_SEC`: амплитуда
   (p75 модуля несущей в окне) и брак по движению, если амплитуда больше чем
   `MOTION_REJECT_FACTOR`× медианы амплитуд по прогону.

**Важно:** частота для графиков — только по фазе (см. `instantaneous_rate_cpm`).
Оценка по argmax спектра (Уэлч) остаётся исключительно диагностикой в
`hrv_core.pmd_check` — на слабом сигнале в покое argmax прыгает на вторую
гармонику: живой прогон 2026-09-06 дал 30 и 35 цикл/мин отдельными окнами при
настоящих 16.6.

Общая нарезка на скользящие окна и правило браковки по движению раньше жили
только в диагностике `hrv_core.pmd` (`breathing_from_acc`) — вынесены сюда
(`iter_windows`, `motion_reject_windows`, `MOTION_REJECT_FACTOR`), `pmd.py`
теперь их импортирует, а не дублирует.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, detrend, filtfilt, hilbert

RESAMPLE_HZ = 10.0
BAND_LOW_HZ = 0.10
BAND_HIGH_HZ = 0.45
BUTTER_ORDER = 2
RATE_SMOOTH_SEC = 15.0

QUALITY_WINDOW_SEC = 60.0
QUALITY_STEP_SEC = 30.0

# Во сколько раз амплитуда окна должна превысить медианную амплитуду по всем
# окнам прогона, чтобы окно посчиталось «не дыханием, а движением». На живом
# прогоне 2026-09-06 спокойные окна дали ~20 мг, окно посадки — ~158 мг
# (разница ×7.9); порог ×3 берёт запас втрое меньше этого разрыва — ловит
# явное движение, не задевая обычный разброс амплитуды дыхания между окнами.
MOTION_REJECT_FACTOR = 3.0

# Минимум длительности записи для оценки (сек). Меньше — не набирается даже
# запас `filtfilt`/`hilbert` на краях после интерполяции на RESAMPLE_HZ (для
# полосового Баттерворта 2-го порядка панель требует больше отсчётов, чем
# кажется на глаз); совпадает с прежним порогом диагностики в pmd.py.
MIN_DURATION_SEC = 20.0


def iter_windows(n: int, fs: float, window_sec: float, step_sec: float):
    """Границы скользящих окон по индексам — общая нарезка для диагностики
    PMD (`hrv_core.pmd.breathing_from_acc`) и для качества дыхания здесь:
    порог браковки не должен разъезжаться между ними по разным циклам.

    По построению `start + win_n` никогда не превышает `n` — каждое окно
    ровно `win_n` отсчётов (либо весь ряд целиком, если он короче окна),
    хвостов короче окна не бывает."""
    win_n = min(n, int(round(fs * window_sec)))
    if win_n <= 0:
        return
    step_n = max(1, int(round(fs * step_sec)))
    for start in range(0, max(1, n - win_n + 1), step_n):
        yield start, start + win_n


def motion_reject_windows(amplitudes: list[float], factor: float = MOTION_REJECT_FACTOR) -> list[bool]:
    """True для окна, чья амплитуда больше чем `factor`× медианы амплитуд по
    прогону — считаем это движением (сесть, поправить ремень), не дыханием."""
    if not amplitudes:
        return []
    median_amp = float(np.median(amplitudes))
    return [median_amp > 0 and a > factor * median_amp for a in amplitudes]


def resample_uniform(
    ts: np.ndarray, xyz: np.ndarray, fs: float = RESAMPLE_HZ
) -> tuple[np.ndarray, np.ndarray] | None:
    """(ts секунды эпохи, (N,3) мг) → равномерная сетка `fs` Гц линейной
    интерполяцией. None, если отсчётов или длительности не хватает даже на
    два узла сетки."""
    if len(ts) < 2:
        return None
    t0, t1 = float(ts[0]), float(ts[-1])
    n = int((t1 - t0) * fs)
    if n < 2:
        return None
    grid = t0 + np.arange(n) / fs
    out = np.stack([np.interp(grid, ts, xyz[:, k]) for k in range(3)], axis=1)
    return grid, out


def bandpass(
    sig: np.ndarray,
    fs: float,
    low: float = BAND_LOW_HZ,
    high: float = BAND_HIGH_HZ,
    order: int = BUTTER_ORDER,
) -> np.ndarray:
    """Баттерворт `order`-го порядка, нулевая фаза (`filtfilt`), после
    `detrend` — как в спецификации, не средний ход, а конкретный рецепт."""
    sig = detrend(np.asarray(sig, dtype=float))
    b, a = butter(order, [low, high], btype="band", fs=fs)
    return filtfilt(b, a, sig)


def select_carrier_axis(filtered_xyz: np.ndarray) -> tuple[int, np.ndarray]:
    """Несущая ось — та, у которой p75 модуля отфильтрованного сигнала
    наибольший. Возвращает (индекс 0/1/2, p75 по каждой оси)."""
    p75 = np.percentile(np.abs(filtered_xyz), 75, axis=0)
    return int(np.argmax(p75)), p75


def hilbert_phase(v: np.ndarray) -> np.ndarray:
    return np.unwrap(np.angle(hilbert(v)))


def cycles_between(phase: np.ndarray, i0: int, i1: int) -> float:
    """Число циклов дыхания между двумя индексами — разность фаз / 2π, не
    подсчёт пиков (см. модульный docstring)."""
    return float((phase[i1] - phase[i0]) / (2 * np.pi))


def instantaneous_rate_cpm(
    phase: np.ndarray, fs: float, smooth_sec: float = RATE_SMOOTH_SEC
) -> np.ndarray:
    """Мгновенная частота (цикл/мин) — производная фазы, сглаженная
    скользящим средним по окну `smooth_sec`. Только эта оценка идёт на
    графики — argmax спектра (диагностика в `pmd_check`) на слабом сигнале
    прыгает на вторую гармонику, см. модульный docstring."""
    d_phase = np.gradient(phase) * fs  # рад/с
    rate_cpm = d_phase / (2 * np.pi) * 60.0
    win = max(1, int(round(smooth_sec * fs)))
    if win > 1 and win < len(rate_cpm):
        kernel = np.ones(win)
        # Делить на длину окна нельзя: у краёв в окно попадает меньше отсчётов,
        # и «среднее» занижается тем сильнее, чем ближе к краю. На живой записи
        # 2026-09-06 (сессия 222) это давало 1.7-4.4 цикл/мин на первых
        # секундах при настоящих 10-12 — на графике выглядело как медленное
        # дыхание в начале сессии, то есть как находка, а не как артефакт.
        # Нормируем на фактическое число слагаемых.
        counts = np.convolve(np.ones_like(rate_cpm), kernel, mode="same")
        rate_cpm = np.convolve(rate_cpm, kernel, mode="same") / counts
    return rate_cpm


def cycle_boundaries(phase: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Моменты, где фаза проходит через 0 по модулю 2π — границы циклов
    дыхания (для будущего счёта вдохов, графики их не используют)."""
    wrapped = np.mod(phase, 2 * np.pi)
    crossings = np.where(np.diff(wrapped) < -np.pi)[0]  # спад с ~2π на ~0
    return t[crossings]


def quality_windows(
    t_grid: np.ndarray,
    wave: np.ndarray,
    rate_cpm: np.ndarray,
    fs: float,
    *,
    window_sec: float = QUALITY_WINDOW_SEC,
    step_sec: float = QUALITY_STEP_SEC,
) -> list[dict]:
    """Окна качества: амплитуда (p75 модуля несущей) и медианная частота
    (по фазе) в окне; браковка по амплитуде — см. `motion_reject_windows`."""
    windows = []
    for start, end in iter_windows(len(wave), fs, window_sec, step_sec):
        seg = wave[start:end]
        rate_seg = rate_cpm[start:end]
        windows.append({
            "t_start": float(t_grid[start]),
            "t_end": float(t_grid[end - 1]),
            "amp_mg": float(np.percentile(np.abs(seg), 75)),
            "rate_cpm": float(np.median(rate_seg)) if len(rate_seg) else None,
        })
    rejected = motion_reject_windows([w["amp_mg"] for w in windows])
    for w, r in zip(windows, rejected):
        w["rejected"] = r
    return windows


def analyze_breathing(samples: list[tuple[float, int, int, int]]) -> dict | None:
    """Полный расчёт по сессии. `samples` — [(ts, x, y, z), …] абсолютные
    секунды эпохи + мг, как отдаёт `hrv_core.db.load_accel_samples`.

    None, если отсчётов или длительности не хватает даже на одну оценку —
    вызывающая сторона (эндпойнт) обязана явно сообщить об этом, а не
    отдавать пустые массивы (см. ARCHITECTURE.md)."""
    if len(samples) < 2:
        return None

    arr = np.asarray(samples, dtype=float)
    order = np.argsort(arr[:, 0])
    ts = arr[order, 0]
    xyz = arr[order, 1:4]

    if float(ts[-1] - ts[0]) < MIN_DURATION_SEC:
        return None

    resampled = resample_uniform(ts, xyz, RESAMPLE_HZ)
    if resampled is None:
        return None
    t_grid, uni = resampled

    filtered = np.stack([bandpass(uni[:, k], RESAMPLE_HZ) for k in range(3)], axis=1)
    axis, _p75 = select_carrier_axis(filtered)
    wave = filtered[:, axis]

    phase = hilbert_phase(wave)
    rate_cpm = instantaneous_rate_cpm(phase, RESAMPLE_HZ)
    boundaries = cycle_boundaries(phase, t_grid)
    windows = quality_windows(t_grid, wave, rate_cpm, RESAMPLE_HZ)

    # Края ряда частоты негодны и должны быть видны как разрыв, а не как
    # медленное дыхание. `filtfilt` и `hilbert` на конечном сигнале дают
    # переходный процесс, и на трёх живых записях 2026-09-06 (222-224) он
    # выглядел одинаково: плавный подъём с 3.5-8.5 до нормальных 10-14
    # цикл/мин за первые ~20 секунд. Совпадение формы у трёх независимых
    # записей и есть доказательство, что это прибор, а не дыхание.
    # Окна качества считаются до маскирования — им нужен полный ряд.
    rate_cpm = rate_cpm.astype(float).copy()
    edge = min(int(round(RATE_SMOOTH_SEC * RESAMPLE_HZ)), len(rate_cpm) // 3)
    if edge > 0:
        rate_cpm[:edge] = np.nan
        rate_cpm[-edge:] = np.nan

    good = [w for w in windows if not w["rejected"]]
    good_rates = [w["rate_cpm"] for w in good if w["rate_cpm"] is not None]
    amps = [w["amp_mg"] for w in windows]

    return {
        "t": t_grid,
        "wave": wave,
        "rate_cpm": rate_cpm,
        "phase": phase,
        "fs": RESAMPLE_HZ,
        "axis": "XYZ"[axis],
        "cycle_boundaries": boundaries,
        "total_cycles": cycles_between(phase, 0, -1),
        "windows": windows,
        "summary": {
            "axis": "XYZ"[axis],
            "n_windows": len(windows),
            "n_good": len(good),
            "good_fraction": (len(good) / len(windows)) if windows else None,
            "cpm_median": float(np.median(good_rates)) if good_rates else None,
            "amp_median_mg": float(np.median(amps)) if amps else None,
        },
    }


def decimate_for_transport(
    t: np.ndarray,
    arrays: list[np.ndarray],
    max_points: int,
    *,
    max_hz: float = 4.0,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Прореживание для передачи на фронт: не больше `max_points` точек и не
    гуще `max_hz` точек в секунду (данные внутри считаются на `RESAMPLE_HZ`,
    для графика это избыточно) — оба потолка учитываются одновременно."""
    n = len(t)
    if n == 0:
        return t, arrays
    duration = float(t[-1] - t[0]) if n > 1 else 0.0
    cap_by_rate = max(2, int(round(duration * max_hz))) if duration > 0 else n
    target = max(2, min(max_points, cap_by_rate, n))
    if n <= target:
        return t, arrays
    idx = np.linspace(0, n - 1, target).astype(int)
    return t[idx], [a[idx] for a in arrays]
