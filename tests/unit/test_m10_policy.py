"""The MCP policy boundary, on its own.

`policy.py` imports no SDK, so the security half of this interface is testable
with no protocol session — which matters more here than anywhere else in M10,
because these are the assertions that stand between a prompt-injected agent and
the user's home directory.

The protocol-level counterparts live in `test_m10_mcp.py`; these are the unit
tests underneath them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from docmax.core.config import Config
from docmax.core.errors import InvalidParameterError
from docmax.mcp.policy import Policy


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    inside = tmp_path / "allowed"
    outside = tmp_path / "elsewhere"
    inside.mkdir()
    outside.mkdir()
    return inside, outside


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


def test_the_default_root_is_the_working_directory() -> None:
    """Never "everywhere": a default that is safe only once configured is not."""
    assert Policy.build().roots == (Path.cwd().resolve(),)


def test_roots_are_resolved(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    policy = Policy.build([tmp_path / "a" / ".." / "a" / "b"])

    assert policy.roots == (nested.resolve(),)


def test_a_root_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(InvalidParameterError, match="cannot be a root"):
        Policy.build([target])


def test_a_missing_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidParameterError, match="cannot be a root"):
        Policy.build([tmp_path / "absent"])


def test_a_path_inside_a_root_is_allowed(tree: tuple[Path, Path]) -> None:
    inside, _ = tree
    policy = Policy.build([inside])

    assert policy.check(inside / "doc.pdf", field="inputs") == (inside / "doc.pdf").resolve()


def test_the_root_itself_is_inside_the_root(tree: tuple[Path, Path]) -> None:
    inside, _ = tree

    assert Policy.build([inside]).contains(inside)


def test_a_nested_path_is_allowed(tree: tuple[Path, Path]) -> None:
    inside, _ = tree
    deep = inside / "a" / "b" / "c.pdf"

    assert Policy.build([inside]).contains(deep)


def test_a_path_outside_every_root_is_refused(tree: tuple[Path, Path]) -> None:
    inside, outside = tree

    with pytest.raises(InvalidParameterError, match="outside every allowed root"):
        Policy.build([inside]).check(outside / "secret.pdf", field="inputs")


def test_traversal_out_of_a_root_is_refused(tree: tuple[Path, Path]) -> None:
    """`..` is collapsed by resolution before the comparison, which is the check."""
    inside, _ = tree
    escape = inside / ".." / "elsewhere" / "secret.pdf"

    with pytest.raises(InvalidParameterError, match="outside every allowed root"):
        Policy.build([inside]).check(escape, field="inputs")


def test_a_sibling_with_a_shared_prefix_is_refused(tmp_path: Path) -> None:
    """`/allowed-other` must not pass because it starts with `/allowed`.

    A string `startswith` would let it through. `is_relative_to` compares path
    components, which is why it is used instead.
    """
    allowed = tmp_path / "allowed"
    lookalike = tmp_path / "allowed-other"
    allowed.mkdir()
    lookalike.mkdir()

    with pytest.raises(InvalidParameterError, match="outside every allowed root"):
        Policy.build([allowed]).check(lookalike / "x.pdf", field="inputs")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privilege on Windows")
def test_a_symlink_pointing_out_of_a_root_is_refused(tree: tuple[Path, Path]) -> None:
    """Resolution is what makes this the same case as `..`."""
    inside, outside = tree
    secret = outside / "secret.pdf"
    secret.write_text("secret", encoding="utf-8")
    (inside / "link.pdf").symlink_to(secret)

    with pytest.raises(InvalidParameterError, match="outside every allowed root"):
        Policy.build([inside]).check(inside / "link.pdf", field="inputs")


def test_several_roots_are_all_allowed(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    policy = Policy.build([first, second])

    assert policy.contains(first / "a.pdf")
    assert policy.contains(second / "b.pdf")


def test_the_refusal_names_the_field_it_came_from(tree: tuple[Path, Path]) -> None:
    inside, outside = tree

    with pytest.raises(InvalidParameterError) as caught:
        Policy.build([inside]).check(outside / "x.pdf", field="output")

    assert "'output'" in caught.value.message


def test_the_refusal_names_the_roots_so_the_caller_can_fix_it(
    tree: tuple[Path, Path],
) -> None:
    inside, outside = tree

    with pytest.raises(InvalidParameterError) as caught:
        Policy.build([inside]).check(outside / "x.pdf", field="inputs")

    assert str(inside.resolve()) in (caught.value.remedy or "")


def test_check_all_refuses_on_the_first_bad_path(tree: tuple[Path, Path]) -> None:
    inside, outside = tree
    policy = Policy.build([inside])

    with pytest.raises(InvalidParameterError):
        policy.check_all([inside / "ok.pdf", outside / "bad.pdf"], field="inputs")


# ---------------------------------------------------------------------------
# Cloud
# ---------------------------------------------------------------------------


def test_offline_is_forced_by_default() -> None:
    assert Policy.build().configure(Config()).offline is True


def test_allow_cloud_leaves_the_configuration_alone() -> None:
    assert Policy.build(allow_cloud=True).configure(Config()).offline is False


def test_allow_cloud_cannot_clear_a_configured_offline() -> None:
    """`offline` is one-way. A flag on a protocol server must not defeat a policy."""
    configured = Policy.build(allow_cloud=True).configure(Config(offline=True))

    assert configured.offline is True


def test_configure_changes_nothing_else() -> None:
    original = Config(cloud_endpoint="https://example.test", api_key="k")

    configured = Policy.build().configure(original)

    assert configured.cloud_endpoint == original.cloud_endpoint
    assert configured.api_key == original.api_key


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_describe_states_the_roots_and_the_cloud_setting(tree: tuple[Path, Path]) -> None:
    inside, _ = tree

    described = Policy.build([inside]).describe()

    assert str(inside.resolve()) in described
    assert "Cloud engines: disabled" in described


def test_describe_says_when_cloud_is_enabled(tree: tuple[Path, Path]) -> None:
    inside, _ = tree

    assert "Cloud engines: enabled" in Policy.build([inside], allow_cloud=True).describe()


def test_a_policy_cannot_be_widened_after_construction(tree: tuple[Path, Path]) -> None:
    """A policy a request could widen is not a policy."""
    import dataclasses

    policy = Policy.build([tree[0]])

    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.allow_cloud = True  # type: ignore[misc]
