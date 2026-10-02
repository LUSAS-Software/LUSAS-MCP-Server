# ---------------------------------------------------------------------------
# Tools Helpers
# Description:  Tools helper functions for LUSAS MCP Server.
# Copyright (c) 2026 Finite Element Analysis Ltd.
# All rights reserved.
# ---------------------------------------------------------------------------

import os, logging, time, subprocess, base64, binascii, re, math
from datetime import datetime, timezone
from mcp.types import CallToolResult, ImageContent, TextContent
from mcp.server.fastmcp.utilities.types import Image

# --- Constants -------------------------------------------------------------

_QUERY_FILTERED_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "how", "if", "in", "is", "it", "its", "of", "on", "or",
    "the", "this", "to", "what", "when", "where", "which", "with", "you",
    "your", "lusas"
}

# --- Logging -------------------------------------------------------------

def configure_logging(log_filepath=''):
    log_filepath = log_filepath or 'lusas_mcp.log'
    log_directory = os.path.dirname(log_filepath)
    if log_directory:
        os.makedirs(log_directory, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_filepath, encoding='utf-8')
        ],
        force=True,
    )

# --- Scripts -------------------------------------------------------------

def prepare_vbscript(code: str, injectErrorHandling: bool = True) -> str:
    """
    Prepares VBScript code for execution in LUSAS by handling line continuations, injecting error handling, and ensuring the correct engine declaration.
    """
    initial_lines = code.splitlines()
    code_lines = initial_lines.copy()

    # Ensure engine declaration is only once at the top
    code_lines.insert(0, "$ENGINE=VBSCRIPT")
    for i in range(1, len(code_lines)):
        if code_lines[i].strip().startswith("$ENGINE="):
            code_lines[i] = ""

    # Comment Option commands
    for i in range(len(code_lines)):
        if code_lines[i].strip().lower().startswith("option explicit"):
            code_lines[i] = "'" + code_lines[i]

    # Inject error handling
    if injectErrorHandling:
        # Check if code has subs or functions
        has_subs_or_functions = False
        for line in code_lines:
            stripped = line.strip().lower()
            if stripped.startswith("sub ") or stripped.startswith("function "):
                has_subs_or_functions = True
                break

        helper = """'Injected error handling helper function
On Error Resume Next
Dim NoFatalErrorScriptAI
NoFatalErrorScriptAI = True
Function CheckError(line)
	If Err.Number <> 0 And Not FatalErrorScriptAI Then
        Call getTextWindow().writeLine("AI Script ERROR: Line " & line & ", Error " & Err.Number & " - " & Err.Description, 86)
        Err.Clear
		NoFatalErrorScriptAI = False
        CheckError = True
    Else
        CheckError = False
    End If
End Function
"""
        processed_lines = []
        for line in code_lines:
            stripped = line.strip()
            
            # Add helper after engine definition
            if stripped.startswith("$ENGINE="):
                processed_lines.append(line)
                processed_lines.append(helper)
                if not has_subs_or_functions:
                    processed_lines.append("Sub Main()")
                    processed_lines.append("On Error Resume Next")
                continue

            # Skip duplicate On Error Resume Next if already present
            lower = stripped.lower()
            if lower == "on error resume next":
                continue

            # Add line to processed lines
            processed_lines.append(line)

            # Preserve empty lines
            # Skip comments
            # Skip goto
            if stripped == "" or stripped.startswith("'") or lower.startswith("goto ") or lower.startswith(":"):
                continue
            
            # Skip line continuation lines (ending with '_')
            no_comments = line.split("'")[0].strip()  # Remove comments for keyword checking
            if no_comments.endswith("_"):
                continue
            
            # Skip lines starting with keywords that should not have error handling injected before them
            skip_keywords = (
                "if ",
                "elseif ",
                "else",
                "end if",
                "for ",
                "next",
                "do ",
                "loop",
                "while ",
                "wend",
                "select case",
                "case ",
                "end select",
                "function ",
                "end function",
                "sub ",
                "end sub",
            )
            if stripped.lower().startswith(skip_keywords):
                continue
            
            # Inject after executable statements
            lineNumber = initial_lines.index(line) + 1
            if not has_subs_or_functions:
                processed_lines.append(f"If CheckError(\"{lineNumber}\") Then Exit Sub")
            else:
                # Inject skip command
                processed_lines[-1] = "If NoFatalErrorScriptAI Then " + processed_lines[-1]
                # Inject error check
                processed_lines.append(f"Call CheckError(\"{lineNumber}\")")
                
        if not has_subs_or_functions:
            processed_lines.append("End Sub")
            processed_lines.append("Call Main()")
        code_lines = processed_lines

    return "\n".join(code_lines)

