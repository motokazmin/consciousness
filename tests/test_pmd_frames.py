"""Разбор кадров PMD-акселерометра и маски возможностей — чистые функции, без BLE."""

from hrv_core.pmd import (
    ACC_DEFAULT_HZ,
    ACC_RANGE_G,
    ACC_RESOLUTION_BITS,
    ACC_TYPE,
    SETTING_RANGE,
    SETTING_RESOLUTION,
    SETTING_SAMPLE_RATE,
    build_acc_start_command,
    decode_features,
    more_frames_pending,
    parse_acc_frame,
    parse_measurement_settings,
)


def _settings_block(setting_type: int, values: list[int]) -> bytes:
    """Обратное к parse_measurement_settings: один блок [тип][счёт(1 байт)][значения]."""
    body = bytes([setting_type, len(values)])
    for v in values:
        body += v.to_bytes(2, "little")
    return body


def _header(frame_type: int) -> bytes:
    # тип(1) + timestamp(8, значение не используется парсером) + frame_type(1)
    return bytes([ACC_TYPE]) + (12345).to_bytes(8, "little") + bytes([frame_type])


def test_parse_acc_frame_wrong_type_returns_empty():
    assert parse_acc_frame(bytes([0x01]) + bytes(9)) == []


def test_parse_acc_frame_too_short_returns_empty():
    assert parse_acc_frame(bytes([ACC_TYPE, 0, 0, 0])) == []


def test_parse_acc_frame_body_not_multiple_of_6_returns_empty():
    frame = _header(0x01) + b"\x00" * 7  # 7 не кратно 6
    assert parse_acc_frame(frame) == []


def test_parse_acc_frame_real_device_frame():
    """Настоящий кадр живого H10 (обрезан на 16 байт от конца — тело 210 байт
    = 35 отсчётов). Раскладка и первые три отсчёта подтверждены ручным
    разбором hex (см. задачу): дельта-упаковки в реальном потоке нет."""
    raw = bytes.fromhex(
        "025098f19ebd4352080169fc1000400167fc0d0043016bfc130040016cfc1300"
        "430165fc1100420168fc12003d0169fc12003e016afc13003e0165fc13003a01"
        "68fc0e003c0169fc0f00400168fc0f003f0167fc0f00410167fc1000430169fc"
        "13003c0168fc13003a0167fc15003d0168fc15003c0169fc14003e0167fc1400"
        "3f0166fc16003d016bfc13003d016bfc16003c016cfc19003d0167fc16004701"
        "69fc1900390169fc190041016bfc1900430166fc19003f0169fc19003e016efc"
        "1a00410168fc1600460168fc180045016bfc1a0045016dfc1b004401"
    )
    samples = parse_acc_frame(raw)
    assert len(samples) == 35
    assert samples[:3] == [(-919, 16, 320), (-921, 13, 323), (-917, 19, 320)]


def test_decode_features_mask_ecg_and_accelerometer():
    # 0x05 = бит0 (ЭКГ) + бит2 (акселерометр) — подтверждено на живом ремне (P-001)
    assert decode_features(bytes([0x00, 0x05])) == ["ЭКГ", "акселерометр"]


def test_decode_features_empty_mask():
    assert decode_features(bytes([0x00, 0x00])) == []


def test_decode_features_too_short():
    assert decode_features(b"\x00") == []
    assert decode_features(b"") == []


def test_parse_measurement_settings_matches_real_device_reply():
    """Эталон: настоящий ответ живого H10 на запрос настроек акселерометра
    (см. задачу). Заголовок (F0 01 02 00 00 — 5 байт: op, type, error, more)
    уже отрезан вызывающей стороной; здесь проверяется разбор тела. Счётчик
    значений в блоке — 1 байт, не 2 (это и было причиной, почему старый разбор
    возвращал пустоту), и устройство не объявляет channels."""
    raw = bytes.fromhex("f0010200000004190032006400c800010110000203020004000800")
    assert raw[:5] == bytes([0xF0, 0x01, ACC_TYPE, 0x00, 0x00])
    body = raw[5:]
    assert parse_measurement_settings(body) == {
        SETTING_SAMPLE_RATE: [25, 50, 100, 200],
        SETTING_RESOLUTION: [16],
        SETTING_RANGE: [2, 4, 8],
    }


