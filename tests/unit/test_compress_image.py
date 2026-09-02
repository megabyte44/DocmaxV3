"""``compress-image`` — shrink an image in place, preserving format.

This tool is pure-Python (Pillow), so all tests run with a real compression
when Pillow is available. Tests verify:

* Spec shape and configuration
* Format preservation (input format must match output format)
* Quality parameter validation
* Pillow availability handling
* Real compression reduces or maintains file size
* PNG ignores quality parameter without error
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from docmax.core.cancellation import NEVER_CANCELLED
from docmax.core.config import Config
from docmax.core.errors import InvalidParameterError
from docmax.core.models import DocumentRef, Engine, OutputTarget
from docmax.core.protocols import NULL_PROGRESS
from docmax.core.registry import get_tool
from docmax.core.router import EngineRouter

if TYPE_CHECKING:
    from docmax.core.models import ToolResult

#: Skips if Pillow is not installed, but it's in the base images extra so should
#: always be present in the test environment.
PILLOW_AVAILABLE = importlib.util.find_spec("PIL") is not None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_jpeg(path: Path, size: tuple[int, int] = (100, 100)) -> Path:
    """Create a minimal JPEG for testing."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Create a simple gradient image
    image = Image.new("RGB", size, color="red")
    image.save(path, format="JPEG", quality=95)
    return path


def make_png(path: Path, size: tuple[int, int] = (100, 100)) -> Path:
    """Create a minimal PNG for testing."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Create a simple solid color PNG
    image = Image.new("RGB", size, color="blue")
    image.save(path, format="PNG")
    return path


def compress_image(source: Path, destination: Path, **extra: Any) -> ToolResult:
    """Helper to run compress-image through the router."""
    router = EngineRouter(config=Config())
    return router.run(
        "compress-image",
        [DocumentRef.from_path(source)],
        OutputTarget(destination=destination),
        progress=NULL_PROGRESS,
        cancellation=NEVER_CANCELLED,
        **extra,
    )


# ---------------------------------------------------------------------------
# Spec and Configuration
# ---------------------------------------------------------------------------


def test_spec_shape() -> None:
    """Verify the tool spec is correctly configured."""
    spec = get_tool("compress-image")

    assert spec.name == "compress-image"
    assert spec.category == "image"
    assert spec.accepts_multiple_inputs is False
    # ADR 0033 pins `output_required` to `convert` and `metadata` alone
    # (test_registry.py). compress-image stays False: the CLI already makes
    # `-o` mandatory, and a derived destination that disagrees with the input's
    # format is refused by `run()` rather than written mislabelled.
    assert spec.output_required is False
    assert Engine.LOCAL in spec.supported_engines
    assert Engine.CLOUD not in spec.supported_engines

    # Check params
    param_names = {p.name for p in spec.params}
    assert "quality" in param_names
    assert "format" not in param_names  # No format param; tool preserves format

    # Find quality param
    quality_param = next(p for p in spec.params if p.name == "quality")
    assert quality_param.type_ == "int"
    assert quality_param.default == 80


def test_unavailable_when_pillow_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Pillow is missing, the tool reports unavailability."""
    import importlib

    from docmax.tools.compress_image import local

    # Mock find_spec to return None for PIL
    def mock_find_spec(name: str):  # type: ignore[no-untyped-def]
        if name == "PIL":
            return None
        return importlib.util.find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", mock_find_spec)

    # Reload the module to pick up the mocked find_spec
    importlib.reload(local)

    strategy = local.CompressImageLocal()
    assert strategy.is_available() is False
    reason = strategy.unavailable_reason()
    assert reason is not None
    assert "Pillow" in reason
    assert "pip install" in reason


# ---------------------------------------------------------------------------
# Format Preservation
# ---------------------------------------------------------------------------


def test_destination_format_must_match_input(tmp_path: Path) -> None:
    """Requesting a different output format is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.png"  # Mismatch: JPEG input, PNG output

    with pytest.raises(InvalidParameterError) as exc_info:
        compress_image(source, destination)

    error = exc_info.value
    assert "format" in str(error).lower()
    assert "jpeg" in str(error).lower()
    assert "png" in str(error).lower()
    # Should suggest using convert
    assert "convert" in (error.remedy or "").lower()


# ---------------------------------------------------------------------------
# Quality Validation
# ---------------------------------------------------------------------------


def test_quality_too_low(tmp_path: Path) -> None:
    """Quality below 1 is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        compress_image(source, destination, quality=0)

    error = exc_info.value
    assert "quality" in error.context.get("parameter", "").lower()
    assert "1" in str(error)
    assert "95" in str(error)


def test_quality_too_high(tmp_path: Path) -> None:
    """Quality above 95 is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        compress_image(source, destination, quality=96)

    error = exc_info.value
    assert "quality" in error.context.get("parameter", "").lower()
    assert "1" in str(error)
    assert "95" in str(error)


def test_quality_not_integer(tmp_path: Path) -> None:
    """Quality as a string is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        compress_image(source, destination, quality="80")

    error = exc_info.value
    assert "quality" in error.context.get("parameter", "").lower()
    assert "integer" in str(error).lower()


# ---------------------------------------------------------------------------
# Real Compression
# ---------------------------------------------------------------------------


def test_compress_jpeg(tmp_path: Path) -> None:
    """Compressing a JPEG produces a valid JPEG."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    result = compress_image(source, destination, quality=60)

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()
    assert len(result.outputs) == 1
    assert result.outputs[0] == destination

    # Check details
    assert result.details["format"] == "jpeg"
    assert result.details["quality"] == 60
    assert result.details["original_bytes"] > 0
    assert result.details["compressed_bytes"] > 0
    assert "saved_bytes" in result.details

    # Verify the output is a valid JPEG
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "JPEG"


def test_compress_png_ignores_quality(tmp_path: Path) -> None:
    """PNG compression ignores the quality parameter."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png(tmp_path / "test.png")
    destination = tmp_path / "out.png"

    # PNG should accept quality without error, but ignore it
    result = compress_image(source, destination, quality=10)

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()
    assert result.details["format"] == "png"
    assert result.details["quality"] == 10  # Parameter is accepted

    # Verify the output is a valid PNG
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "PNG"


def test_docs_empty_rejected(tmp_path: Path) -> None:
    """Calling compress-image with no documents is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    destination = tmp_path / "out.jpg"

    router = EngineRouter(config=Config())
    with pytest.raises(InvalidParameterError) as exc_info:
        router.run(
            "compress-image",
            [],  # Empty docs
            OutputTarget(destination=destination),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )

    error = exc_info.value
    assert "document" in str(error).lower()


def test_output_format_preserved_for_all_formats(tmp_path: Path) -> None:
    """Format preservation works for each supported format."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Test JPEG
    jpeg_source = tmp_path / "test.jpg"
    Image.new("RGB", (50, 50), color="red").save(jpeg_source, format="JPEG", quality=95)

    jpeg_dest = tmp_path / "out.jpg"
    result = compress_image(jpeg_source, jpeg_dest)
    assert result.details["format"] == "jpeg"
    assert Image.open(str(jpeg_dest)).format == "JPEG"

    # Test PNG
    png_source = tmp_path / "test.png"
    Image.new("RGB", (50, 50), color="blue").save(png_source, format="PNG")

    png_dest = tmp_path / "out.png"
    result = compress_image(png_source, png_dest)
    assert result.details["format"] == "png"
    assert Image.open(str(png_dest)).format == "PNG"


def test_unknown_image_format_rejected(tmp_path: Path) -> None:
    """An unsupported image format is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    # Create a dummy BMP and try to compress to an unknown extension
    source = tmp_path / "test.jpg"
    from PIL import Image

    Image.new("RGB", (50, 50)).save(source, format="JPEG", quality=95)

    destination = tmp_path / "out.xyz"  # Unknown format

    with pytest.raises(InvalidParameterError) as exc_info:
        compress_image(source, destination)

    error = exc_info.value
    assert "format" in str(error).lower()


def test_details_include_bytes_and_quality(tmp_path: Path) -> None:
    """Result details report original, compressed, and saved bytes, plus quality."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    result = compress_image(source, destination, quality=50)

    assert "original_bytes" in result.details
    assert "compressed_bytes" in result.details
    assert "saved_bytes" in result.details
    assert "quality" in result.details
    assert "format" in result.details

    # Bytes should be consistent
    assert result.details["saved_bytes"] == (
        result.details["original_bytes"] - result.details["compressed_bytes"]
    )
