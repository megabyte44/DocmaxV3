"""``convert-image`` — convert an image from one format to another.

This tool is pure-Python (Pillow), so all tests run with a real conversion
when Pillow is available. Tests verify:

* Spec shape and configuration
* Format conversion (input format can differ from output format)
* Pillow availability handling
* Real conversions work correctly for all format pairs
* Transparency handling (JPEG flattens to white, PNG preserves alpha)
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

    # Create a simple solid color image
    image = Image.new("RGB", size, color="red")
    image.save(path, format="JPEG", quality=95)
    return path


def make_png(path: Path, size: tuple[int, int] = (100, 100), with_alpha: bool = False) -> Path:
    """Create a minimal PNG for testing."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Create a simple solid color PNG
    if with_alpha:
        image = Image.new("RGBA", size, color=(0, 0, 255, 128))
    else:
        image = Image.new("RGB", size, color="blue")
    image.save(path, format="PNG")
    return path


def make_gif(path: Path, size: tuple[int, int] = (100, 100)) -> Path:
    """Create a minimal GIF for testing."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Create a simple solid color GIF
    image = Image.new("P", size, color=0)
    image.save(path, format="GIF")
    return path


def make_tiff(path: Path, size: tuple[int, int] = (100, 100)) -> Path:
    """Create a minimal TIFF for testing."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Create a simple solid color TIFF
    image = Image.new("RGB", size, color="green")
    image.save(path, format="TIFF")
    return path


def make_bmp(path: Path, size: tuple[int, int] = (100, 100)) -> Path:
    """Create a minimal BMP for testing."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Create a simple solid color BMP
    image = Image.new("RGB", size, color="yellow")
    image.save(path, format="BMP")
    return path


def convert_image(source: Path, destination: Path, **extra: Any) -> ToolResult:
    """Helper to run convert-image through the router."""
    router = EngineRouter(config=Config())
    return router.run(
        "convert-image",
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
    spec = get_tool("convert-image")

    assert spec.name == "convert-image"
    assert spec.category == "image"
    assert spec.accepts_multiple_inputs is False
    # `output_required` is pinned to `convert` and `metadata` alone
    # (test_registry.py); the CLI's `-o` is already mandatory without it.
    assert spec.output_required is False
    assert Engine.LOCAL in spec.supported_engines
    assert Engine.CLOUD not in spec.supported_engines

    # One parameter: `to`. No `quality` -- that is compress-image's job.
    assert [p.name for p in spec.params] == ["to"]

    to_param = spec.params[0]
    assert to_param.type_ == "str"
    # Optional, unlike `convert`'s own required `--to`: an image extension
    # names its format reliably, so `-o out.png` alone is enough.
    assert to_param.required is False
    assert to_param.default is None
    # Read from the shared table rather than retyped. ADR 0010.
    assert to_param.choices == ("png", "jpeg", "tiff", "bmp", "gif")
    # Presentational only, for the TUI: "to" reads correctly as a flag, where
    # the verb is in the command, but names nothing above a dropdown. The wire
    # name is unchanged, which is what keeps the CLI and MCP schema out of it.
    assert to_param.label == "output format"
    assert to_param.name == "to"


def test_unavailable_when_pillow_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Pillow is missing, the tool reports unavailability."""
    import importlib

    from docmax.tools.convert_image import local

    # Mock find_spec to return None for PIL
    def mock_find_spec(name: str):  # type: ignore[no-untyped-def]
        if name == "PIL":
            return None
        return importlib.util.find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", mock_find_spec)

    # Reload the module to pick up the mocked find_spec
    importlib.reload(local)

    strategy = local.ConvertImageLocal()
    assert strategy.is_available() is False
    reason = strategy.unavailable_reason()
    assert reason is not None
    assert "Pillow" in reason
    assert "pip install" in reason


# ---------------------------------------------------------------------------
# Format Conversions
# ---------------------------------------------------------------------------


def test_convert_jpeg_to_png(tmp_path: Path) -> None:
    """Converting JPEG to PNG works."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.png"

    result = convert_image(source, destination)

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()
    assert len(result.outputs) == 1
    assert result.outputs[0] == destination

    # Check details
    assert result.details["input_format"] == "jpeg"
    assert result.details["output_format"] == "png"

    # Verify the output is a valid PNG
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "PNG"


def test_convert_png_to_jpeg(tmp_path: Path) -> None:
    """Converting PNG to JPEG works."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png(tmp_path / "test.png")
    destination = tmp_path / "out.jpg"

    result = convert_image(source, destination)

    assert result.engine_used == Engine.LOCAL
    assert destination.exists()
    assert result.details["input_format"] == "png"
    assert result.details["output_format"] == "jpeg"

    # Verify the output is a valid JPEG
    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "JPEG"


