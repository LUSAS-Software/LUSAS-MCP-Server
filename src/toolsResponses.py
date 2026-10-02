# ---------------------------------------------------------------------------
# MCP Tools Responses
# Description:  Functions for creating standardized responses for MCP tools.
# Copyright (c) 2026 Finite Element Analysis Ltd.
# All rights reserved.
# ---------------------------------------------------------------------------

def ok(data: dict) -> dict:
    """Returns a standardized success response with the provided data."""
    return {"ok": True, "data": data}

def error(code: str, message: str, details: dict | None = None) -> dict:
    """Returns a standardized error response with the provided code, message, and optional details."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }
