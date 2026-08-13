"""The error hierarchy is public API. These tests pin its contract."""

from __future__ import annotations

from docmax.core import errors
from docmax.core.errors import (
    CloudEngineUnavailableError,
    CloudTimeoutError,
    ConsentRequiredError,
    DocMaxError,
    ErrorCode,
    InPlaceOverwriteError,
    InputError,
    InputNotFoundError,
    InternalError,
    LocalDependencyMissingError,
)


def _error_classes() -> list[type[DocMaxError]]:
    """Every exported error class, including the base."""
    return [
        obj
        for name in errors.__all__
        if isinstance(obj := getattr(errors, name), type) and issubclass(obj, DocMaxError)
    ]


def _concrete_error_classes() -> list[type[DocMaxError]]:
    """Every exported error class a call site would actually raise.

    ``DocMaxError`` is excluded: it is the catch-everything base, and its
    ``INTERNAL`` code is the correct fallback for anything that fails to
    declare its own.
    """
    return [cls for cls in _error_classes() if cls is not DocMaxError]


def test_every_error_class_declares_a_code() -> None:
    """No class may silently inherit ``INTERNAL`` and mislabel itself."""
    mislabelled = [
        cls.__name__
        for cls in _concrete_error_classes()
        if cls is not InternalError and cls.code is ErrorCode.INTERNAL
    ]
    assert not mislabelled, (
        f"these classes inherit INTERNAL instead of declaring a code: {mislabelled}"
    )


def test_error_codes_are_unique() -> None:
    """Codes appear in ``--json`` output; duplicates make them ambiguous."""
    seen: dict[ErrorCode, str] = {}
    duplicates: list[str] = []
    for cls in _concrete_error_classes():
        if cls.code in seen:
            duplicates.append(f"{cls.__name__} duplicates {seen[cls.code]} ({cls.code.value})")
        else:
            seen[cls.code] = cls.__name__
    assert not duplicates, duplicates


def test_family_codes_are_prefixes_of_their_members() -> None:
    """``code.startswith("input")`` must reliably match the whole input family."""
    assert InputNotFoundError.code.value.startswith(InputError.code.value)
    assert CloudTimeoutError.code.value.startswith("cloud.")


def test_message_and_remedy_are_carried() -> None:
    exc = InputNotFoundError("no such file: report.pdf")
    assert str(exc) == "no such file: report.pdf"
    assert exc.remedy, "anticipated errors must tell the user what to do next"


def test_explicit_remedy_overrides_the_default() -> None:
    exc = InputNotFoundError("gone", remedy="check the mount")
    assert exc.remedy == "check the mount"


def test_to_dict_is_json_shaped() -> None:
    exc = InPlaceOverwriteError("output is also an input", context={"path": "a.pdf"})
    payload = exc.to_dict()

    assert payload["code"] == ErrorCode.OUTPUT_IN_PLACE.value
    assert payload["message"] == "output is also an input"
    assert payload["remedy"]
    assert payload["retryable"] is False
    assert payload["context"] == {"path": "a.pdf"}


def test_dependency_error_carries_what_is_missing() -> None:
    """The router needs this to offer the cloud engine as an alternative."""
    exc = LocalDependencyMissingError(
        "Tesseract is not installed",
        dependency="tesseract",
        install_hint="docmax setup --ocr",
    )
    assert exc.dependency == "tesseract"
    assert exc.context["dependency"] == "tesseract"
    assert exc.remedy == "docmax setup --ocr"


def test_consent_error_names_the_tool() -> None:
    """Consent is recorded per tool, not globally."""
    exc = ConsentRequiredError("uploading would send your document", tool="ocr")
    assert exc.tool == "ocr"
    assert exc.context["tool"] == "ocr"


def test_cloud_failures_are_retryable_by_default() -> None:
    assert CloudTimeoutError.retryable is True
    assert issubclass(CloudTimeoutError, CloudEngineUnavailableError)


def test_auth_and_quota_are_not_retryable() -> None:
    """Retrying a bad key or an exhausted quota just wastes the user's time."""
    assert errors.CloudAuthError.retryable is False
    assert errors.CloudQuotaExceededError.retryable is False


def test_internal_errors_are_not_the_users_fault() -> None:
    assert InternalError.user_fixable is False
    assert InternalError("boom").remedy


def test_every_error_is_catchable_as_docmax_error() -> None:
    """One `except DocMaxError` at the CLI boundary must catch everything."""
    for cls in _error_classes():
        assert issubclass(cls, DocMaxError)
        assert issubclass(cls, Exception)


def test_docmax_error_is_not_a_system_exit() -> None:
    """The v2 regression guard.

    ``SystemExit`` does not inherit from ``Exception``, which is precisely why
    v2's ``except Exception`` handlers failed to catch ``abort()`` and a single
    missing dependency killed a whole batch run.
    """
    assert not issubclass(DocMaxError, SystemExit)
    assert not issubclass(DocMaxError, BaseException) or issubclass(DocMaxError, Exception)
