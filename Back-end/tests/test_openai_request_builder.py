"""Tests for providers.openai_request_builder.

Verifies that:
1. build_responses_request_kwargs produces byte-identical dicts to
   OpenAICaller._build_request_kwargs for the same inputs.
2. The schema-transform helpers (schema_allows_null, make_schema_nullable,
   normalize_strict_schema) match the OpenAICaller class-method behaviour for
   representative schemas.
"""
import sys
import os
import types

# ---------------------------------------------------------------------------
# Minimal stub so openAI_caller imports cleanly without a real OpenAI key
# ---------------------------------------------------------------------------

def _install_stubs():
    """Insert fake modules so openAI_caller.py can be imported in tests."""
    # Fake openai SDK
    if "openai" not in sys.modules:
        fake_openai = types.ModuleType("openai")
        fake_openai.OpenAI = None  # will raise RuntimeError on instantiation
        sys.modules["openai"] = fake_openai

    # Fake dotenv
    if "dotenv" not in sys.modules:
        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.load_dotenv = lambda: False
        sys.modules["dotenv"] = fake_dotenv


_install_stubs()

# Make sure the app directory is on the path
_APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from providers.openai_request_builder import (
    build_responses_request_kwargs,
    build_structured_output_kwargs,
    normalize_strict_schema,
    schema_allows_null,
    make_schema_nullable,
)

# ---------------------------------------------------------------------------
# Helper: build a minimal OpenAICaller-like object to call _build_request_kwargs
# without needing a real OpenAI client.
# ---------------------------------------------------------------------------

def _make_caller(model="gpt-4.1-mini", reasoning_effort=None):
    """Return a stub OpenAICaller instance that never touches the network."""
    # We need to bypass __init__ (which calls OpenAI()) so we patch at class level.
    import providers.openAI_caller as mod

    class _StubCaller(mod.OpenAICaller):
        def __init__(self):
            # Skip super().__init__ — no real client needed
            self.model = model
            self.reasoning_effort = reasoning_effort

    return _StubCaller()


# ---------------------------------------------------------------------------
# Tests: build_responses_request_kwargs vs OpenAICaller._build_request_kwargs
# ---------------------------------------------------------------------------