def prepare_python_script(code: str, injectErrorHandling: bool = True) -> str:
    """
    Prepares LPython code for execution in LUSAS by ensuring the engine
    declaration and optionally wrapping user code with traceback reporting.
    """
    code_lines = code.splitlines()

    # Preserve existing engine declarations by commenting them out.
    user_lines = []
    has_engine_declaration = False
    for line in code_lines:
        if line.strip().lower().startswith("$engine="):
            user_lines.append("#" + line)
            has_engine_declaration = True
        else:
            user_lines.append(line)
    user_code = "\n".join(user_lines)
    
    if not injectErrorHandling:
        if has_engine_declaration:
            return f"{user_code}"
        # Try to preserve line order while adding the engine declaration at the top
        if user_code.strip().startswith("\n") or user_code.strip().startswith("#"):
            return f"$ENGINE=LPython {user_code}"
        return f"$ENGINE=LPython\n{user_code}"

    return f'''$ENGINE=LPython
_user_code = {user_code!r}
_print_log_path = getTextWindow().getLogFile() + ".print.log"

# Injected print override
def print(*args, sep=" ", end="\\n", file=None, flush=False):
    if file is not None:
        import builtins
        return builtins.print(*args, sep=sep, end=end, file=file, flush=flush)

    if sep is None:
        sep = " "
    if end is None:
        end = "\\n"

    with open(_print_log_path, "a", encoding="utf-8") as f:
        f.write(sep.join(str(arg) for arg in args) + end)
        if flush:
            f.flush()

try:
    exec(compile(_user_code, "<lusas_mcp_ai_code>", "exec"), globals(), globals())
except Exception as _exc:
    print("ERROR AI Script Python exception: " + str(_exc))
    import traceback
    _traceback = traceback.format_exc()
    print(_traceback)
'''

def cache_script(code: str, extension: str, suffix: str = "", cache_path: str = "LUSAS_MCP_Generated_Scripts") -> str:
    """Caches the given code to a file in the specified cache path and returns the file path."""
    import os

    # Create cache directory if it doesn't exist
    os.makedirs(cache_path, exist_ok=True)

    # Generate a name based on a timestamp (yyyy.mm.dd_hh.mm.ss) to ensure uniqueness
    timestamp = time.strftime("%Y.%m.%d_%H.%M.%S", time.localtime())
    filename = f"AI_script{suffix}_{timestamp}.{extension}"
    file_path = os.path.join(cache_path, filename)
    # Create folder if it doesn't exist
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    # Write code to file if it doesn't already exist
    if not os.path.isfile(file_path):
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(code)

    return file_path

def run_python_script(script_path) -> dict:
    """Runs the given Python script and captures its stdout, stderr, and exit code."""
    try:
        # Run the Python script
        result = subprocess.run(
            ["python", script_path],
            capture_output=True,
            text=True
        )

        # Get stdout and stderr
        stdout_logs = result.stdout
        stderr_logs = result.stderr
        exit_code = result.returncode

        return {
            "exit_code": exit_code,
            "stdout": stdout_logs,
            "stderr": stderr_logs
        }

    except Exception as e:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e)
        }

# --- Images -------------------------------------------------------------

def manual_image_content(value: str) -> ImageContent | None:
    """Extracts image content from a base64-encoded string in the format 'data:image/<type>;base64,<data>'."""

    header, separator, data = value.partition(",")
    if not separator or not header.startswith("data:image/") or not header.endswith(";base64"):
        return None
    image_format = header.removeprefix("data:image/").removesuffix(";base64")
    if not image_format:
        return None

    try:
        img_bytes = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        return None

    img_obj = Image(data=img_bytes, format=image_format)
    return img_obj.to_image_content()

