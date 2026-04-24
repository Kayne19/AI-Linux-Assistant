"""Pure, stateless helpers for constructing OpenAI Responses API request kwargs.

These functions contain no self-references and are safe to import from both
the synchronous OpenAICaller and the async Batch API wrapper without risk of
circular imports.
"""

from providers.structured_output import schema_name


def schema_allows_null(schema):
    """Return True if the given JSON schema allows a null value."""
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if schema_type == "null":
        return True
    if isinstance(schema_type, list) and "null" in schema_type:
        return True
    if None in (schema.get("enum") or []):
        return True
    for variant_key in ("anyOf", "oneOf"):
        variants = schema.get(variant_key) or []
        if any(isinstance(v, dict) and schema_allows_null(v) for v in variants):
            return True
    return False


def make_schema_nullable(schema):
    """Wrap *schema* in ``anyOf: [schema, {type: null}]`` if it does not already allow null."""
    if schema_allows_null(schema):
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def normalize_strict_schema(schema):
    """Recursively normalise a JSON schema for use with OpenAI strict structured output.

    - All properties are required (OpenAI strict mode requires this).
    - Optional properties become nullable via ``anyOf``.
    - Nested sub-schemas are normalised recursively.
    """
    if isinstance(schema, list):
        return [normalize_strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    normalized = {}
    for key, value in schema.items():
        if key in {"properties", "items", "anyOf", "oneOf", "allOf"}:
            continue
        normalized[key] = value

    properties = schema.get("properties")
    if isinstance(properties, dict):
        original_required = set(schema.get("required") or [])
        normalized_properties = {}
        for name, subschema in properties.items():
            normalized_subschema = normalize_strict_schema(subschema)
            if name not in original_required:
                normalized_subschema = make_schema_nullable(normalized_subschema)
            normalized_properties[name] = normalized_subschema
        normalized["properties"] = normalized_properties
        normalized["required"] = list(properties.keys())

    if "items" in schema:
        normalized["items"] = normalize_strict_schema(schema["items"])
    for variant_key in ("anyOf", "oneOf", "allOf"):
        if variant_key in schema:
            normalized[variant_key] = normalize_strict_schema(schema[variant_key])
    return normalized


def build_structured_output_kwargs(output_schema):
    """Return the ``text`` kwarg block that activates OpenAI native JSON schema output."""
    normalized_schema = normalize_strict_schema(output_schema)
    return {
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name(output_schema),
                "schema": normalized_schema,
                "strict": True,
            }
        }
    }


def build_responses_request_kwargs(
    *,
    model: str,
    reasoning_effort,
    system_prompt: str,
    translated_tools,
    temperature,
    max_output_tokens,
    structured_output: bool = False,
    output_schema=None,
) -> dict:
    """Construct the kwargs passed to ``client.responses.create(...)``.

    Used by both the synchronous OpenAICaller and the Batch API wrapper so a
    single chunk payload is byte-for-byte identical on either path.

    Args:
        model: The OpenAI model identifier string.
        reasoning_effort: When set, enables reasoning mode and suppresses
            temperature (OpenAI does not allow both simultaneously).
        system_prompt: System instructions placed in the ``instructions`` field.
        translated_tools: Pre-translated list of tool dicts, or None/empty list.
        temperature: Sampling temperature; ignored when *reasoning_effort* is set.
        max_output_tokens: Hard cap on output token count.
        structured_output: If True, attach the JSON schema output format block.
        output_schema: The JSON schema dict; required when *structured_output* is True.

    Returns:
        A plain ``dict`` suitable for ``**``-unpacking into ``responses.create``.
    """
    request_kwargs: dict = {
        "model": model,
        "instructions": system_prompt,
    }
    if reasoning_effort:
        request_kwargs["reasoning"] = {"effort": reasoning_effort}
    if translated_tools:
        request_kwargs["tools"] = translated_tools
        request_kwargs["parallel_tool_calls"] = True
    if temperature is not None and not reasoning_effort:
        request_kwargs["temperature"] = temperature
    if max_output_tokens is not None:
        request_kwargs["max_output_tokens"] = max_output_tokens
    if structured_output:
        request_kwargs.update(build_structured_output_kwargs(output_schema))
    return request_kwargs
