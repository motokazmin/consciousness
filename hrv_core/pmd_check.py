"""Проверочная команда для человека: PMD-акселерометр на живом Polar H10.

**Важно.** Второй живой прогон подтвердил разбор настроек (заголовок ответа —
5 байт, счётчик значений в блоке — 1 байт) и опроверг гипотезу о недостающей
настройке `channels`: она устройством не объявляется, а если добавить её в
команду — старт отвергается (INVALID_PARAMETER). Команда старта без channels
получила SUCCESS, и последующий стоп тоже вернул SUCCESS (а не
ALREADY_IN_STATE) — значит измерение реально запускалось. При этом ни одного
кадра данных так и не пришло. Открытый вопрос теперь — доставка BLE-уведомлений
(см. `hrv_core/pmd.py`, `_ensure_data_after_start` — три варианта подписки).
Поэтому эта команда не просто говорит «работает» или «не работает» — она
печатает сырьё на каждом шаге, чтобы по одному прогону было видно, где именно
разбор разошёлся с реальностью, если разошёлся.

Запуск (ремень надет, не занят другим клиентом BLE):
    python -m hrv_core.pmd_check [--mac AA:BB:..] [--seconds 30] [--dump raw.csv]

`--dump PATH` сохраняет все принятые отсчёты в CSV (сырьё, не только пик по
Уэлчу — см. `_write_dump`): пригодится, если понадобится разобрать запись
другим способом, чем текущая диагностика дыхания.

Тестами не покрыта: живой BLE в тестах не участвует (см. tests/).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from pathlib import Path

from hrv_core.ble_scan import (
    bleak_adapter_kwargs,
    bluetoothctl_paired,
    discover_ble_devices,
    format_bleak_connect_error,
)
from hrv_core.pmd import (
    PMD_CONTROL,
    PMD_DATA,
    PMD_SERVICE,
    PAIRING_HINT,
    PmdAccStream,
    PmdError,
    PmdPairingRequiredError,
    breathing_from_acc,
)

# Печатать снимок счётчиков кадров/отсчётов каждые столько секунд.
SNAPSHOT_EVERY_SEC = 5.0

DumpRow = tuple[int, int, int | None, float, int, int, int]


def _write_dump(
    path: Path,
    rows: list[DumpRow],
    requested_hz: float,
    measured_hz: float | None,
    total_frames: int,
    total_samples: int,
    measured_window: float | None,
    first_frame_wall_time: float | None,
) -> None:
    """Все принятые отсчёты в CSV — пик по Уэлчу не единственный способ
    разобрать запись, а печатью в терминал прогон не сохраняется.

    Первая строка — комментарий `#` с метаданными прогона, включая
    `time.time()` первого кадра с отсчётами (привязать запись к внешним
    событиям по стенным часам). Дальше — обычный CSV с заголовком."""
    measured_txt = f"{measured_hz:.2f}" if measured_hz is not None else "н/д"
    window_txt = f"{measured_window:.1f}" if measured_window is not None else "н/д"
    wall_txt = f"{first_frame_wall_time:.3f}" if first_frame_wall_time is not None else "н/д"
    with path.open("w", newline="") as f:
        f.write(
            f"# requested_hz={requested_hz:.0f} measured_hz={measured_txt} "
            f"total_frames={total_frames} total_samples={total_samples} "
            f"stream_window_s={window_txt} first_frame_wall_time={wall_txt}\n"
        )
        writer = csv.writer(f)
        writer.writerow(
            ["frame_idx", "sample_idx", "device_ts_ns", "host_monotonic", "x", "y", "z"]
        )
        writer.writerows(rows)
    print(f"\nДамп отсчётов: {path} ({len(rows)} строк)")


async def check(mac: str | None, seconds: float, dump: Path | None = None) -> int:
    from bleak import BleakClient

    if mac is None:
        print("Ищу датчик…")
        devices = await discover_ble_devices(timeout=10.0)
        polar = [d for d in devices if (d.name or "").lower().startswith("polar")]
        if not polar:
            names = ", ".join(sorted({d.name for d in devices if d.name})) or "ничего"
            print(f"Polar не найден. Видно: {names}")
            return 1
        mac = polar[0].address
        print(f"Нашёл: {polar[0].name} [{mac}]")

    paired = bluetoothctl_paired(mac)
    paired_txt = {True: "да", False: "нет", None: "не удалось узнать"}[paired]
    print(f"Сопряжение (bluetoothctl): {paired_txt}")
    if paired is False:
        print(f"Подсказка заранее: {PAIRING_HINT}")

    async with BleakClient(mac, **bleak_adapter_kwargs()) as client:
        print("Связь: подключено ✓")

        has_pmd = any(s.uuid.lower() == PMD_SERVICE for s in client.services)
        print(f"Сервис PMD: {'есть' if has_pmd else 'НЕТ'}")
        if not has_pmd:
            print("Сервиса PMD на устройстве нет. Дальше идти некуда.")
            return 2

        for service in client.services:
            if service.uuid.lower() != PMD_SERVICE:
                continue
            for ch in service.characteristics:
                uuid = ch.uuid.lower()
                if uuid == PMD_CONTROL:
                    label = "control point"
                elif uuid == PMD_DATA:
                    label = "data"
                else:
                    continue
                props = ",".join(ch.properties)
                print(f"Характеристика {label}: свойства=[{props}]")
                if uuid == PMD_DATA and "notify" not in ch.properties:
                    print(
                        "ВНИМАНИЕ: у data-характеристики нет notify — поток "
                        "физически не может подписаться, дело не в команде старта."
                    )

        batches: list[tuple[float, list[tuple[int, int, int]], float]] = []

        def on_batch(batch_ts: float, samples: list[tuple[int, int, int]], hz: float) -> None:
            batches.append((batch_ts, samples, hz))

        dump_rows: list[DumpRow] = []
        first_frame_wall_time: list[float] = []  # 0 или 1 элемент — проще, чем nonlocal

        def on_frame(
            frame_idx: int,
            device_ts_ns: int | None,
            host_monotonic: float,
            samples: list[tuple[int, int, int]],
        ) -> None:
            if not first_frame_wall_time:
                first_frame_wall_time.append(time.time())
            for sample_idx, (x, y, z) in enumerate(samples):
                dump_rows.append((frame_idx, sample_idx, device_ts_ns, host_monotonic, x, y, z))

        stream = PmdAccStream(
            client, on_batch, on_event=print, on_frame=on_frame if dump else None
        )

        print("\n=== Маска возможностей и договор о частоте акселерометра ===")
        t0 = time.time()
        try:
            hz = await stream.start()
        except PmdPairingRequiredError as exc:
            print(f"\nПрошивка требует сопряжения: {exc}")
            return 3
        except PmdError as exc:
            print(f"\nPMD отказал: {exc}")
            return 4
        print(f"\nЧастота принята устройством: {hz:.0f} Гц")

        print(f"\n=== Поток данных, {seconds:.0f} с ===")
        next_snapshot = SNAPSHOT_EVERY_SEC
        while time.time() - t0 < seconds:
            await asyncio.sleep(min(1.0, seconds - (time.time() - t0)))
            elapsed = time.time() - t0
            if elapsed >= next_snapshot or elapsed >= seconds:
                print(
                    f"[{elapsed:5.1f}s] кадров всего: {stream.total_frames}, "
                    f"отсчётов всего: {stream.total_samples}"
                )
                next_snapshot += SNAPSHOT_EVERY_SEC

        await stream.stop()
        elapsed = time.time() - t0

        samples: list[tuple[int, int, int]] = []
        for _, s, _ in batches:
            samples.extend(s)

        measured_hz = stream.measured_hz
        measured_window = stream.measured_window_s

        if dump:
            _write_dump(
                dump, dump_rows, hz, measured_hz, stream.total_frames, stream.total_samples,
                measured_window, first_frame_wall_time[0] if first_frame_wall_time else None,
            )

        print(f"\nИтого за {elapsed:.1f}s: кадров {stream.total_frames}, "
              f"отсчётов {stream.total_samples} (из них в пачках для БД: {len(samples)})")
        if stream.total_samples == 0:
            print("Поток не пошёл: договорились о частоте, старт и стоп подтверждены "
                  "control point'ом (см. статусы выше), а кадров данных всё равно нет. "
                  "Смотреть вывод трёх вариантов подписки на PMD_DATA выше — какой из них "
                  "дал кадры, если хоть один.")
            return 5

        if measured_hz is None:
            print(
                "фактическую частоту потока измерить не удалось (кадров с отсчётами "
                "меньше двух, или интервал между первым и последним нулевой) — "
                "пропускаю диагностику дыхания."
            )
            return 0
        print(
            f"фактическая частота (по кадрам потока): {measured_hz:.1f} Гц "
            f"(запрошено {hz:.0f} Гц, окно измерения {measured_window:.1f}s из "
            f"{elapsed:.1f}s общего времени команды)"
        )
        if hz and abs(measured_hz - hz) / hz > 0.20:
            print(
                "ВНИМАНИЕ: фактическая частота расходится с запрошенной больше чем на "
                "20% — механика потока (потери кадров, троттлинг BLE), не разбор кадров: "
                "он проверен отдельно (см. hex кадров выше)."
            )

        if not samples:
            print("Отсчётов для БД-пачек нет (буфер не успел набраться) — "
                  "по сырым кадрам выше видно, идут ли вообще данные.")
            return 0

        arr_stats = []
        for k, name in enumerate("XYZ"):
            vals = [s[k] for s in samples]
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            std = var ** 0.5
            arr_stats.append((name, mean, std, min(vals), max(vals)))
        print("\nПо осям (мг):")
        for name, mean, std, lo, hi in arr_stats:
            print(f"  {name}: среднее {mean:8.0f}  разброс {std:7.1f}  диапазон [{lo}, {hi}]")

        br = breathing_from_acc(samples, measured_hz)
        if br is None:
            print("\nОтсчётов мало для оценки дыхания (это диагностика, не метрика — "
                  "в БД и на графики не идёт).")
            return 0
        print(
            f"\nДиагностика дыхания по окнам (не метрика, только для проверки на "
            f"слух/на глаз, в БД и на графики не идёт). Окно {br['window_sec']:.0f}с, "
            f"шаг 30с, оценка квантована шагом {br['bin_cpm']:.1f} цикл/мин:"
        )
        for w in br["windows"]:
            mark = "БРАК (движение)" if w["rejected"] else "ок"
            print(
                f"  [{w['t_start_sec']:6.0f}–{w['t_end_sec']:6.0f}s] ось {w['axis']}  "
                f"{w['cpm']:5.1f} цикл/мин  полоса {w['band_share_pct']:4.0f}%  "
                f"ампл {w['amp_mg']:6.1f} мг  {mark}"
            )
        if br["cpm_median"] is None:
            print(
                f"\nВсе {br['n_windows']} окон забракованы по движению — устойчивой "
                "оценки дыхания на этом прогоне нет."
            )
        else:
            print(
                f"\nИтог по {br['n_quiet']}/{br['n_windows']} спокойным окнам: "
                f"медиана {br['cpm_median']:.1f} цикл/мин "
                f"(разброс {br['cpm_min']:.1f}–{br['cpm_max']:.1f})."
            )
        print("Сверить с тем, как Роман дышал в эти секунды.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mac", help="MAC датчика, если не искать сканированием")
    ap.add_argument("--seconds", type=float, default=30.0, help="длина проверки потока")
    ap.add_argument(
        "--dump", type=Path, default=None,
        help="сохранить все принятые отсчёты в CSV по этому пути (по умолчанию не пишем)",
    )
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(check(args.mac, args.seconds, args.dump)))
    except Exception as exc:  # проверочная команда: важен диагноз, не стектрейс
        hint = format_bleak_connect_error(exc)
        print(f"Не удалось: {exc}")
        if hint:
            print(hint)
        sys.exit(1)


if __name__ == "__main__":
    main()