def test_convert_jpeg_to_tiff(tmp_path: Path) -> None:
    """Converting JPEG to TIFF works."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.tiff"

    result = convert_image(source, destination)

    assert result.details["input_format"] == "jpeg"
    assert result.details["output_format"] == "tiff"
    assert destination.exists()

    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "TIFF"


def test_convert_png_to_gif(tmp_path: Path) -> None:
    """Converting PNG to GIF works."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png(tmp_path / "test.png")
    destination = tmp_path / "out.gif"

    result = convert_image(source, destination)

    assert result.details["input_format"] == "png"
    assert result.details["output_format"] == "gif"
    assert destination.exists()

    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "GIF"


def test_convert_gif_to_jpeg(tmp_path: Path) -> None:
    """Converting GIF to JPEG works."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_gif(tmp_path / "test.gif")
    destination = tmp_path / "out.jpg"

    result = convert_image(source, destination)

    assert result.details["input_format"] == "gif"
    assert result.details["output_format"] == "jpeg"
    assert destination.exists()

    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "JPEG"


def test_convert_tiff_to_png(tmp_path: Path) -> None:
    """Converting TIFF to PNG works."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_tiff(tmp_path / "test.tiff")
    destination = tmp_path / "out.png"

    result = convert_image(source, destination)

    assert result.details["input_format"] == "tiff"
    assert result.details["output_format"] == "png"
    assert destination.exists()

    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "PNG"


def test_convert_bmp_to_png(tmp_path: Path) -> None:
    """Converting BMP to PNG works."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_bmp(tmp_path / "test.bmp")
    destination = tmp_path / "out.png"

    result = convert_image(source, destination)

    assert result.details["input_format"] == "bmp"
    assert result.details["output_format"] == "png"
    assert destination.exists()

    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "PNG"


# ---------------------------------------------------------------------------
# Transparency Handling
# ---------------------------------------------------------------------------


def test_png_with_alpha_to_jpeg_flattens_to_white(tmp_path: Path) -> None:
    """Converting PNG with alpha to JPEG flattens transparency to white background."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png(tmp_path / "test.png", with_alpha=True)
    destination = tmp_path / "out.jpg"

    result = convert_image(source, destination)

    assert result.details["input_format"] == "png"
    assert result.details["output_format"] == "jpeg"
    assert destination.exists()

    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "JPEG"
    # JPEG should not have alpha channel
    assert image.mode == "RGB"


def test_jpeg_to_png_allows_alpha(tmp_path: Path) -> None:
    """Converting JPEG to PNG produces a file that supports alpha."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.png"

    result = convert_image(source, destination)

    assert result.details["input_format"] == "jpeg"
    assert result.details["output_format"] == "png"

    from PIL import Image

    image = Image.open(str(destination))
    assert image.format == "PNG"
    # PNG can have alpha channel
    assert image.mode in ("RGB", "RGBA", "P")


# ---------------------------------------------------------------------------
# Error Cases
# ---------------------------------------------------------------------------


def test_docs_empty_rejected(tmp_path: Path) -> None:
    """Calling convert-image with no documents is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    destination = tmp_path / "out.jpg"

    router = EngineRouter(config=Config())
    with pytest.raises(InvalidParameterError) as exc_info:
        router.run(
            "convert-image",
            [],  # Empty docs
            OutputTarget(destination=destination),
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
        )

    error = exc_info.value
    assert "document" in str(error).lower()


def test_unknown_input_format_rejected(tmp_path: Path) -> None:
    """An unsupported input format is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    # Create a dummy JPEG and rename to unknown extension
    source = tmp_path / "test.xyz"
    image = Image.new("RGB", (50, 50))
    image.save(tmp_path / "test.jpg", format="JPEG")
    (tmp_path / "test.jpg").rename(source)

    destination = tmp_path / "out.png"

    with pytest.raises(InvalidParameterError) as exc_info:
        convert_image(source, destination)

    error = exc_info.value
    assert "format" in str(error).lower()


def test_unknown_output_format_rejected(tmp_path: Path) -> None:
    """An unsupported output format is rejected."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.xyz"  # Unknown format

    with pytest.raises(InvalidParameterError) as exc_info:
        convert_image(source, destination)

    error = exc_info.value
    assert "format" in str(error).lower()


