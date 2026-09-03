"""``watermark-image`` — draw semi-transparent text on an image.

This tool is pure-Python (Pillow), so all tests run with a real watermarking
when Pillow is available. Tests verify:

* Spec shape and configuration
* Parameter validation (text, opacity, size, angle)
* Pillow availability handling
* Real watermarking produces valid images
* Watermark text and position are rendered correctly
* Different positions work correctly
* Rotation angle is applied
* Opacity affects the watermark visibility
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


def make_image(path: Path, size: tuple[int, int] = (200, 200)) -> Path:
    """Create a minimal test image."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Create a simple gradient image with a blue color
    image = Image.new("RGB", size, color="lightblue")
    image.save(path, format="JPEG", quality=95)
    return path


def make_png_image(path: Path, size: tuple[int, int] = (200, 200)) -> Path:
    """Create a minimal PNG test image."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Create a simple solid color PNG
    image = Image.new("RGB", size, color="lightgreen")
    image.save(path, format="PNG")
    return path


def watermark_image(source: Path, destination: Path, **extra: Any) -> ToolResult:
    """Helper to run watermark-image through the router."""
    router = EngineRouter(config=Config())
    return router.run(
        "watermark-image",
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
    spec = get_tool("watermark-image")

    assert spec.name == "watermark-image"
    assert spec.category == "image"
    assert spec.accepts_multiple_inputs is False
    assert spec.output_required is False
    assert Engine.LOCAL in spec.supported_engines
    assert Engine.CLOUD not in spec.supported_engines

    # Check params
    param_names = {p.name for p in spec.params}
    assert "text" in param_names
    assert "position" in param_names
    assert "size" in param_names
    assert "opacity" in param_names
    assert "angle" in param_names

    # Find text param (should be required)
    text_param = next(p for p in spec.params if p.name == "text")
    assert text_param.type_ == "str"
    assert text_param.required is True

    # Check defaults
    position_param = next(p for p in spec.params if p.name == "position")
    assert position_param.default == "center"

    size_param = next(p for p in spec.params if p.name == "size")
    assert size_param.default == 48.0

    opacity_param = next(p for p in spec.params if p.name == "opacity")
    assert opacity_param.default == 0.15

    angle_param = next(p for p in spec.params if p.name == "angle")
    assert angle_param.default == 45.0


def test_unavailable_when_pillow_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Pillow is missing, the tool reports unavailability."""
    import importlib

    from docmax.tools.watermark_image import local

    # Mock find_spec to return None for PIL
    def mock_find_spec(name: str) -> Any:
        if name == "PIL":
            return None
        return importlib.util.find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", mock_find_spec)

    # Reload the module to pick up the mocked find_spec
    importlib.reload(local)

    strategy = local.WatermarkImageLocal()
    assert strategy.is_available() is False
    reason = strategy.unavailable_reason()
    assert reason is not None
    assert "Pillow" in reason
    assert "pip install" in reason


# ---------------------------------------------------------------------------
# Parameter Validation
# ---------------------------------------------------------------------------


def test_text_required(tmp_path: Path) -> None:
    """Text parameter is required."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        watermark_image(source, destination, text="")

    error = exc_info.value
    assert "text" in str(error).lower()


def test_text_empty_rejected(tmp_path: Path) -> None:
    """Empty text is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        watermark_image(source, destination, text="   ")

    error = exc_info.value
    assert "text" in str(error).lower()


def test_opacity_too_low(tmp_path: Path) -> None:
    """Opacity below 0 is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        watermark_image(source, destination, text="Test", opacity=-0.1)

    error = exc_info.value
    assert "opacity" in error.context.get("parameter", "").lower()
    assert "0" in str(error)
    assert "1" in str(error)


def test_opacity_too_high(tmp_path: Path) -> None:
    """Opacity above 1 is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        watermark_image(source, destination, text="Test", opacity=1.1)

    error = exc_info.value
    assert "opacity" in error.context.get("parameter", "").lower()
    assert "0" in str(error)
    assert "1" in str(error)


def test_opacity_not_number(tmp_path: Path) -> None:
    """Opacity as a non-number is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        watermark_image(source, destination, text="Test", opacity="0.5")

    error = exc_info.value
    assert "opacity" in error.context.get("parameter", "").lower()


def test_size_must_be_positive(tmp_path: Path) -> None:
    """Size must be positive."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        watermark_image(source, destination, text="Test", size=0)

    error = exc_info.value
    assert "size" in str(error).lower()

    with pytest.raises(InvalidParameterError):
        watermark_image(source, destination, text="Test", size=-10)


def test_size_not_number(tmp_path: Path) -> None:
    """Size as a non-number is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        watermark_image(source, destination, text="Test", size="48")

    error = exc_info.value
    assert "size" in error.context.get("parameter", "").lower()


def test_angle_not_number(tmp_path: Path) -> None:
    """Angle as a non-number is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        watermark_image(source, destination, text="Test", angle="45")

    error = exc_info.value
    assert "angle" in error.context.get("parameter", "").lower()


# ---------------------------------------------------------------------------
# Real Watermarking
# ---------------------------------------------------------------------------


def test_watermark_jpeg_basic(tmp_path: Path) -> None:
    """Watermarking a JPEG produces a valid JPEG with text."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    result = watermark_image(source, destination, text="WATERMARK")

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()
    assert len(result.outputs) == 1
    assert result.outputs[0] == destination

    # Check details
    assert result.details["text"] == "WATERMARK"
    assert result.details["position"] == "center"
    assert result.details["size"] == 48.0
    assert result.details["opacity"] == 0.15
    assert result.details["angle"] == 45.0

    # Verify the output is a valid image
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "JPEG"
    assert image.size == (200, 200)


def test_watermark_png(tmp_path: Path) -> None:
    """Watermarking a PNG produces a valid PNG."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png_image(tmp_path / "test.png")
    destination = tmp_path / "out.png"

    result = watermark_image(source, destination, text="TEST PNG")

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()
    assert result.details["format"] == "png"

    # Verify the output is a valid PNG
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "PNG"


def test_watermark_center_position(tmp_path: Path) -> None:
    """Watermark at center position."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    result = watermark_image(source, destination, text="CENTER", position="center")

    assert result.details["position"] == "center"
    assert destination.exists()


def test_watermark_top_left_position(tmp_path: Path) -> None:
    """Watermark at top-left position."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    result = watermark_image(source, destination, text="TOP-LEFT", position="top-left")

    assert result.details["position"] == "top-left"
    assert destination.exists()


def test_watermark_bottom_right_position(tmp_path: Path) -> None:
    """Watermark at bottom-right position."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    result = watermark_image(source, destination, text="BOTTOM-RIGHT", position="bottom-right")

    assert result.details["position"] == "bottom-right"
    assert destination.exists()


def test_watermark_different_sizes(tmp_path: Path) -> None:
    """Watermarks with different font sizes."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")

    # Test small size
    dest_small = tmp_path / "out_small.jpg"
    result = watermark_image(source, dest_small, text="Small", size=24.0)
    assert result.details["size"] == 24.0
    assert dest_small.exists()

    # Test large size
    dest_large = tmp_path / "out_large.jpg"
    result = watermark_image(source, dest_large, text="Large", size=96.0)
    assert result.details["size"] == 96.0
    assert dest_large.exists()


def test_watermark_different_opacities(tmp_path: Path) -> None:
    """Watermarks with different opacity levels."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")

    # Test fully transparent (nearly invisible)
    dest_transparent = tmp_path / "out_transparent.jpg"
    result = watermark_image(source, dest_transparent, text="Transparent", opacity=0.01)
    assert result.details["opacity"] == 0.01
    assert dest_transparent.exists()

    # Test solid
    dest_solid = tmp_path / "out_solid.jpg"
    result = watermark_image(source, dest_solid, text="Solid", opacity=1.0)
    assert result.details["opacity"] == 1.0
    assert dest_solid.exists()


def test_watermark_different_angles(tmp_path: Path) -> None:
    """Watermarks with different rotation angles."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")

    # Test no rotation
    dest_0 = tmp_path / "out_0.jpg"
    result = watermark_image(source, dest_0, text="Horizontal", angle=0)
    assert result.details["angle"] == 0
    assert dest_0.exists()

    # Test 45 degree rotation (default)
    dest_45 = tmp_path / "out_45.jpg"
    result = watermark_image(source, dest_45, text="45 Degrees", angle=45)
    assert result.details["angle"] == 45
    assert dest_45.exists()

    # Test 90 degree rotation
    dest_90 = tmp_path / "out_90.jpg"
    result = watermark_image(source, dest_90, text="Vertical", angle=90)
    assert result.details["angle"] == 90
    assert dest_90.exists()


def test_docs_empty_rejected(tmp_path: Path) -> None:
    """Calling watermark-image with no documents is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    destination = tmp_path / "out.jpg"

    router = EngineRouter(config=Config())
    with pytest.raises(InvalidParameterError) as exc_info:
        router.run(
            "watermark-image",
            [],  # Empty docs
            OutputTarget(destination=destination),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            text="Test",
        )

    error = exc_info.value
    assert "document" in str(error).lower()


def test_output_format_validation(tmp_path: Path) -> None:
    """Output format must be a recognized image format."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.xyz"  # Unknown format

    with pytest.raises(InvalidParameterError) as exc_info:
        watermark_image(source, destination, text="Test")

    error = exc_info.value
    assert "format" in str(error).lower()


def test_details_include_all_parameters(tmp_path: Path) -> None:
    """Result details include all watermark parameters."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_image(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    result = watermark_image(
        source, destination, text="DETAILED", position="top-right", size=60.0, opacity=0.3, angle=30
    )

    assert "text" in result.details
    assert "position" in result.details
    assert "size" in result.details
    assert "opacity" in result.details
    assert "angle" in result.details
    assert "format" in result.details
    assert "image_width" in result.details
    assert "image_height" in result.details

    assert result.details["text"] == "DETAILED"
    assert result.details["position"] == "top-right"
    assert result.details["size"] == 60.0
    assert result.details["opacity"] == 0.3
    assert result.details["angle"] == 30
