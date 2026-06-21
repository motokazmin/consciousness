"""
Переименование mp3 файлов по списку из текстового файла.

Использование:
    python rename_phrases.py sit_meditation.txt

Скрипт читает строки вида "FILE: sit_v_01.mp3" — остальное игнорирует.
Mp3 из текущей папки сортируются по времени создания и сопоставляются
с именами по порядку.
"""

import os
import sys
import glob


def load_names(path: str) -> list[str]:
    names = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("FILE:"):
                name = line[len("FILE:"):].strip()
                if name:
                    names.append(name)
    return names


def main():
    if len(sys.argv) < 2:
        print("Использование: python rename_phrases.py sit_meditation.txt")
        sys.exit(1)

    names_file = sys.argv[1]
    if not os.path.exists(names_file):
        print(f"❌ Файл не найден: {names_file}")
        sys.exit(1)

    names = load_names(names_file)
    if not names:
        print("❌ Строки FILE: не найдены в файле")
        sys.exit(1)

    # Mp3 в текущей папке кроме уже переименованных
    all_mp3 = sorted(glob.glob("*.mp3"), key=lambda f: os.path.getmtime(f))
    files = [f for f in all_mp3 if f not in names]

    print(f"Файлов для переименования : {len(files)}")
    print(f"Имён в списке             : {len(names)}")

    if len(files) != len(names):
        print("\n❌ Количество не совпадает — проверь папку")
        if files:
            print(f"   Первый файл : {files[0]}")
            print(f"   Последний   : {files[-1]}")
        sys.exit(1)

    print("\nПлан переименования:")
    from datetime import datetime
    for old, new in zip(files, names):
        ts = datetime.fromtimestamp(os.path.getmtime(old)).strftime("%H:%M:%S")
        print(f"  [{ts}]  {old}  →  {new}")

    confirm = input("\nПродолжить? (y/n): ").strip().lower()
    if confirm != "y":
        print("Отменено.")
        return

    for old, new in zip(files, names):
        os.rename(old, new)
        print(f"✅  {old}  →  {new}")

    print(f"\nГотово. Переименовано: {len(names)} файлов.")


if __name__ == "__main__":
    main()
