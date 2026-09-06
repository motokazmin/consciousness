"""Отметки вдохов с клавиатуры: опорный ряд для сверки с акселерометром.

Зачем: сверять итоговый счёт («насчитал 60 за три с половиной минуты») с
прибором нечестно — человек может сбиться, и разойдётся счёт с прибором или
прибор с дыханием, различить нельзя. Нажатие на каждом вдохе даёт не одно
число, а ряд моментов: сверяется каждый цикл отдельно, сбиться незаметно
невозможно.

Часы общие с дампом акселерометра: здесь `time.time()`, в шапке дампа
`pmd_check --dump` — `first_frame_wall_time` из тех же часов.

Запуск во втором терминале, параллельно с `pmd_check`:

    python -m research.tools.breath_marks ~/breath_run3.marks

Enter — отметка. Ctrl-D (или пустая строка после `.`) — конец. Файл пишется
сразу на каждой отметке: прерывание не теряет уже нажатое.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    path = Path(sys.argv[1]).expanduser()
    n = 0
    started = time.time()
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# breath marks, wall clock (time.time()), started {started:.3f}\n")
        f.flush()
        print(f"Пишу в {path}. Enter — отметка, Ctrl-D — конец.")
        while True:
            try:
                line = sys.stdin.readline()
            except KeyboardInterrupt:
                break
            if not line:  # Ctrl-D
                break
            if line.strip() == ".":
                break
            ts = time.time()
            n += 1
            f.write(f"{ts:.3f}\n")
            f.flush()
            # Печатаем скупо: экран не должен тянуть внимание на себя.
            if n % 10 == 0:
                print(f"  {n}")
    dur = time.time() - started
    rate = 60.0 * n / dur if dur > 0 else 0.0
    print(f"Отметок: {n} за {dur:.0f} c — {rate:.1f} в минуту. Файл: {path}")


if __name__ == "__main__":
    main()
