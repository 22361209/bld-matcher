from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import IO
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError


PRODUCT_IMAGE_INPUT_MAX_BYTES = 30 * 1024 * 1024
PRODUCT_IMAGE_MAX_PIXELS = 50_000_000
PRODUCT_IMAGE_LARGE_MAX_BYTES = 500 * 1024
PRODUCT_IMAGE_LARGE_MAX_SIZE = (1920, 1920)
PRODUCT_IMAGE_THUMB_MAX_BYTES = 100 * 1024
PRODUCT_IMAGE_THUMB_SIZE = (320, 240)
PRODUCT_IMAGE_OUTPUT_SUFFIX = ".webp"


@dataclass(frozen=True, slots=True)
class ProcessedProductImage:
    large: bytes
    thumbnail: bytes
    large_size: tuple[int, int]
    thumbnail_size: tuple[int, int]


def _rewind(source: IO[bytes]) -> None:
    try:
        source.seek(0)
    except (AttributeError, OSError):
        pass


def _normalized_image(source: str | Path | IO[bytes]) -> Image.Image:
    if not isinstance(source, (str, Path)):
        _rewind(source)
    try:
        with Image.open(source) as opened:
            if opened.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("产品图片支持 JPG、PNG、WEBP。")
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > PRODUCT_IMAGE_MAX_PIXELS:
                raise ValueError("产品图片总像素不能超过 5000 万。")
            if int(getattr(opened, "n_frames", 1) or 1) > 1:
                raise ValueError("产品图片不支持动画格式。")
            transposed = ImageOps.exif_transpose(opened)
            transposed.load()
            has_alpha = transposed.mode in {"RGBA", "LA"} or "transparency" in transposed.info
            image = transposed.convert("RGBA" if has_alpha else "RGB")
            image.info.clear()
            return image
    except ValueError:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as exc:
        raise ValueError("文件内容不是有效的产品图片。") from exc
    finally:
        if not isinstance(source, (str, Path)):
            _rewind(source)


def _fit_size(size: tuple[int, int], bounds: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    scale = min(bounds[0] / width, bounds[1] / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _webp_bytes(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="WEBP", quality=quality, method=4, exact=True)
    return buffer.getvalue()


def _encode_webp_under_limit(
    source: Image.Image,
    *,
    bounds: tuple[int, int],
    max_bytes: int,
    maximum_quality: int,
    minimum_quality: int,
) -> tuple[bytes, tuple[int, int]]:
    fitted_size = _fit_size(source.size, bounds)
    fitted = source if fitted_size == source.size else source.resize(fitted_size, Image.Resampling.LANCZOS)
    target_long_side = max(fitted.size)

    while True:
        if max(fitted.size) != target_long_side:
            scale = target_long_side / max(fitted.size)
            current_size = (
                max(1, round(fitted.width * scale)),
                max(1, round(fitted.height * scale)),
            )
            current = fitted.resize(current_size, Image.Resampling.LANCZOS)
        else:
            current = fitted

        low_quality = minimum_quality if target_long_side > 480 else min(minimum_quality, 35)
        low_payload = _webp_bytes(current, low_quality)
        if len(low_payload) <= max_bytes:
            best = low_payload
            low = low_quality + 1
            high = maximum_quality
            while low <= high:
                quality = (low + high) // 2
                payload = _webp_bytes(current, quality)
                if len(payload) <= max_bytes:
                    best = payload
                    low = quality + 1
                else:
                    high = quality - 1
            return best, current.size

        if target_long_side <= 160:
            raise ValueError("产品图片无法在保证可识别的前提下压缩到大小上限。")
        target_long_side = max(160, round(target_long_side * 0.85))


def process_product_image(source: str | Path | IO[bytes]) -> ProcessedProductImage:
    image = _normalized_image(source)
    large, large_size = _encode_webp_under_limit(
        image,
        bounds=PRODUCT_IMAGE_LARGE_MAX_SIZE,
        max_bytes=PRODUCT_IMAGE_LARGE_MAX_BYTES,
        maximum_quality=84,
        minimum_quality=60,
    )
    thumbnail, thumbnail_size = _processed_thumbnail(image)
    return ProcessedProductImage(
        large=large,
        thumbnail=thumbnail,
        large_size=large_size,
        thumbnail_size=thumbnail_size,
    )


def _processed_thumbnail(image: Image.Image) -> tuple[bytes, tuple[int, int]]:
    return _encode_webp_under_limit(
        image,
        bounds=PRODUCT_IMAGE_THUMB_SIZE,
        max_bytes=PRODUCT_IMAGE_THUMB_MAX_BYTES,
        maximum_quality=80,
        minimum_quality=60,
    )


def process_product_thumbnail(source: str | Path | IO[bytes]) -> tuple[bytes, tuple[int, int]]:
    return _processed_thumbnail(_normalized_image(source))


def validate_synced_product_image(payload: bytes) -> tuple[int, int]:
    if not payload or len(payload) > PRODUCT_IMAGE_LARGE_MAX_BYTES:
        raise ValueError("业务数据包产品大图必须严格小于等于 500 KB。")
    try:
        with Image.open(BytesIO(payload)) as opened:
            if opened.format != "WEBP":
                raise ValueError("业务数据包产品图片必须是 WebP 大图。")
            width, height = opened.size
            if (
                width <= 0
                or height <= 0
                or width > PRODUCT_IMAGE_LARGE_MAX_SIZE[0]
                or height > PRODUCT_IMAGE_LARGE_MAX_SIZE[1]
            ):
                raise ValueError("业务数据包产品大图尺寸不能超过 1920×1920。")
            if width * height > PRODUCT_IMAGE_MAX_PIXELS:
                raise ValueError("产品图片总像素不能超过 5000 万。")
            if int(getattr(opened, "n_frames", 1) or 1) > 1:
                raise ValueError("产品图片不支持动画格式。")
            opened.verify()
            return width, height
    except ValueError:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as exc:
        raise ValueError("业务数据包产品图片不是有效的 WebP 大图。") from exc


def process_synced_product_image(payload: bytes) -> ProcessedProductImage:
    large_size = validate_synced_product_image(payload)
    thumbnail, thumbnail_size = process_product_thumbnail(BytesIO(payload))
    return ProcessedProductImage(
        large=payload,
        thumbnail=thumbnail,
        large_size=large_size,
        thumbnail_size=thumbnail_size,
    )


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