def get_manual_image(image_id: str) -> CallToolResult:
    """Return native MCP image content for an image ID from a manual search hit."""
    import toolsLPI

    value = toolsLPI.get_manual_image_data(image_id)
    if value is not None:
        image_content = manual_image_content(value)
        if image_content is not None:
            return CallToolResult(content=[image_content], isError=False)
    
    return CallToolResult(
        content=[TextContent(type="text", text=f"Manual image not found: {image_id}")],
        isError=True,
    )


# --- Text -------------------------------------------------------------

def first_sentence(text: str) -> str:
    """Extracts the first sentence from a given text string."""
    if not text:
        return ""
    match = re.search(r"(.+?[.!?])(?:\s|$)", text)
    return match.group(1).strip() if match else text.strip()

def get_snippet(text: str, tokens: list[str], context: int = -1) -> str:
    """
    Return a snippet of text surrounding the first occurrence of any token.
    If context is negative, return the full text. If context is zero, return an empty string.
    """
    if not text:
        return ""
    if context == 0:
        return ""

    text = re.sub(r"\s+", " ", text).strip()
    if context < 0:
        return text

    lowered = text.lower()
    positions = [
        position
        for token in tokens
        for position in [lowered.find(token.lower())]
        if position >= 0
    ]
    center = min(positions) if positions else 0
    start = max(0, center - context)
    end = min(len(text), center + context)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet

