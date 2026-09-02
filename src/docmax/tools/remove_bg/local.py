"""The local engine for ``remove-bg``.

Background removal uses an ONNX model via rembg, which downloads the model to
``~/.u2net/`` on first use of a given model name — a network operation that
``is_available()`` cannot detect. Availability only checks whether the package
is importable, per the "cheap, no import, no network" contract every strategy
must honor. Network failures are caught and re-raised as typed errors so they
do not escape ``core``/``tools`` raw.

Every heavy import sits inside a method. The module itself costs nothing to
import, which matters because the registry reads it on every ``--help``.
"""

from __future__ import annotations

import importlib.util
import time
from typing import TYPE_CHECKING, Any

from docmax.core.branding import DIST_NAME
from docmax.core.errors import ExternalToolFailedError, InvalidParameterError
from docmax.core.models import Engine, ToolResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, MissingDependency, ProgressSink

#: Installed by the ``remove-bg`` extra.
DEPENDENCY = "rembg"


def is_available() -> bool:
    """Whether rembg is installed and importable."""
    if importlib.util.find_spec(DEPENDENCY) is None:
        return False
    # Double-check: actually try to import rembg to see if it initializes
    try:
        import rembg  # noqa: F401

        return True
    except (SystemExit, Exception):
        # If rembg fails to initialize, report unavailable
        return False


def unavailable_reason() -> str | None:
    """Why not, including how to fix it."""
    if is_available():
        return None
    return f'rembg is not installed. Install it with: pip install "{DIST_NAME}[remove-bg]"'


class RemoveBgLocal:
    """Strip the background from an image using an ONNX model via rembg."""

    def is_available(self) -> bool:
        return is_available()

    def unavailable_reason(self) -> str | None:
        return unavailable_reason()

    def missing_dependencies(self) -> tuple[MissingDependency, ...]:
        """Structured detail behind :meth:`unavailable_reason`, for a TUI dialog.

        The optional, duck-typed extra ``EngineStrategy`` documents —
        ``EngineRouter.missing_dependencies`` reads it with ``getattr``
        rather than requiring it, so this method turns "the local engine is
        unavailable" into "Dependency Required: rembg".
        """
        from docmax.core.protocols import MissingDependency

        if self.is_available():
            return ()
        return (
            MissingDependency(
                name="rembg",
                reason="Background removal requires the rembg package.",
                url="https://github.com/danielgatis/rembg",
            ),
        )

    def run(
        self,
        docs: Sequence[DocumentRef],
        target: OutputTarget,
        *,
        progress: ProgressSink,
        cancellation: CancellationToken,
        **params: Any,
    ) -> ToolResult:
        """Remove the background from an image and save as a transparent PNG."""
        if not docs:
            raise InvalidParameterError(
                "Remove-bg needs an image.",
                remedy="Pass an image file.",
            )

        if target.destination.suffix.lower() != ".png":
            raise InvalidParameterError(
                "Background removal produces PNG; use -o file.png instead.",
                remedy="Specify an output file with a .png extension.",
                context={"path": str(target.destination), "suffix": target.destination.suffix},
            )

        from PIL import Image, UnidentifiedImageError

        from docmax.core.atomic import atomic_write
        from docmax.core.errors import CorruptDocumentError
        from docmax.tools.remove_bg.validators import is_readable_png

        # Import rembg carefully: it calls sys.exit(1) on some errors,
        # which would crash the entire process. Catch that and re-raise as typed error.
        try:
            import rembg
        except SystemExit as exc:
            import traceback

            tb_str = traceback.format_exc()
            raise ExternalToolFailedError(
                "rembg initialization failed during import. This usually means onnxruntime or a model is missing or incompatible.",
                remedy="Try: pip install --upgrade --force-reinstall onnxruntime rembg pillow numpy scipy",
                context={"exit_code": exc.code, "traceback": tb_str[-500:]},
            ) from exc
        except Exception as exc:
            raise ExternalToolFailedError(
                f"rembg failed to import: {exc}",
                remedy="Check that onnxruntime and rembg are properly installed: pip install --upgrade onnxruntime rembg",
                context={"error": str(exc)},
            ) from exc

        started = time.monotonic()

        # Validate cancellation before starting work.
        cancellation.raise_if_cancelled(operation="remove-bg")

        # Open and validate the input image first.
        progress.start("Loading image", total=None)
        try:
            image = Image.open(str(docs[0].path)).convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise CorruptDocumentError(
                f"Could not open {docs[0].path.name} as an image: {exc}",
                remedy="Check the file is a valid image.",
                context={"path": str(docs[0].path)},
            ) from exc

        # Now perform background removal with rembg.
        # This is where model downloads happen and network can fail.
        try:
            model_name = params.get("model", "u2net")
            progress.start(f"Preparing model '{model_name}' (downloading on first use)", total=None)
            session = rembg.new_session(model_name=model_name)
            progress.start(f"Removing background from {docs[0].path.name}", total=None)
            output = rembg.remove(image, session=session)
        except Exception as exc:
            # Wrap rembg failures (including network/download) as a typed error.
            # rembg may raise urllib/requests errors on model download failure.
            raise ExternalToolFailedError(
                "Background removal model download failed. Use --offline to disable cloud features, or pre-download the model.",
                remedy="Check your network connection, or download the model manually.",
                context={"model": params.get("model", "u2net"), "error": str(exc)},
            ) from exc

        # Write through atomic_write to ensure consistency.
        with atomic_write(target, validators=(is_readable_png,)) as handle:
            output.save(handle, format="PNG")

        progress.advance()

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(),
            details={
                "model": params.get("model", "u2net"),
                "output_size": output.size,
            },
        )


def _version() -> str:
    """Get the rembg version, or 'unknown' if it cannot be determined."""
    try:
        from importlib.metadata import version

        return f"rembg/{version('rembg')}"
    except Exception:
        return "rembg/unknown"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return RemoveBgLocal()


__all__ = ["RemoveBgLocal", "build", "is_available", "unavailable_reason"]
