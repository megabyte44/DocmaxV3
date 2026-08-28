"""`ToolSpec` rendered as JSON Schema, checked against every registered tool.

Parametrised over the registry rather than over a hand-written list, for the
reason `test_cli_json.py` gives about itself: a list agrees on the day it is
written and drifts the first time someone adds a tool.

`schema.py` imports no SDK, so none of this needs a protocol session, an event
loop, or the optional dependency. That is the same argument `tui/forms.py` made
at M7 and it is why the mapping lives in its own module.
"""

from __future__ import annotations

from typing import Any

import pytest

from docmax.core.registry import Param, ToolSpec, get_tool, iter_tools
from docmax.mcp import schema

ALL_SPECS = list(iter_tools())
SPEC_IDS = [spec.name for spec in ALL_SPECS]


def spec_with(*params: Param, **kwargs: Any) -> ToolSpec:
    from docmax.core.models import Engine

    defaults: dict[str, Any] = {
        "name": "widget",
        "summary": "Does a thing.",
        "category": "test",
        "module": "docmax.tools.widget",
        "supported_engines": frozenset({Engine.LOCAL}),
        "params": params,
    }
    defaults.update(kwargs)
    return ToolSpec(**defaults)


# ---------------------------------------------------------------------------
# Every registered tool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", ALL_SPECS, ids=SPEC_IDS)
def test_every_tool_produces_a_well_formed_schema(spec: ToolSpec) -> None:
    body = schema.input_schema(spec)

    assert body["type"] == "object"
    assert body["additionalProperties"] is False
    assert schema.INPUTS in body["properties"]
    assert schema.OUTPUT in body["properties"]
    assert schema.INPUTS in body["required"]


@pytest.mark.parametrize("spec", ALL_SPECS, ids=SPEC_IDS)
def test_every_tool_schema_validates_as_json_schema(spec: ToolSpec) -> None:
    """Not merely a dict of the right shape — a schema a client can compile."""
    import jsonschema  # type: ignore[import-untyped]

    jsonschema.Draft202012Validator.check_schema(schema.input_schema(spec))


@pytest.mark.parametrize("spec", ALL_SPECS, ids=SPEC_IDS)
def test_every_declared_parameter_appears(spec: ToolSpec) -> None:
    properties = schema.input_schema(spec)["properties"]

    for param in spec.params:
        assert param.name in properties, f"{spec.name} lost {param.name}"
        assert properties[param.name]["description"] == param.description


@pytest.mark.parametrize("spec", ALL_SPECS, ids=SPEC_IDS)
def test_no_tool_offers_force(spec: ToolSpec) -> None:
    """ADR 0028/0029: an agent may not overwrite a file it did not create."""
    assert "force" not in schema.input_schema(spec)["properties"]


@pytest.mark.parametrize("spec", ALL_SPECS, ids=SPEC_IDS)
def test_required_parameters_are_marked_required(spec: ToolSpec) -> None:
    required = set(schema.input_schema(spec)["required"])

    for param in spec.params:
        assert (param.name in required) is param.required, param.name


# ---------------------------------------------------------------------------
# The mapping itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("str", "string"),
        ("int", "integer"),
        ("float", "number"),
        ("bool", "boolean"),
        ("path", "string"),
    ],
)
def test_every_param_type_maps(declared: str, expected: str) -> None:
    """`Param.type_` is a closed set precisely so this mapping can be total."""
    body = schema.input_schema(spec_with(Param(name="x", description="d", type_=declared)))

    assert body["properties"]["x"]["type"] == expected


def test_choices_become_an_enum() -> None:
    body = schema.input_schema(
        spec_with(Param(name="preset", description="quality", choices=("screen", "ebook")))
    )

    assert body["properties"]["preset"]["enum"] == ["screen", "ebook"]


def test_a_default_is_carried_through() -> None:
    body = schema.input_schema(
        spec_with(Param(name="dpi", description="d", type_="int", default=300))
    )

    assert body["properties"]["dpi"]["default"] == 300


def test_a_none_default_is_omitted_rather_than_null() -> None:
    """`None` means "no default", which is not the same as a default of null."""
    body = schema.input_schema(spec_with(Param(name="pages", description="d", default=None)))

    assert "default" not in body["properties"]["pages"]


def test_a_single_input_tool_is_bounded_to_one() -> None:
    body = schema.input_schema(spec_with(accepts_multiple_inputs=False))
    inputs = body["properties"][schema.INPUTS]

    assert inputs["type"] == "array"
    assert inputs["minItems"] == 1
    assert inputs["maxItems"] == 1


def test_a_multi_input_tool_is_unbounded() -> None:
    body = schema.input_schema(spec_with(accepts_multiple_inputs=True))

    assert "maxItems" not in body["properties"][schema.INPUTS]


def test_merge_accepts_several_inputs() -> None:
    """The real multi-input tool, so the flag is exercised against a live spec."""
    body = schema.input_schema(get_tool("merge"))

    assert "maxItems" not in body["properties"][schema.INPUTS]


def test_output_is_optional() -> None:
    """So the tools that produce nothing are callable without inventing a path."""
    body = schema.input_schema(get_tool("get-info"))

    assert schema.OUTPUT not in body["required"]


def test_the_output_description_names_the_default_suffix() -> None:
    body = schema.input_schema(get_tool("merge"))

    assert ".pdf" in body["properties"][schema.OUTPUT]["description"]


def test_the_description_carries_the_summary_and_category() -> None:
    spec = get_tool("merge")

    described = schema.described(spec)

    assert spec.summary in described
    assert spec.category in described


def test_inputs_and_output_are_not_parameter_names() -> None:
    """A tool declaring `inputs` or `output` would silently collide.

    Not a hypothetical worth ignoring: the schema puts both at the top level
    beside the declared parameters, so a clash would have one overwrite the
    other. No tool does this today, and this is what says so.
    """
    reserved = {schema.INPUTS, schema.OUTPUT}
    offenders = [
        f"{spec.name}.{param.name}"
        for spec in ALL_SPECS
        for param in spec.params
        if param.name in reserved
    ]

    assert not offenders, offenders