class TestBuildResponsesRequestKwargsParity:
    """Module-level function must produce identical output to the class method."""

    def _module_kwargs(self, **kw):
        return build_responses_request_kwargs(**kw)

    def _class_kwargs(self, caller, system_prompt, translated_tools, temperature,
                      max_output_tokens, structured_output=False, output_schema=None):
        return caller._build_request_kwargs(
            system_prompt, translated_tools, temperature, max_output_tokens,
            structured_output=structured_output, output_schema=output_schema,
        )

    def test_basic_no_tools(self):
        caller = _make_caller()
        cls_result = self._class_kwargs(caller, "sys", [], None, None)
        mod_result = self._module_kwargs(
            model="gpt-4.1-mini",
            reasoning_effort=None,
            system_prompt="sys",
            translated_tools=[],
            temperature=None,
            max_output_tokens=None,
        )
        assert cls_result == mod_result

    def test_with_tools_and_temperature(self):
        fake_tool = {"type": "function", "name": "my_tool", "description": "does stuff",
                     "parameters": {}, "strict": True}
        caller = _make_caller()
        cls_result = self._class_kwargs(caller, "prompt", [fake_tool], 0.7, 1024)
        mod_result = self._module_kwargs(
            model="gpt-4.1-mini",
            reasoning_effort=None,
            system_prompt="prompt",
            translated_tools=[fake_tool],
            temperature=0.7,
            max_output_tokens=1024,
        )
        assert cls_result == mod_result

    def test_reasoning_effort_suppresses_temperature(self):
        caller = _make_caller(reasoning_effort="medium")
        cls_result = self._class_kwargs(caller, "sys", [], 0.5, None)
        mod_result = self._module_kwargs(
            model="gpt-4.1-mini",
            reasoning_effort="medium",
            system_prompt="sys",
            translated_tools=[],
            temperature=0.5,
            max_output_tokens=None,
        )
        assert cls_result == mod_result
        # Both must have reasoning block and no temperature
        assert "reasoning" in cls_result
        assert "temperature" not in cls_result

    def test_structured_output(self):
        schema = {"type": "object", "title": "MySchema", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
        caller = _make_caller()
        cls_result = self._class_kwargs(caller, "sys", [], None, None,
                                        structured_output=True, output_schema=schema)
        mod_result = self._module_kwargs(
            model="gpt-4.1-mini",
            reasoning_effort=None,
            system_prompt="sys",
            translated_tools=[],
            temperature=None,
            max_output_tokens=None,
            structured_output=True,
            output_schema=schema,
        )
        assert cls_result == mod_result
        assert "text" in cls_result

    def test_different_model(self):
        caller = _make_caller(model="o3")
        cls_result = self._class_kwargs(caller, "hi", None, None, 512)
        mod_result = self._module_kwargs(
            model="o3",
            reasoning_effort=None,
            system_prompt="hi",
            translated_tools=None,
            temperature=None,
            max_output_tokens=512,
        )
        assert cls_result == mod_result


# ---------------------------------------------------------------------------
# Tests: schema_allows_null
# ---------------------------------------------------------------------------

class TestSchemaAllowsNull:
    def _class_method(self, schema):
        return _make_caller()._schema_allows_null(schema)

    def test_null_type(self):
        s = {"type": "null"}
        assert schema_allows_null(s) == self._class_method(s) == True

    def test_union_type_list_with_null(self):
        s = {"type": ["string", "null"]}
        assert schema_allows_null(s) == self._class_method(s) == True

    def test_union_type_list_without_null(self):
        s = {"type": ["string", "integer"]}
        assert schema_allows_null(s) == self._class_method(s) == False

    def test_enum_with_none(self):
        s = {"enum": [1, None, "a"]}
        assert schema_allows_null(s) == self._class_method(s) == True

    def test_any_of_with_null_variant(self):
        s = {"anyOf": [{"type": "string"}, {"type": "null"}]}
        assert schema_allows_null(s) == self._class_method(s) == True

    def test_one_of_without_null(self):
        s = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        assert schema_allows_null(s) == self._class_method(s) == False

    def test_plain_string(self):
        s = {"type": "string"}
        assert schema_allows_null(s) == self._class_method(s) == False

    def test_non_dict(self):
        assert schema_allows_null("not a schema") == self._class_method("not a schema") == False


# ---------------------------------------------------------------------------
# Tests: make_schema_nullable
# ---------------------------------------------------------------------------

class TestMakeSchemaNull:
    def _class_method(self, schema):
        return _make_caller()._make_schema_nullable(schema)

    def test_already_nullable_passthrough(self):
        s = {"type": "null"}
        assert make_schema_nullable(s) == self._class_method(s) == s

    def test_wraps_non_nullable(self):
        s = {"type": "string"}
        result_mod = make_schema_nullable(s)
        result_cls = self._class_method(s)
        assert result_mod == result_cls
        assert result_mod == {"anyOf": [{"type": "string"}, {"type": "null"}]}

    def test_already_any_of_with_null(self):
        s = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
        assert make_schema_nullable(s) == self._class_method(s) == s


# ---------------------------------------------------------------------------
# Tests: normalize_strict_schema
# ---------------------------------------------------------------------------

class TestNormalizeStrictSchema:
    def _class_method(self, schema):
        return _make_caller()._normalize_strict_schema(schema)

    def test_passthrough_non_dict(self):
        assert normalize_strict_schema("raw") == self._class_method("raw") == "raw"

    def test_list_passthrough(self):
        lst = [{"type": "string"}, {"type": "integer"}]
        assert normalize_strict_schema(lst) == self._class_method(lst)

    def test_required_fields_become_nullable(self):
        schema = {
            "type": "object",
            "properties": {
                "required_field": {"type": "string"},
                "optional_field": {"type": "integer"},
            },
            "required": ["required_field"],
        }
        result_mod = normalize_strict_schema(schema)
        result_cls = self._class_method(schema)
        assert result_mod == result_cls
        # required_field should NOT be made nullable
        assert result_mod["properties"]["required_field"] == {"type": "string"}
        # optional_field should be wrapped in anyOf with null
        assert result_mod["properties"]["optional_field"] == {
            "anyOf": [{"type": "integer"}, {"type": "null"}]
        }
        # All properties appear in required list
        assert set(result_mod["required"]) == {"required_field", "optional_field"}

    def test_nested_normalization(self):
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "string"},
                    },
                    "required": [],
                }
            },
            "required": [],
        }
        result_mod = normalize_strict_schema(schema)
        result_cls = self._class_method(schema)
        assert result_mod == result_cls

    def test_items_recursion(self):
        schema = {
            "type": "array",
            "items": {"type": "object", "properties": {"v": {"type": "number"}}, "required": []},
        }
        result_mod = normalize_strict_schema(schema)
        result_cls = self._class_method(schema)
        assert result_mod == result_cls
        assert "items" in result_mod

    def test_any_of_recursion(self):
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        result_mod = normalize_strict_schema(schema)
        result_cls = self._class_method(schema)
        assert result_mod == result_cls
