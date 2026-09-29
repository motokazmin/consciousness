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
    python -m hrv_core.pmd_check [--mac AA:BB:..] [--seconds 30]

Тестами не покрыта: живой BLE в тестах не участвует (см. tests/).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

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


async def check(mac: str | None, seconds: float) -> int:
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

        stream = PmdAccStream(client, on_batch, on_event=print)

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

        print(f"\nИтого за {elapsed:.1f}s: кадров {stream.total_frames}, "
              f"отсчётов {stream.total_samples} (из них в пачках для БД: {len(samples)})")
        if stream.total_samples == 0:
            print("Поток не пошёл: договорились о частоте, старт и стоп подтверждены "
                  "control point'ом (см. статусы выше), а кадров данных всё равно нет. "
                  "Смотреть вывод трёх вариантов подписки на PMD_DATA выше — какой из них "
                  "дал кадры, если хоть один.")
            return 5

        fs_actual = stream.total_samples / elapsed
        print(f"фактическая частота (отсчётов/время): {fs_actual:.1f} Гц "
              f"(запрошено {hz:.0f} Гц)")
        if hz and abs(fs_actual - hz) / hz > 0.20:
            print(
                "ВНИМАНИЕ: фактическая частота расходится с запрошенной больше чем на "
                "20% — вероятный признак того, что разбор дельта-кадров даёт неверное "
                "число отсчётов (см. hex кадров выше)."
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

        br = breathing_from_acc(samples, fs_actual)
        if br is None:
            print("\nОтсчётов мало для оценки дыхания (это диагностика, не метрика — "
                  "в БД и на графики не идёт).")
            return 0
        print(
            f"\nДиагностика дыхания (не метрика, только для проверки на слух/на глаз): "
            f"ось {br['axis']}, {br['cpm']:.1f} цикл/мин, "
            f"доля дыхательной полосы в мощности {br['band_share_pct']:.0f}%."
        )
        print("Сверить с тем, как Роман дышал в эти секунды.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mac", help="MAC датчика, если не искать сканированием")
    ap.add_argument("--seconds", type=float, default=30.0, help="длина проверки потока")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(check(args.mac, args.seconds)))
    except Exception as exc:  # проверочная команда: важен диагноз, не стектрейс
        hint = format_bleak_connect_error(exc)
        print(f"Не удалось: {exc}")
        if hint:
            print(hint)
        sys.exit(1)


if __name__ == "__main__":
    main()