# ---------------------------------------------------------------------------
# Details and Results
# ---------------------------------------------------------------------------


def test_result_includes_conversion_details(tmp_path: Path) -> None:
    """Result details report conversion parameters and formats."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "test.jpg")
    destination = tmp_path / "out.png"

    result = convert_image(source, destination)

    assert "input_format" in result.details
    assert "output_format" in result.details
    assert result.details["input_format"] == "jpeg"
    assert result.details["output_format"] == "png"


# ---------------------------------------------------------------------------
# The `to` parameter
# ---------------------------------------------------------------------------


def test_to_names_the_output_format(tmp_path: Path) -> None:
    """`--to` matching the destination extension converts as asked."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_png(tmp_path / "in.png")
    destination = tmp_path / "out.jpg"

    result = convert_image(source, destination, to="jpeg")

    assert destination.exists()
    assert Image.open(str(destination)).format == "JPEG"
    assert result.details["output_format"] == "jpeg"
    assert result.details["requested_format"] == "jpeg"


@pytest.mark.parametrize(
    ("name", "suffix", "pillow_format"),
    [
        ("png", ".png", "PNG"),
        ("jpeg", ".jpg", "JPEG"),
        ("tiff", ".tiff", "TIFF"),
        ("bmp", ".bmp", "BMP"),
        ("gif", ".gif", "GIF"),
    ],
)
def test_every_declared_format_is_accepted_by_to(
    name: str, suffix: str, pillow_format: str, tmp_path: Path
) -> None:
    """The choices the spec advertises are the choices the engine honours.

    The spec reads its vocabulary from `_formats`; without this, a name could
    be offered in `--help` and refused at run time.
    """
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_png(tmp_path / "in.png")
    destination = tmp_path / f"out{suffix}"

    convert_image(source, destination, to=name)

    assert Image.open(str(destination)).format == pillow_format


def test_to_is_optional_and_the_extension_decides(tmp_path: Path) -> None:
    """Omitting `--to` leaves the destination extension in charge."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_png(tmp_path / "in.png")
    destination = tmp_path / "out.bmp"

    result = convert_image(source, destination)

    assert Image.open(str(destination)).format == "BMP"
    assert result.details["output_format"] == "bmp"
    assert result.details["requested_format"] is None


def test_to_wins_over_the_destination_extension(tmp_path: Path) -> None:
    """An explicit `--to` decides the format, whatever the filename says.

    Asserted at the strategy, reached here with a deliberately contradictory
    target -- the router would normally have corrected it first (see the
    destination tests below). `--to png` produces PNG either way: the
    parameter is the authority, not the extension.
    """
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    source = make_jpeg(tmp_path / "in.jpg")
    destination = tmp_path / "out.jpg"

    result = convert_image(source, destination, to="png")

    assert Image.open(str(destination)).format == "PNG"
    assert result.details["output_format"] == "png"


def test_an_unknown_to_lists_what_this_tool_accepts(tmp_path: Path) -> None:
    """The remedy names convert-image's five formats, not to-images' three."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png(tmp_path / "in.png")
    destination = tmp_path / "out.png"

    with pytest.raises(InvalidParameterError) as caught:
        convert_image(source, destination, to="webp")

    remedy = caught.value.remedy or ""
    for name in ("png", "jpeg", "tiff", "bmp", "gif"):
        assert name in remedy


