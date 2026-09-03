"""``resize`` — shrink or expand an image to new dimensions.

This tool is pure-Python (Pillow), so all tests run with real resizing
when Pillow is available. Tests verify:

* Spec shape and configuration
* Parameter validation (width/height positive, quality 1-95, fit choices)
* Pillow availability handling
* Actual resize for each fit mode (stretch, contain, cover/fill)
* Format preservation and conversion
* Output dimensions match expectations for each fit mode
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


def resize(source: Path, destination: Path, **extra: Any) -> ToolResult:
    """Helper to run resize through the router."""
    router = EngineRouter(config=Config())
    return router.run(
        "resize",
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
    spec = get_tool("resize")

    assert spec.name == "resize"
    assert spec.category == "image"
    assert spec.accepts_multiple_inputs is False
    assert spec.output_required is False
    assert Engine.LOCAL in spec.supported_engines
    assert Engine.CLOUD not in spec.supported_engines

    # Check params
    param_names = {p.name for p in spec.params}
    assert "width" in param_names
    assert "height" in param_names
    assert "scale" in param_names
    assert "fit" in param_names
    assert "quality" in param_names

    # Width and height are individually optional: `scale`, or either one on
    # its own, is enough to say how big. Requiring both was the usability
    # problem -- it asked for a pixel number the user often has to leave the
    # application to find. `local.py` still refuses a run that names no size
    # at all, which is the rule that actually matters.
    width_param = next(p for p in spec.params if p.name == "width")
    assert width_param.type_ == "int"
    assert width_param.required is False
    assert width_param.label == "width (px)"

    height_param = next(p for p in spec.params if p.name == "height")
    assert height_param.type_ == "int"
    assert height_param.required is False
    assert height_param.label == "height (px)"

    scale_param = next(p for p in spec.params if p.name == "scale")
    assert scale_param.type_ == "float"
    assert scale_param.required is False
    assert scale_param.label == "scale (%)"

    # scale is one way to say how big, width/height/fit is the other; a form
    # shows one and not the other, driven by this shared group.
    from docmax.tools.resize.tool import RESIZE_METHOD

    assert scale_param.group == RESIZE_METHOD
    assert scale_param.group_option == "Percentage"
    assert width_param.group == RESIZE_METHOD
    assert height_param.group == RESIZE_METHOD
    assert width_param.group_option == height_param.group_option == "Dimensions"

    # Declaring the four values is what makes the TUI render a dropdown
    # instead of a free-text box; the engine accepted exactly these already.
    fit_param = next(p for p in spec.params if p.name == "fit")
    assert fit_param.type_ == "str"
    assert fit_param.default == "cover"
    assert fit_param.group == RESIZE_METHOD
    assert fit_param.group_option == "Dimensions"

    # quality is not part of the either/or: it applies whichever way the
    # size was chosen.
    quality_param = next(p for p in spec.params if p.name == "quality")
    assert quality_param.group == ""
    assert fit_param.choices == ("cover", "contain", "fill", "stretch")

    # The form can state the image's real size before asking for a new one.
    assert spec.describe_inputs is not None

    # Check quality param
    quality_param = next(p for p in spec.params if p.name == "quality")
    assert quality_param.type_ == "int"
    assert quality_param.default == 80


def test_unavailable_when_pillow_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Pillow is missing, the tool reports unavailability."""
    import importlib

    from docmax.tools.resize import local

    # Mock find_spec to return None for PIL
    def mock_find_spec(name: str):  # type: ignore[no-untyped-def]
        if name == "PIL":
            return None
        return importlib.util.find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", mock_find_spec)

    # Reload the module to pick up the mocked find_spec
    importlib.reload(local)

    strategy = local.ResizeLocal()
    assert strategy.is_available() is False
    reason = strategy.unavailable_reason()
    assert reason is not None
    assert "Pillow" in reason
    assert "pip install" in reason


# ---------------------------------------------------------------------------
# Parameter Validation
# ---------------------------------------------------------------------------


def test_some_target_size_is_required(tmp_path: Path) -> None:
    """One of scale, width or height must be given -- but only one is needed.

    Replaces the pair of "width is required" / "height is required" tests:
    demanding both was the usability problem, since it asked a user for a
    pixel number they usually have to leave the application to find.
    """
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, destination)

    remedy = (exc_info.value.remedy or "").lower()
    assert "scale" in remedy
    assert "width" in remedy
    assert "height" in remedy


def test_scale_and_explicit_dimensions_are_contradictory(tmp_path: Path) -> None:
    """Two ways of saying the size at once is a question, not a default."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, tmp_path / "out.jpg", scale=50, width=100)

    assert "scale" in str(exc_info.value).lower()


def test_width_must_be_positive(tmp_path: Path) -> None:
    """Width must be a positive integer."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, destination, width=0, height=100)

    error = exc_info.value
    assert "width" in error.context.get("parameter", "").lower()
    assert "positive" in str(error).lower()