def test_parse_measurement_settings_without_channels():
    """Устройство не объявляет channels вовсе (подтверждено на живом H10) —
    разбор не должен падать и не должен ничего домысливать."""
    body = _settings_block(SETTING_SAMPLE_RATE, [25]) + _settings_block(SETTING_RESOLUTION, [16])
    assert parse_measurement_settings(body) == {
        SETTING_SAMPLE_RATE: [25],
        SETTING_RESOLUTION: [16],
    }


def test_parse_measurement_settings_empty():
    assert parse_measurement_settings(b"") == {}


def test_parse_measurement_settings_truncated_block_stops_gracefully():
    """Обещали 2 значения, байт хватает только на тип+счёт — не падаем."""
    body = _settings_block(SETTING_SAMPLE_RATE, [25]) + bytes([SETTING_RANGE, 0x02])
    assert parse_measurement_settings(body) == {SETTING_SAMPLE_RATE: [25]}


def test_more_frames_pending_flag_set():
    assert more_frames_pending(bytes([0xF0, 0x02, ACC_TYPE, 0x00, 0x01])) is True


def test_more_frames_pending_flag_clear():
    assert more_frames_pending(bytes([0xF0, 0x02, ACC_TYPE, 0x00, 0x00])) is False


def test_more_frames_pending_short_frame_is_false():
    assert more_frames_pending(bytes([0xF0, 0x02, ACC_TYPE, 0x00])) is False


def test_build_acc_start_command_from_declared_settings_has_no_channels():
    """Настоящий ответ H10 не объявляет channels — команда старта не содержит
    эту настройку: её отправка была диагностирована как причина
    INVALID_PARAMETER, гипотеза «пропущен channels» опровергнута."""
    settings = {
        SETTING_SAMPLE_RATE: [25, 50, 100, 200],
        SETTING_RESOLUTION: [16],
        SETTING_RANGE: [2, 4, 8],
    }
    command = build_acc_start_command(settings)
    assert command == bytes([
        0x02, ACC_TYPE,
        0x00, 0x01, 25, 0x00,
        0x01, 0x01, 16, 0x00,
        0x02, 0x01, 8, 0x00,
    ])


def test_build_acc_start_command_picks_lowest_rate_at_or_above_default():
    settings = {SETTING_SAMPLE_RATE: [13, 26, 52], SETTING_RESOLUTION: [16]}
    command = build_acc_start_command(settings)
    hz = command[4] | (command[5] << 8)
    assert hz == 26  # ближайшая большая, раз 25 самой нет в списке


def test_build_acc_start_command_falls_back_to_hardcoded_when_no_settings_declared():
    """Устройство ничего не объявило (пустой ответ) — откат на жёстко зашитый
    набор из трёх настроек, без channels: именно ту команду, которую
    устройство один раз уже приняло (SUCCESS)."""
    command = build_acc_start_command({})
    assert command == bytes([
        0x02, ACC_TYPE,
        SETTING_SAMPLE_RATE, 0x01, ACC_DEFAULT_HZ & 0xFF, (ACC_DEFAULT_HZ >> 8) & 0xFF,
        SETTING_RESOLUTION, 0x01, ACC_RESOLUTION_BITS & 0xFF, (ACC_RESOLUTION_BITS >> 8) & 0xFF,
        SETTING_RANGE, 0x01, ACC_RANGE_G & 0xFF, (ACC_RANGE_G >> 8) & 0xFF,
    ])


def test_build_acc_start_command_unknown_setting_type_is_not_dropped():
    """Объявленный тип, о котором у нас нет мнения, всё равно попадает в
    команду (первое значение) — правило «ничего не пропускать» относится
    только к тому, что реально объявлено."""
    settings = {SETTING_SAMPLE_RATE: [25], SETTING_RESOLUTION: [16], 0x09: [7]}
    command = build_acc_start_command(settings)
    assert bytes([0x09, 0x01, 7, 0x00]) in command
