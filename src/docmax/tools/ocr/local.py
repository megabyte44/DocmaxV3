"""The local engine for ``ocr``.

Rasterise, deskew, recognise, reassemble. Four steps, one page at a time.

Three things have to be present for this to run: the Tesseract binary, a
language pack, and Poppler for rasterisation — plus OpenCV if pages are to be
straightened. Any of them can be missing on its own, so availability reports
*which*: a router that can only say "unavailable" cannot tell the user what to
install, and cannot explain why it is offering the cloud engine instead.

## One process per page

Tesseract can take a list file and emit a whole multi-page PDF in one
invocation. This does not, for the reason ``to-images`` gives for the same
choice: a progress bar that advances per page, cancellation that takes effect
within one page rather than at the end of a five-hundred-page scan, and the
ability to leave individual pages alone. The cost is process launches, which
next to OCR itself is noise.

## Pages that already carry text are left exactly as they were

Not skipped as an optimisation — skipped because recognising them would be
wrong. Tesseract's PDF output *replaces* a page with its rasterisation plus an
invisible text layer, so re-recognising a page that already has real text throws
away the real text and doubles the layer, and a copy-paste then returns
everything twice. A mixed document — a scanned contract behind a generated cover
page — is the common case, not the exotic one.

The consequence is worth stating plainly, because it is a real cost: **a page
this tool does recognise comes back as an image at ``--dpi``.** That is what
OCR of a scan means; the page was already an image. A page that was already text
is copied through untouched, which is why the skip matters.

## Nothing is ever written beside the source

Every intermediate image lives in a ``TemporaryDirectory`` that is removed on
the way out, including after a failure and after a cancellation. v2 wrote a
``_preprocessed.png`` next to every document it touched, which its own
folder-watch mode then consumed as new input.

Every heavy import sits inside a method. The module itself costs nothing to
import, which matters because the registry reads it on every ``--help``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docmax.core.branding import CLI_NAME
from docmax.core.errors import InvalidParameterError
from docmax.core.models import Engine, ToolResult
from docmax.tools import _binaries, _deskew, _dpi
from docmax.tools._pdf import open_pdf, page_count, save
from docmax.tools.ocr.validators import checks_for, has_text

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from docmax.core.cancellation import CancellationToken
    from docmax.core.models import DocumentRef, OutputTarget
    from docmax.core.protocols import EngineStrategy, MissingDependency, ProgressSink

#: The recogniser, and the rasteriser. Both are declared in ``_binaries`` with
#: their per-platform install lines, and ``doctor`` has reported on both since M0.
TESSERACT = "tesseract"
PDFTOPPM = "pdftoppm"
BINARIES = (TESSERACT, PDFTOPPM)

#: OpenCV, and only for ``--deskew``. It is the whole of the ``ocr`` extra:
#: ADR 0022 records why ``pytesseract`` and ``pdf2image`` are not used, and what
#: that buys.
PYTHON_DEPENDENCIES = ("cv2",)

#: PNG rather than JPEG: recognition on a lossy image is worse for no saving
#: that survives the page being re-encoded into the output anyway.
_RASTER_FLAG = "-png"
_RASTER_SUFFIX = ".png"

#: Tesseract appends this to the output root it is given.
_PDF_SUFFIX = ".pdf"

#: Names the scratch directory so an operator looking at a full disk can tell
#: whose temp files they are. Built from ``CLI_NAME`` because
#: ``core/branding.py`` is the only module allowed to spell the brand out;
#: ``tests/hygiene/test_branding.py`` enforces it.
_SCRATCH_PREFIX = f"{CLI_NAME}-ocr-"


def missing_dependencies() -> tuple[str, ...]:
    """Everything the engine itself needs and cannot find, in one list.

    Binaries only. OpenCV is deliberately excluded: it is needed only for
    ``--deskew``, so it must not gate whether the engine is *available* — that
    would make ``ocr --no-deskew`` unavailable on a machine without it,
    contradicting the promise ADR 0022 makes. ``run()`` checks ``cv2`` itself,
    only when ``deskew`` is actually requested, and raises ``_opencv_missing()``
    with its own remedy.

    Kept module-level and public so the router can quote it when it builds the
    ``LocalDependencyMissingError`` that justifies the cloud fallback.
    """
    return tuple(name for name in BINARIES if _binaries.find(name) is None)


def install_hint() -> str:
    """What to type to get the engine itself running, per platform.

    Built from ``_binaries``' own declarations rather than written out, so the
    command here and the one ``doctor`` prints cannot disagree. It used to name
    ``setup --ocr``, a command that does not exist — see ADR 0022.

    Says nothing about OpenCV: that is a ``--deskew``-only dependency, and
    ``_opencv_missing()`` gives its own remedy at the point ``--deskew`` is
    actually requested and found absent.
    """
    # One clause per missing thing, semicolon-separated. Two binaries missing
    # used to render as "Install it with: X Install it with: Y", which reads as
    # one broken sentence rather than two commands to type.
    steps = [
        _binaries.describe(name).install_hint().removeprefix("Install it with: ")
        for name in BINARIES
        if _binaries.find(name) is None
    ]

    if not steps:  # pragma: no cover — nothing is missing
        return f"Or run `{CLI_NAME} ocr --engine cloud` to skip the install entirely."
    return (
        f"Install: {'; '.join(steps)}. "
        f"Or run `{CLI_NAME} ocr --engine cloud` to skip the install entirely."
    )


class OcrLocal:
    """Rasterise, deskew, recognise, and rebuild with a text layer."""

    def is_available(self) -> bool:
        return not missing_dependencies()

    def unavailable_reason(self) -> str | None:
        missing = missing_dependencies()
        if not missing:
            return None
        return f"Not installed: {', '.join(missing)}. {install_hint()}"

    def missing_dependencies(self) -> tuple[MissingDependency, ...]:
        """Structured detail behind :meth:`unavailable_reason`, for a TUI dialog.

        The optional, duck-typed extra ``EngineStrategy`` documents —
        ``EngineRouter.missing_dependencies`` reads it with ``getattr``
        rather than requiring it, so this is the one method that turns "the
        local engine is unavailable" into "Dependency Required: Tesseract".

        Binaries only, for the same reason :func:`is_available` excludes
        OpenCV: a ``--deskew``-only Python dependency must not appear as a
        reason the whole engine cannot run.
        """
        from docmax.core.protocols import MissingDependency

        return tuple(
            MissingDependency(
                name=_binaries.describe(name).name,
                reason=f"OCR cannot run because {_binaries.describe(name).name} is not installed.",
                url=_binaries.describe(name).homepage or None,
            )
            # `missing_dependencies()` — the module function above — is what
            # `is_available` itself calls; reusing it here is what keeps this
            # method and that check unable to disagree.
            for name in missing_dependencies()
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
        """OCR ``docs[0]`` into ``target``."""
        import tempfile
        import time
        from pathlib import Path

        from pypdf import PdfReader, PdfWriter

        if not docs:
            raise InvalidParameterError(
                "OCR needs a document.",
                remedy="Pass the scanned PDF to recognise.",
            )

        # Parameters first, so a typo is reported as a typo rather than as a
        # missing dependency ten seconds later.
        lang = _language(params)
        dpi = _dpi.parse(params.get("dpi"), default=300)
        deskew = _deskew_flag(params)

        document = docs[0]
        reader = open_pdf(document)
        total = page_count(reader)

        tesseract = _binaries.require(TESSERACT, tool="ocr")
        pdftoppm = _binaries.require(PDFTOPPM, tool="ocr")
        if deskew and not _deskew.is_available():
            raise _opencv_missing()
        _require_languages(tesseract, lang, cancellation)

        # Decided once, before any work: which pages already carry real text and
        # must therefore be copied through rather than recognised.
        already_text = {index for index in range(total) if has_text(reader.pages[index])}
        pending = total - len(already_text)

        started = time.monotonic()
        progress.start(f"Recognising {pending} of {total} page(s)", total=total)

        recognised: list[int] = []
        failed: list[int] = []
        straightened: list[int] = []
        produced: dict[int, Path] = {}

        with tempfile.TemporaryDirectory(prefix=_SCRATCH_PREFIX) as scratch:
            workspace = Path(scratch)

            for index in range(total):
                # Between pages: nothing is at the destination yet, and the
                # staged file is discarded wholesale on the way out.
                cancellation.raise_if_cancelled(operation="ocr")

                if index in already_text:
                    progress.advance()
                    continue

                page_number = index + 1
                page_pdf = self._recognise_page(
                    document.path,
                    page_number,
                    workspace=workspace,
                    tesseract=tesseract,
                    pdftoppm=pdftoppm,
                    lang=lang,
                    dpi=dpi,
                    deskew=deskew,
                    cancellation=cancellation,
                    straightened=straightened,
                )
                if page_pdf is None:
                    failed.append(page_number)
                else:
                    produced[index] = page_pdf
                    recognised.append(page_number)
                progress.advance()

            if pending and not recognised:
                # Every page that needed recognising failed. Writing a file that
                # is byte-for-byte the input, under a name that promises a text
                # layer, would be the worst of the available outcomes.
                raise _every_page_failed(failed)

            writer = PdfWriter()
            for index in range(total):
                page_pdf = produced.get(index)
                if page_pdf is None:
                    writer.add_page(reader.pages[index])
                else:
                    writer.add_page(PdfReader(str(page_pdf)).pages[0])

            # Inside the temporary directory's lifetime: the staged pages are
            # still being read from it while the output is written.
            save(writer, target, validators=checks_for(total))

        return ToolResult(
            outputs=(target.destination,),
            engine_used=Engine.LOCAL,
            duration_ms=int((time.monotonic() - started) * 1000),
            engine_version=_version(tesseract, cancellation),
            details={
                "pages": total,
                "recognised": len(recognised),
                # Named, not just counted. A user who expected a scanned
                # document and sees `skipped_with_text: 12` has learned
                # something true about their file.
                "skipped_with_text": sorted(index + 1 for index in already_text),
                "failed": failed,
                "deskewed": straightened,
                "lang": lang,
                "dpi": dpi,
            },
        )

    def _recognise_page(
        self,
        source: Path,
        page_number: int,
        *,
        workspace: Path,
        tesseract: str,
        pdftoppm: str,
        lang: str,
        dpi: int,
        deskew: bool,
        cancellation: CancellationToken,
        straightened: list[int],
    ) -> Path | None:
        """One page: rasterise, straighten, recognise. ``None`` if it failed.

        A page that fails is reported and skipped rather than taken as a reason
        to lose the other four hundred — the same judgement ``crop`` makes about
        a page its box does not fit. The run as a whole still fails if *every*
        page failed, which is the case that means something is actually wrong.
        """
        from docmax.core.errors import ExternalToolFailedError

        image_root = workspace / f"page-{page_number:05d}"
        text_root = workspace / f"ocr-{page_number:05d}"

        # `-singlefile` writes exactly `<root>.png`, with no page number of its
        # own — whose zero-padding width has varied between Poppler releases.
        _binaries.run(
            [
                pdftoppm,
                _RASTER_FLAG,
                "-r",
                str(dpi),
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-singlefile",
                str(source),
                str(image_root),
            ],
            tool="ocr",
            cancellation=cancellation,
        )

        image = image_root.with_suffix(_RASTER_SUFFIX)
        if not image.is_file():
            # Poppler exited zero without drawing anything. Rare, and precisely
            # the class of failure `compress` guards against for Ghostscript.
            return None

        if deskew and _deskew.straighten(image) != 0.0:
            straightened.append(page_number)

        try:
            _binaries.run(
                [tesseract, str(image), str(text_root), "-l", lang, "pdf"],
                tool="ocr",
                cancellation=cancellation,
            )
        except ExternalToolFailedError:
            # One page Tesseract could not read. Not the run's problem unless
            # every page does it; a timeout or a cancellation is a different
            # kind of event and is deliberately not caught here.
            return None

        recognised = text_root.with_suffix(_PDF_SUFFIX)
        return recognised if recognised.is_file() else None


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def _language(params: dict[str, Any]) -> str:
    """The language codes to pass through, or a typed error naming the problem.

    Tesseract's own ``+`` syntax, unchanged from v2 —
    ``docs/migrating-from-v2.md`` promises ``--lang eng+hin`` still works.
    Validated for *shape* here; whether the packs are installed is
    :func:`_require_languages`, which can only be answered by asking Tesseract.
    """
    value = params.get("lang", "eng")
    if value is None:
        return "eng"
    if not isinstance(value, str) or not value.strip():
        raise InvalidParameterError(
            f"lang must be a language code, not {value!r}.",
            remedy="Use a Tesseract code such as eng, deu, or eng+hin for two.",
            context={"parameter": "lang"},
        )

    codes = [code.strip() for code in value.split("+")]
    if not all(codes) or not all(code.replace("_", "").isalnum() for code in codes):
        raise InvalidParameterError(
            f"{value!r} is not a valid language selection.",
            remedy="Use a Tesseract code such as eng, deu, or eng+hin for two.",
            context={"parameter": "lang"},
        )
    return "+".join(codes)


def _deskew_flag(params: dict[str, Any]) -> bool:
    value = params.get("deskew", True)
    if value is None:
        return True
    if not isinstance(value, bool):
        raise InvalidParameterError(
            f"deskew must be true or false, not {value!r}.",
            remedy="Pass --deskew or --no-deskew.",
            context={"parameter": "deskew"},
        )
    return value


def _require_languages(
    tesseract: str,
    lang: str,
    cancellation: CancellationToken,
) -> None:
    """Refuse a language pack that is not installed, and name the ones that are.

    Best-effort by design: if the probe itself fails, this says nothing and lets
    the recognition report the problem. A version of Tesseract that cannot list
    its languages is not a reason to refuse to try.

    Worth the extra process. "Failed with exit 1" for a missing German pack
    sends a user to the wrong place entirely; "deu is not installed — available:
    eng, osd" sends them to their package manager.
    """
    from docmax.core.errors import DocMaxError

    try:
        completed = _binaries.run(
            [tesseract, "--list-langs"], tool="ocr", cancellation=cancellation
        )
    except DocMaxError:
        return

    # Tesseract prints the header and the list to stderr on some builds and
    # stdout on others, so both are read.
    raw = (completed.stdout or b"") + b"\n" + (completed.stderr or b"")
    installed = {
        line.strip()
        for line in raw.decode("utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().endswith(":")
    }
    if not installed:
        return

    wanted = [code for code in lang.split("+") if code not in installed]
    if not wanted:
        return

    raise InvalidParameterError(
        f"Tesseract has no language pack for {', '.join(wanted)}.",
        remedy=(
            f"Installed: {', '.join(sorted(installed))}. "
            "Install the pack for your platform, or choose one of these."
        ),
        context={"parameter": "lang", "missing": wanted, "installed": sorted(installed)},
    )


def _opencv_missing() -> InvalidParameterError:
    """``--deskew`` was asked for and OpenCV is absent."""
    from docmax.core.branding import DIST_NAME

    return InvalidParameterError(
        "Straightening pages needs OpenCV, which is not installed.",
        remedy=(
            f'Install it with: pip install "{DIST_NAME}[ocr]" — '
            "or pass --no-deskew to recognise the pages as they are."
        ),
        context={"parameter": "deskew", "dependency": "cv2"},
    )


def _every_page_failed(failed: list[int]) -> Exception:
    """Nothing was recognised. Built rather than raised, so the caller raises."""
    from docmax.core.errors import ExternalToolFailedError

    return ExternalToolFailedError(
        f"Tesseract could not recognise any of the {len(failed)} page(s) it was given.",
        remedy="Check --lang matches the document, and try a higher --dpi.",
        context={"tool": "ocr", "failed_pages": failed},
    )


def _version(binary: str, cancellation: CancellationToken) -> str:
    """Whatever actually did the work, in the same form the cloud engine reports.

    Best-effort: a version probe that failed must not fail a recognition that
    already succeeded, so the result simply says less.
    """
    from docmax.core.errors import DocMaxError

    try:
        completed = _binaries.run([binary, "--version"], tool="ocr", cancellation=cancellation)
    except DocMaxError:
        return f"{TESSERACT}/unknown"

    reported = (completed.stdout or completed.stderr or b"").decode("utf-8", errors="replace")
    first = reported.strip().splitlines()
    if not first:
        return f"{TESSERACT}/unknown"
    # The first line is `tesseract 5.3.4`; the name is already in the prefix.
    words = first[0].strip().split()
    return f"{TESSERACT}/{words[-1] if words else 'unknown'}"


def build() -> EngineStrategy:
    """Factory the registry calls. Every strategy module exposes exactly this."""
    return OcrLocal()


__all__ = [
    "BINARIES",
    "PYTHON_DEPENDENCIES",
    "OcrLocal",
    "build",
    "install_hint",
    "missing_dependencies",
]
