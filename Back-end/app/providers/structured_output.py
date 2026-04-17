import json


def require_output_schema(structured_output, output_schema):
    if not structured_output:
        return None
    if not isinstance(output_schema, dict):
        raise ValueError("structured_output=True requires output_schema to be a dict.")
    return output_schema


def schema_name(output_schema, default="structured_output"):
    if isinstance(output_schema, dict):
        title = str(output_schema.get("title") or "").strip()
        if title:
            return title
    return default


def is_valid_json_text(text):
    if not isinstance(text, str) or not text.strip():
        return False
    try:
        json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return False
    return True


def warning_payload(provider, model, output_schema, reason, native_method, used_prompt_fallback):
    return {
        "provider": provider,
        "model": model,
        "schema_name": schema_name(output_schema),
        "reason": str(reason or "structured output unavailable"),
        "native_method": native_method,
        "used_prompt_fallback": bool(used_prompt_fallback),
    }
