# ---------------------------------------------------------------------------
# MCP Tools Validation
# Description:  Functions for validating input parameters for MCP tools.
# Copyright (c) 2026 Finite Element Analysis Ltd.
# All rights reserved.
# ---------------------------------------------------------------------------

from toolsResponses import *
import logging, inspect
from functools import wraps


# --- Functions -------------------------------------------------------------

def safe_tool_wrapper(fn, global_lusas=None):
    """Wrap a tool to catch/log uncaught exceptions and normalise responses.

    If the wrapped function's first parameter is named 'lusas' and
    `global_lusas` is provided, the wrapper will call the function with
    that object bound as the first argument. The wrapper also coerces
    legacy return types into the canonical ok()/error() contract.
    """
    tool_signature = inspect.signature(fn)
    parameters = list(tool_signature.parameters.values())
    has_lusas_param = len(parameters) > 0 and parameters[0].name == "lusas"
    if has_lusas_param:
        return_signature = tool_signature.replace(parameters=parameters[1:])
    else:
        return_signature = tool_signature

    def _normalise_response(result):
        try:
            if isinstance(result, dict) and isinstance(result.get("ok"), bool):
                return result
        except Exception:
            pass

        if isinstance(result, str):
            if result.startswith("Error:") or result.lower().startswith("error"):
                return error("legacy_error", result, {})
            return ok({"result": result})

        if result is None:
            return ok({})

        return ok({"result": result})

    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                if has_lusas_param and global_lusas is not None:
                    result = await fn(global_lusas, *args, **kwargs)
                else:
                    result = await fn(*args, **kwargs)
                return _normalise_response(result)
            except Exception as e:
                logger.exception("Unhandled exception in tool %s", fn.__name__)
                return error(
                    "tool_execution_failed",
                    f"Unhandled exception in tool '{fn.__name__}'.",
                    {"exception": str(e)},
                )

        async_wrapper.__signature__ = return_signature
        return async_wrapper

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            if has_lusas_param and global_lusas is not None:
                result = fn(global_lusas, *args, **kwargs)
            else:
                result = fn(*args, **kwargs)
            return _normalise_response(result)
        except Exception as e:
            logger.exception("Unhandled exception in tool %s", fn.__name__)
            return error(
                "tool_execution_failed",
                f"Unhandled exception in tool '{fn.__name__}'.",
                {"exception": str(e)},
            )

    wrapper.__signature__ = return_signature
    return wrapper

# --- Main -------------------------------------------------------------

logger = logging.getLogger('lusas_mcp_server')