def text_from_object(value) -> str:
    """Recursively converts a value to a string representation for text content."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(text_from_object(item) for item in value)
    if isinstance(value, dict):
        return " ".join(
            f"{key} {text_from_object(item)}"
            for key, item in value.items()
        )
    return str(value)


# --- Search & Scoring -------------------------------------------------------------

class SearchRecord:
    """Represents a search record with high, mid, and low scoring components."""
    def __init__(self, high: str, mid: str, low: str):
        self.high = high
        self.mid = mid
        self.low = low

    def getAllTokens(self):
        """Return all tokens from all components."""
        return tokenise_text(f"{self.high} {self.mid} {self.low}")

    def getHighTokens(self):
        """Return tokens from high components."""
        return tokenise_text(self.high)

    def getMidTokens(self):
        """Return tokens from mid components."""
        return tokenise_text(self.mid)

    def getLowTokens(self):
        """Return tokens from low components."""
        return tokenise_text(self.low)

    def getSimpleScore(self, tokens: list[str], high_weight: int = 25, mid_weight: int = 8) -> int:
        """Score a record based on the presence of tokens in its high, mid, and low components"""
        if not tokens:
            return 0

        words = self.getAllTokens()
        word_set = set(words)

        high_words = set(self.getHighTokens()) # name, title, id
        mid_words = set(self.getMidTokens()) # category
        low_words = set(self.getLowTokens())

        matched = 0
        score = 0
        for token in tokens:
            # Find exact or plural matches
            body_count = len([word for word in words if token_matches(word, token)])
            if body_count == 0:
                if not any(word.startswith(token) for word in word_set):
                    continue
                body_count = 1
            matched += 1

            # Increase score for exact matches
            score += body_count

            # Hight word matches
            if tokens_match(token, mid_words):
                score += high_weight
            elif any(word.startswith(token) for word in high_words):
                score += high_weight / 2

            # Medium word matches
            if tokens_match(token, mid_words):
                score += mid_weight
            elif any(word.startswith(token) for word in mid_words):
                score += mid_weight / 2

            # Low word matches
            if token in low_words or any(word.startswith(token) for word in low_words):
                score += 1

        if matched == 0:
            return 0

        if len(tokens) > 1 and matched < len(tokens):
            score = max(1, score // (len(tokens) - matched + 2))
        return score

    def getTFIDFScore(
        self,
        tokens: list[str],
        frequencies: dict[str, int | dict[str, int]],
        high_weight: int = 18,
        mid_weight: int = 10,
        require_all_terms: bool = True,
    ) -> int:
        """Score this record with corpus-aware TF-IDF and field bonuses.

        Pass the value returned by :meth:`getTFIDFFrequencies` for the full
        result corpus. Query terms are required to match by default; a caller
        can opt into partial matches for discovery searches. Term frequency is
        taken from the low-priority body field, while high and mid fields
        receive bounded IDF-scaled bonuses.
        """
        if not tokens:
            return 0

        def match_count(words: list[str], token: str) -> int:
            exact_count = words.count(token)
            return exact_count or sum(token_matches(word, token) for word in words)

        def field_tokens(record: "SearchRecord") -> tuple[list[str], list[str], list[str]]:
            return (
                record.getHighTokens(),
                record.getMidTokens(),
                tokenise_text(record.low, unique=False), # allow repeated terms for TF-IDF
            )

        record_count = int(frequencies["record_count"])
        document_frequency = frequencies["document_frequency"]
        if not isinstance(document_frequency, dict):
            raise ValueError("'document_frequency' must be a dictionary of token counts.")
        score = 0.0
        matched_terms = 0
        high_words, mid_words, low_words = field_tokens(self)
        for token in tokens:
            high_count = match_count(high_words, token)
            mid_count = match_count(mid_words, token)
            low_count = match_count(low_words, token)
            if not any((high_count, mid_count, low_count)):
                if require_all_terms:
                    return 0
                continue

            matched_terms += 1
            idf = math.log((record_count + 1) / (int(document_frequency.get(token, 0)) + 1)) + 1
            score += low_count * idf
            score += min(1, high_count) * high_weight * idf
            score += min(2, mid_count) * mid_weight * idf

        if matched_terms == 0:
            return 0
        if matched_terms < len(tokens):
            score /= len(tokens) - matched_terms + 2
        return int(round(score))

    @staticmethod
    def getTFIDFFrequencies(
        records: list["SearchRecord"],
        tokens: list[str],
    ) -> dict[str, int | dict[str, int]]:
        """Return corpus document frequencies needed for TF-IDF scoring.

        A term contributes at most once per record, even when it occurs in
        more than one field or is repeated in the low-priority body field.
        """
        document_frequency = {token: 0 for token in tokens}
        for record in records:
            fields = (
                record.getHighTokens(),
                record.getMidTokens(),
                tokenise_text(record.low, unique=False),
            )
            for token in tokens:
                if any(
                    words.count(token) or any(token_matches(word, token) for word in words)
                    for words in fields
                ):
                    document_frequency[token] += 1

        return {
            "record_count": len(records),
            "document_frequency": document_frequency,
        }

def tokenise_text(
    text: str,
    filter_filler_words: bool = True,
    unique: bool = True,
) -> list[str]:
    """Tokenise text for search, optionally retaining repeated terms for TF-IDF."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if filter_filler_words:
        tokens = [token for token in tokens if token not in _QUERY_FILTERED_WORDS]
    return list(dict.fromkeys(tokens)) if unique else tokens

def singularise_token(token: str) -> str:
    """Return a conservative singular form for common English plurals."""
    if len(token) <= 3 or token.endswith(("ss", "is", "us", "series", "species")):
        return token
    if token.endswith("yses"):
        return f"{token[:-3]}sis"
    if token.endswith("ies"):
        return f"{token[:-3]}y"
    if token.endswith(("ches", "shes", "sses", "xes", "zes")):
        return token[:-2]
    if token.endswith("s"):
        return token[:-1]
    return token

def token_matches(word: str, token: str) -> bool:
    """Match an exact term, an existing prefix match, or a plural variant."""
    return (
        word == token
        or word.startswith(token)
        or singularise_token(word) == singularise_token(token)
    )

def tokens_match(word: str, tokens: list[str]) -> bool:
    """Match an exact term, an existing prefix match, or a plural variant."""
    return any(token_matches(word, token) for token in tokens)


# --- Files -------------------------------------------------------------

def file_created_at(stat_result) -> str | None:
    """Return the Windows file-creation time as an ISO 8601 timestamp."""
    if os.name != "nt":
        return None
    return datetime.fromtimestamp(stat_result.st_ctime, timezone.utc).isoformat().replace("+00:00", "Z")