def test_height_must_be_positive(tmp_path: Path) -> None:
    """Height must be a positive integer."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, destination, width=100, height=-50)

    error = exc_info.value
    assert "height" in error.context.get("parameter", "").lower()
    assert "positive" in str(error).lower()


def test_width_not_integer(tmp_path: Path) -> None:
    """Width as a string is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, destination, width="100", height=100)

    error = exc_info.value
    assert "width" in error.context.get("parameter", "").lower()
    assert "integer" in str(error).lower()


def test_height_not_integer(tmp_path: Path) -> None:
    """Height as a string is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, destination, width=100, height="100")

    error = exc_info.value
    assert "height" in error.context.get("parameter", "").lower()
    assert "integer" in str(error).lower()


def test_fit_invalid_mode(tmp_path: Path) -> None:
    """Invalid fit mode is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, destination, width=100, height=100, fit="invalid")

    error = exc_info.value
    assert "fit" in error.context.get("parameter", "").lower()
    assert "invalid" in str(error).lower()


def test_quality_too_low(tmp_path: Path) -> None:
    """Quality below 1 is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.jpg"

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, destination, width=100, height=100, quality=0)

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
        resize(source, destination, width=100, height=100, quality=96)

    error = exc_info.value
    assert "quality" in error.context.get("parameter", "").lower()
    assert "1" in str(error)
    assert "95" in str(error)


# ---------------------------------------------------------------------------
# Real Resizing
# ---------------------------------------------------------------------------


def test_resize_stretch(tmp_path: Path) -> None:
    """Stretch fit mode ignores aspect ratio and stretches to exact dimensions."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg", size=(100, 100))
    destination = tmp_path / "out.jpg"

    result = resize(source, destination, width=200, height=150, fit="stretch")

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()
    assert len(result.outputs) == 1
    assert result.outputs[0] == destination

    # Check details
    assert result.details["fit_mode"] == "stretch"
    assert result.details["resized_size"] == (200, 150)

    # Verify the output is a valid JPEG with correct dimensions
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "JPEG"
    assert image.size == (200, 150)


def test_resize_contain(tmp_path: Path) -> None:
    """Contain fit mode maintains aspect ratio and adds padding."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg", size=(100, 100))
    destination = tmp_path / "out.jpg"

    result = resize(source, destination, width=200, height=150, fit="contain")

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()

    # Check details
    assert result.details["fit_mode"] == "contain"
    assert result.details["resized_size"] == (200, 150)

    # Verify the output is a valid JPEG
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "JPEG"
    assert image.size == (200, 150)


def test_resize_cover(tmp_path: Path) -> None:
    """Cover fit mode maintains aspect ratio and crops to fit."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg", size=(100, 100))
    destination = tmp_path / "out.jpg"

    result = resize(source, destination, width=200, height=150, fit="cover")

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()

    # Check details
    assert result.details["fit_mode"] == "cover"
    assert result.details["resized_size"] == (200, 150)

    # Verify the output is a valid JPEG
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "JPEG"
    assert image.size == (200, 150)


def test_resize_fill_alias_for_cover(tmp_path: Path) -> None:
    """Fill fit mode is an alias for cover."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg", size=(100, 100))
    destination = tmp_path / "out.jpg"

    result = resize(source, destination, width=200, height=150, fit="fill")

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()

    # Check details - fill should behave like cover
    assert result.details["fit_mode"] == "fill"
    assert result.details["resized_size"] == (200, 150)

    # Verify the output is a valid JPEG
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "JPEG"
    assert image.size == (200, 150)


def test_resize_png_ignores_quality(tmp_path: Path) -> None:
    """PNG resize ignores the quality parameter."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png(tmp_path / "test.png")
    destination = tmp_path / "out.png"

    # PNG should accept quality without error, but ignore it
    result = resize(source, destination, width=150, height=150, quality=10)

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()
    assert result.details["quality"] == 10  # Parameter is accepted

    # Verify the output is a valid PNG
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "PNG"
    assert image.size == (150, 150)


def test_docs_empty_rejected(tmp_path: Path) -> None:
    """Calling resize with no documents is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    destination = tmp_path / "out.jpg"

    router = EngineRouter(config=Config())
    with pytest.raises(InvalidParameterError) as exc_info:
        router.run(
            "resize",
            [],  # Empty docs
            OutputTarget(destination=destination),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            width=100,
            height=100,
        )

    error = exc_info.value
    assert "document" in str(error).lower()


def test_unknown_image_format_rejected(tmp_path: Path) -> None:
    """An unsupported image format is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    # Create a dummy JPEG and try to resize to an unknown extension
    source = tmp_path / "test.jpg"
    from PIL import Image

    Image.new("RGB", (50, 50)).save(source, format="JPEG", quality=95)

    destination = tmp_path / "out.xyz"  # Unknown format

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, destination, width=100, height=100)

    error = exc_info.value
    assert "format" in str(error).lower()