def test_an_empty_to_is_refused(tmp_path: Path) -> None:
    """An empty string is not the same as not supplying the option."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png(tmp_path / "in.png")
    destination = tmp_path / "out.png"

    with pytest.raises(InvalidParameterError) as caught:
        convert_image(source, destination, to="   ")

    assert caught.value.context.get("parameter") == "to"


# ---------------------------------------------------------------------------
# The destination follows the chosen format
# ---------------------------------------------------------------------------


def _target(tmp_path: Path, source: Path, requested: str | None, **params: Any) -> OutputTarget:
    """Resolve a destination the way every interface does, through the router."""
    from docmax.core.models import DocumentRef
    from docmax.core.router import EngineRouter

    return EngineRouter(config=Config()).target_for(
        "convert-image",
        [DocumentRef.from_path(source)],
        requested=requested,
        params=params,
    )


def test_an_explicit_format_corrects_the_destination_extension(tmp_path: Path) -> None:
    """The reported bug: `--to png` with `-o convert.jpg` was refused.

    It now writes `convert.png`. Saying the format once is enough, and the
    file that results is not named after a format it does not contain.
    """
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from PIL import Image

    from docmax.core.models import DocumentRef
    from docmax.core.router import EngineRouter

    source = make_jpeg(tmp_path / "image.jpg")
    target = _target(tmp_path, source, str(tmp_path / "convert.jpg"), to="png")

    assert target.destination == tmp_path / "convert.png"

    result = EngineRouter(config=Config()).run(
        "convert-image",
        [DocumentRef.from_path(source)],
        target,
        progress=NULL_PROGRESS,
        cancellation=NEVER_CANCELLED,
        to="png",
    )

    assert result.outputs == (tmp_path / "convert.png",)
    assert Image.open(str(tmp_path / "convert.png")).format == "PNG"
    # The path the user typed is not left behind as a decoy.
    assert not (tmp_path / "convert.jpg").exists()


def test_a_matching_extension_is_left_alone(tmp_path: Path) -> None:
    """Correction applies only when the two actually differ."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "image.jpg")
    target = _target(tmp_path, source, str(tmp_path / "convert.png"), to="png")

    assert target.destination == tmp_path / "convert.png"


def test_an_alternate_spelling_of_the_same_format_is_left_alone(tmp_path: Path) -> None:
    """`.jpeg` and `.jpg` are one format, so neither is rewritten into the other."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png(tmp_path / "image.png")
    target = _target(tmp_path, source, str(tmp_path / "convert.jpeg"), to="jpeg")

    assert target.destination == tmp_path / "convert.jpeg"


def test_the_extension_is_untouched_without_an_explicit_format(tmp_path: Path) -> None:
    """No `--to` means the extension still decides, exactly as before."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_png(tmp_path / "image.png")
    target = _target(tmp_path, source, str(tmp_path / "convert.bmp"))

    assert target.destination == tmp_path / "convert.bmp"


def test_a_derived_destination_uses_the_chosen_format(tmp_path: Path) -> None:
    """With no `-o` at all, the derived name carries the chosen format."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    source = make_jpeg(tmp_path / "image.jpg")
    target = _target(tmp_path, source, None, to="tiff")

    # `.tif` is the canonical spelling in `_formats`, and the tool reads
    # the suffix from there rather than echoing the format name.
    assert target.destination == tmp_path / "image.tif"


def test_the_correction_still_refuses_to_overwrite_an_input(tmp_path: Path) -> None:
    """The corrected path is what gets checked, not the typed one.

    Correcting after the in-place check would let `--to png` land on top of
    the source image with no error at all.
    """
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from docmax.core.errors import InPlaceOverwriteError

    source = make_png(tmp_path / "image.png")

    with pytest.raises(InPlaceOverwriteError):
        _target(tmp_path, source, str(tmp_path / "image.jpg"), to="png")


def test_the_correction_still_refuses_an_existing_file_without_force(tmp_path: Path) -> None:
    """Likewise for the already-exists check."""
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from docmax.core.errors import OutputExistsError

    source = make_jpeg(tmp_path / "image.jpg")
    make_png(tmp_path / "convert.png")

    with pytest.raises(OutputExistsError):
        _target(tmp_path, source, str(tmp_path / "convert.jpg"), to="png")


def test_an_unknown_format_leaves_the_path_alone_and_is_refused_by_the_tool(
    tmp_path: Path,
) -> None:
    """Path resolution is not where a bad format name is reported.

    The hook returns None, so the destination is untouched, and the run then
    fails with the typed error and the full vocabulary -- the same message
    whatever the destination happened to be.
    """
    if not PILLOW_AVAILABLE:
        pytest.skip("Pillow is not installed")

    from docmax.core.models import DocumentRef
    from docmax.core.router import EngineRouter

    source = make_jpeg(tmp_path / "image.jpg")
    target = _target(tmp_path, source, str(tmp_path / "convert.png"), to="webp")
    assert target.destination == tmp_path / "convert.png"

    with pytest.raises(InvalidParameterError) as caught:
        EngineRouter(config=Config()).run(
            "convert-image",
            [DocumentRef.from_path(source)],
            target,
            progress=NULL_PROGRESS,
            cancellation=NEVER_CANCELLED,
            to="webp",
        )
    assert "webp" in str(caught.value)
