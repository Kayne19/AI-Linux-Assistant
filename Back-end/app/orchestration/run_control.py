import inspect


class RunCancelledError(RuntimeError):
    pass


def invoke_cancel_check(cancel_check, checkpoint):
    if cancel_check is None:
        return
    result = cancel_check(checkpoint)
    if result:
        raise RunCancelledError("Run cancelled.")


def call_with_optional_cancel_check(callable_obj, cancel_check=None, **kwargs):
    if cancel_check is not None:
        try:
            signature = inspect.signature(callable_obj)
            if "cancel_check" in signature.parameters:
                kwargs["cancel_check"] = cancel_check
        except (TypeError, ValueError):
            kwargs["cancel_check"] = cancel_check
    return callable_obj(**kwargs)