def test_details_include_all_info(tmp_path: Path) -> None:
    """Result details report input format, output format, original size, resized size, fit mode, and quality."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg", size=(100, 100))
    destination = tmp_path / "out.jpg"

    result = resize(source, destination, width=200, height=150, fit="contain", quality=75)

    assert "input_format" in result.details
    assert "output_format" in result.details
    assert "original_size" in result.details
    assert "resized_size" in result.details
    assert "fit_mode" in result.details
    assert "quality" in result.details

    # Check values
    assert result.details["input_format"] == "jpeg"
    assert result.details["output_format"] == "jpeg"
    assert result.details["original_size"] == (100, 100)
    assert result.details["resized_size"] == (200, 150)
    assert result.details["fit_mode"] == "contain"
    assert result.details["quality"] == 75


def test_resize_scales_down(tmp_path: Path) -> None:
    """Resizing can scale down an image."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg", size=(400, 300))
    destination = tmp_path / "out.jpg"

    resize(source, destination, width=200, height=150, fit="stretch")

    assert destination.exists()
    from PIL import Image

    image = Image.open(str(destination))
    assert image.size == (200, 150)


def test_resize_scales_up(tmp_path: Path) -> None:
    """Resizing can scale up an image."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg", size=(50, 50))
    destination = tmp_path / "out.jpg"

    resize(source, destination, width=200, height=200, fit="stretch")

    assert destination.exists()
    from PIL import Image

    image = Image.open(str(destination))
    assert image.size == (200, 200)


# ---------------------------------------------------------------------------
# Saying how big without knowing the pixels
# ---------------------------------------------------------------------------


def test_scale_halves_the_image(tmp_path: Path) -> None:
    """The whole point of scale: no knowledge of the source size needed."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_jpeg(tmp_path / "test.jpg", size=(1920, 1080))
    destination = tmp_path / "out.jpg"

    result = resize(source, destination, scale=50)

    assert Image.open(str(destination)).size == (960, 540)
    assert result.details["sizing"] == "scale"


def test_scale_can_enlarge(tmp_path: Path) -> None:
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_jpeg(tmp_path / "test.jpg", size=(100, 50))
    resize(source, tmp_path / "out.jpg", scale=200)

    assert Image.open(str(tmp_path / "out.jpg")).size == (200, 100)


def test_a_tiny_scale_still_produces_a_real_image(tmp_path: Path) -> None:
    """Rounding must never reach zero -- a 0-pixel file opens nowhere."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_jpeg(tmp_path / "test.jpg", size=(100, 50))
    resize(source, tmp_path / "out.jpg", scale=0.5)

    width, height = Image.open(str(tmp_path / "out.jpg")).size
    assert width >= 1
    assert height >= 1


def test_width_alone_computes_the_height(tmp_path: Path) -> None:
    """The user should not have to do the arithmetic to keep proportions."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_jpeg(tmp_path / "test.jpg", size=(1920, 1080))
    destination = tmp_path / "out.jpg"

    result = resize(source, destination, width=800)

    assert Image.open(str(destination)).size == (800, 450)
    assert result.details["sizing"] == "width"


def test_height_alone_computes_the_width(tmp_path: Path) -> None:
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_jpeg(tmp_path / "test.jpg", size=(1920, 1080))
    destination = tmp_path / "out.jpg"

    result = resize(source, destination, height=300)

    assert Image.open(str(destination)).size == (533, 300)
    assert result.details["sizing"] == "height"


def test_both_dimensions_still_set_an_exact_box(tmp_path: Path) -> None:
    """The original behaviour is untouched, and `fit` still decides."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_jpeg(tmp_path / "test.jpg", size=(1920, 1080))
    destination = tmp_path / "out.jpg"

    result = resize(source, destination, width=400, height=400, fit="contain")

    assert Image.open(str(destination)).size == (400, 400)
    assert result.details["sizing"] == "width+height"
    assert result.details["fit_mode"] == "contain"


def test_scale_must_be_positive(tmp_path: Path) -> None:
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, tmp_path / "out.jpg", scale=0)

    assert exc_info.value.context.get("parameter") == "scale"


def test_scale_must_be_a_number(tmp_path: Path) -> None:
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")

    with pytest.raises(InvalidParameterError) as exc_info:
        resize(source, tmp_path / "out.jpg", scale="half")

    assert exc_info.value.context.get("parameter") == "scale"


# ---------------------------------------------------------------------------
# What the form can tell the user
# ---------------------------------------------------------------------------


def test_the_spec_describes_the_input_dimensions(tmp_path: Path) -> None:
    """The fact the width and height fields are asking about.

    A user who cannot see this has to leave the application to answer "what
    width?", which is the usability problem the whole change exists to fix.
    """
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from docmax.tools.resize.tool import describe_inputs

    source = make_jpeg(tmp_path / "photo.jpg", size=(1920, 1080))
    described = describe_inputs([source])

    assert described is not None
    assert "1920" in described
    assert "1080" in described
    assert "pixels" in described
    assert "photo.jpg" in described


def test_describing_an_unreadable_input_says_nothing_rather_than_failing(
    tmp_path: Path,
) -> None:
    """A hint that raised would break a form over a file the user is mid-way
    through typing."""
    from docmax.tools.resize.tool import describe_inputs

    broken = tmp_path / "not-an-image.jpg"
    broken.write_bytes(b"not an image")

    assert describe_inputs([broken]) is None
    assert describe_inputs([tmp_path / "absent.jpg"]) is None
    assert describe_inputs([]) is None
