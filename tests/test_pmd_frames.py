"""Разбор кадров PMD-акселерометра и маски возможностей — чистые функции, без BLE."""

import struct

from hrv_core.pmd import ACC_TYPE, decode_features, parse_acc_frame


def _header(frame_type: int) -> bytes:
    # тип(1) + timestamp(8, значение не используется парсером) + frame_type(1)
    return bytes([ACC_TYPE]) + (12345).to_bytes(8, "little") + bytes([frame_type])


def _pack_bits(values: list[int], width: int) -> bytes:
    """Обратное к hrv_core.pmd._unpack_bits: битовый поток, младшими вперёд."""
    total_bits = width * len(values)
    buf = bytearray((total_bits + 7) // 8)
    bit_index = 0
    mask = (1 << width) - 1
    for v in values:
        uv = v & mask
        for i in range(width):
            if (uv >> i) & 1:
                buf[bit_index >> 3] |= 1 << (bit_index & 7)
            bit_index += 1
    return bytes(buf)


def test_parse_acc_frame_wrong_type_returns_empty():
    assert parse_acc_frame(bytes([0x01]) + bytes(9)) == []


def test_parse_acc_frame_too_short_returns_empty():
    assert parse_acc_frame(bytes([ACC_TYPE, 0, 0, 0])) == []


def test_parse_acc_frame_no_delta():
    body = struct.pack("<hhh", 100, -200, 900) + struct.pack("<hhh", 105, -195, 905)
    frame = _header(0x00) + body
    assert parse_acc_frame(frame) == [(100, -200, 900), (105, -195, 905)]


def test_parse_acc_frame_unknown_frame_type_returns_empty():
    frame = _header(0x02) + b"\x00" * 6
    assert parse_acc_frame(frame) == []


def test_parse_acc_frame_delta_single_block():
    ref = (1000, -500, 16000)
    deltas = [(1, -1, 2), (-2, 3, -1)]  # 3 бита на канал, влезает в [-4, 3]
    flat = [v for d in deltas for v in d]
    body = struct.pack("<hhh", *ref) + bytes([3, len(deltas)]) + _pack_bits(flat, 3)
    frame = _header(0x01) + body

    expected = [ref]
    cur = list(ref)
    for d in deltas:
        cur = [c + v for c, v in zip(cur, d)]
        expected.append(tuple(cur))

    assert parse_acc_frame(frame) == expected


def test_parse_acc_frame_delta_non_byte_width_multi_block():
    """Ширина дельты не кратна 8 (5 бит), плюс второй блок другой ширины (3 бита)."""
    ref = (0, 0, 0)
    block1 = [(3, -4, 5), (-6, 7, -8)]  # width=5 → диапазон [-16, 15]
    block2 = [(1, -1, 1)]  # width=3 → диапазон [-4, 3]
    flat1 = [v for d in block1 for v in d]
    flat2 = [v for d in block2 for v in d]
    body = (
        struct.pack("<hhh", *ref)
        + bytes([5, len(block1)]) + _pack_bits(flat1, 5)
        + bytes([3, len(block2)]) + _pack_bits(flat2, 3)
    )
    frame = _header(0x01) + body

    expected = [ref]
    cur = list(ref)
    for d in block1 + block2:
        cur = [c + v for c, v in zip(cur, d)]
        expected.append(tuple(cur))

    assert parse_acc_frame(frame) == expected


def test_parse_acc_frame_truncated_block_stops_gracefully():
    """Заявленный блок длиннее, чем осталось байт — не падаем, отдаём что распарсили."""
    ref = (10, 20, 30)
    body = struct.pack("<hhh", *ref) + bytes([5, 10])  # обещали 10 отсчётов по 5 бит, данных нет
    frame = _header(0x01) + body
    assert parse_acc_frame(frame) == [ref]


def test_decode_features_mask_ecg_and_accelerometer():
    # 0x05 = бит0 (ЭКГ) + бит2 (акселерометр) — подтверждено на живом ремне (P-001)
    assert decode_features(bytes([0x00, 0x05])) == ["ЭКГ", "акселерометр"]


def test_decode_features_empty_mask():
    assert decode_features(bytes([0x00, 0x00])) == []


def test_decode_features_too_short():
    assert decode_features(b"\x00") == []
    assert decode_features(b"") == []
