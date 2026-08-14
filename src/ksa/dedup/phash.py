"""Перцептивный хеш фотографий.

Нужен для случая, когда текст объявления переписали (или он вовсе пустой),
а фото те же самые — в перепостах это происходит постоянно.

Реализовано на Pillow + numpy, без imagehash/scipy: алгоритм короткий,
а лишняя зависимость с бинарными колёсами того не стоит.
"""

from __future__ import annotations

import functools
from pathlib import Path

import numpy as np
from PIL import Image

HASH_SIZE = 8      # итоговый хеш: 8x8 бит = 64 бита = 16 hex-символов
IMAGE_SIZE = 32    # до какого размера ужимаем перед DCT


@functools.lru_cache(maxsize=1)
def _dct_matrix(n: int) -> np.ndarray:
    """Матрица DCT-II: dct2(x) = M @ x @ M.T."""
    k = np.arange(n)
    matrix = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    matrix[0] *= 1 / np.sqrt(2)
    return matrix * np.sqrt(2 / n)


def phash(path: Path | str) -> str | None:
    """16 hex-символов, либо None если файл не читается как картинка."""
    try:
        with Image.open(path) as img:
            grey = img.convert("L").resize((IMAGE_SIZE, IMAGE_SIZE), Image.LANCZOS)
            pixels = np.asarray(grey, dtype=np.float64)
    except Exception:
        return None

    matrix = _dct_matrix(IMAGE_SIZE)
    lowfreq = (matrix @ pixels @ matrix.T)[:HASH_SIZE, :HASH_SIZE]
    bits = (lowfreq > np.median(lowfreq)).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(left: str | None, right: str | None) -> int:
    """Расстояние между хешами; 64 (максимум) если одного из них нет."""
    if not left or not right:
        return 64
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except ValueError:
        return 64


#Эмпирический порог: ≤8 из 64 бит — та же картинка после пережатия/кропа.
SAME_IMAGE = 8
