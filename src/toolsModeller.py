# ---------------------------------------------------------------------------
# Modeller Tools
# Description:  Functions that connect on LUSAS Modeller called from MCP tools.
# Copyright (c) 2026 Finite Element Analysis Ltd.
# All rights reserved.
# ---------------------------------------------------------------------------

import os, sys, logging, tempfile, subprocess, shutil
import toolsHelpers
from LusasClient import (
    LusasClient,
    _analysis_type_to_string,
    _loadset_type_to_string,
    _safe_com_call,
)
from toolsResponses import *
from toolsValidate import *


# --- Functions -------------------------------------------------------------

# This function can be called as a tool through execute_python_code() to force the use
# of Python because if have better error handling and traceback reporting than VBScript.
def _execute_code(lusas: LusasClient, code: str, language: str = "Python") -> dict:
    """Execute arbitrary code in the connected LUSAS instance.
    Parameters:
    code (str): The code to execute.
    language (str): The programming language of the code ("Python" or "VBScript"). Use Python unless you have a specific reason to use VBScript.
    """
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error

    if language.lower() not in {"vbscript", "python"}:
        return error("validation_error", "Unsupported language. Only 'VBScript' and 'Python' are supported.", {"language": language})

    # Prepare code
    raw_code = code
    if language.lower() == "vbscript":
        code = toolsHelpers.prepare_vbscript(code, injectErrorHandling=True)
    else:
        code = toolsHelpers.prepare_python_script(code, injectErrorHandling=True)

    filename = "ai_code.lpy" if language.lower() == "python" else "ai_code.lvb"

    # Save script in cache
    if lusas.config.saveGeneratedScripts:
        extension = filename.split('.')[-1]
        toolsHelpers.cache_script(code, extension)
        toolsHelpers.cache_script(raw_code, extension, suffix="_raw")

    with tempfile.TemporaryDirectory() as temp_dir:
        # Export code to a temporary file and execute it via LUSAS API to avoid issues with multi-line code execution
        temp_path = os.path.join(temp_dir, filename)
        
        with open(temp_path, 'w') as f:
            f.write(code)

        # get full path to temp file and pass to LUSAS
        temp_path = os.path.abspath(temp_path)
        temp_log_path = os.path.join(temp_dir, f"{filename}.log")
        
        logger.debug(f"Script and log paths: {temp_path}, {temp_log_path}")
        logger.debug(f"Code: {code}")


        # Set message suppression to avoid popups for errors or other messages in the executed code
        #supMsgCode = lusas.modeller.getMessageSuppression()
        #lusas.modeller.suppressMessages(2) # does not work
        # Attach log file
        txtWin = lusas.modeller.getTextWindow()
        txtWin.attachLogFile(temp_log_path, False) # openPrintFile

        # Execute script
        errorMsg = ""
        try:
            lusas.modeller.fileOpen(temp_path)
        except Exception as e:
            errorMsg = str(e)

        # Detach log file and restore message suppression
        txtWin.closeLogFile()
        #lusas.modeller.suppressMessages(supMsgCode)

        # Read log file
        log_content = ""
        with open(temp_log_path, 'r') as log_file:
            log_content = log_file.read()

            # Remove first line if it ends with 'log" attached'
            log_in_lines = log_content.splitlines()
            if len(log_in_lines) > 0 and log_in_lines[0].endswith('log" attached'):
                log_content = log_in_lines[1:]
                log_content = "\n".join(log_content)
        logger.debug(f"Log content: {log_content}")
        
        if language.lower() == "vbscript":
            # Check for common error indicators in the log content
            for line in log_in_lines:
                if line.startswith("ERROR "):
                    errorMsg = "Error from script error handling."
                    break

            if errorMsg == "":
                return ok({
                    "message": "Code executed successfully.",
                    "log": log_content
                    })
            else:
                # Save errors in file
                if lusas.config.saveGeneratedScripts:
                    error_log = f"===== Log content =====\n{log_content}"
                    toolsHelpers.cache_script(error_log, "log", suffix=".error")
                # Return error
                return error(
                    "execution_failed", "Executed code reported errors.", 
                    {"log": log_content}
                )
        else:
            # Python
            # Read print log file (if it exists) and check for errors
            print_content = ""
            if os.path.exists(temp_log_path + ".print.log"):
                with open(temp_log_path + ".print.log", 'r') as print_file:
                    print_content = print_file.read()
                    logger.debug(f"Print content: {print_content}")

                    print_in_lines = print_content.splitlines()

                    # Check for common error indicators in the log content
                    for line in print_in_lines:
                        if line.startswith("ERROR "):
                            errorMsg = "Error from script error handling."
                            break

            if errorMsg == "":
                return ok({
                    "message": "Code executed successfully.",
                    "log": log_content,
                    "print": print_content
                    })
            else:
                # Save errors in file
                if lusas.config.saveGeneratedScripts:
                    error_log = f"===== Log content =====\n{log_content}\n\n===== Print content =====\n{print_content}"
                    toolsHelpers.cache_script(error_log, "log", suffix=".error")
                # Return error
                return error(
                    "execution_failed", "Executed code reported errors.", 
                    {"log": log_content, "print": print_content}
                )


# --- Overview Tools -------------------------------------------------------------

def get_connection_status(lusas: LusasClient) -> dict:
    """Get current LUSAS connection and model status."""
    connected = lusas._set_modeller()
    if not connected:
        return {
            "connected": False,
            "version": lusas.versionString,
            "model_open": False,
            "model_name": None,
        }

    model_open = False
    model_name = None
    try:
        modeller = lusas.modeller
        model_open = modeller.existsDatabase()
        if model_open:
            model_name = modeller.db().getDBBasename()
    except Exception as e:
        logger.warning(f"Failed to query model status: {str(e)}")

    return {
        "connected": True,
        "version": lusas.versionString,
        "model_open": model_open,
        "model_name": model_name
    }

def get_model_overview(
    lusas: LusasClient,
    include_attribute_details: bool = False,
    include_analysis_details: bool = False,
    include_loadset_details: bool = False,
    include_group_details: bool = False,
) -> dict:
    """
    Returns an overview of the currently open model.

    The default response includes compact summaries for attributes, analyses,
    loadsets and groups. Detail collections are opt-in because they can be slow
    and token-heavy in large models.
    """
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error

    try:
        modeller = lusas.modeller
        if not modeller.existsDatabase():
            return error("not_found", "No model is currently open.", {})

        db = modeller.db()
        diagnostics = {}
        model_filename = db.getDBFilename()
        is_modified = db.isModified()
        data = {
            "model_name": db.getDBBasename(),
            "model_filepath": model_filename or None,
            "modified_since_saved": bool(is_modified) if is_modified is not None else None,
            "current_working_directory": modeller.getCWD(),
            "analysis_category": db.getAnalysisCategory() if hasattr(db, "getAnalysisCategory") else None,
            "vertical_dir": db.getVerticalDir() if hasattr(db, "getVerticalDir") else None,
            "units": db.getModelUnits().getName(),
            "geometries": {
                "Points" : db.count("Points"),
                "Lines" : db.count("Lines"),
                "Surfaces" : db.count("Surfaces"),
                "Volumes" : db.count("Volumes")
            },
        }
        
        try:
            attrs = list(db.getAttributes("All"))
            data["attributes"] = {
                "count": len(attrs),
                "items": [
                    {
                        "id": _safe_com_call(attr, "getID"),
                        "name": _safe_com_call(attr, "getName"),
                        "attribute_type": _safe_com_call(attr, "getAttributeType"),
                    }
                    for attr in attrs
                ],
            }
            if include_attribute_details:
                data["attribute_details"] = [lusas._attribute_info(attr) for attr in attrs]
        except Exception as e:
            logger.warning(f"Failed to read model attributes for overview: {str(e)}")
            diagnostics["attributes_error"] = str(e)

        try:
            analyses = list(db.getAnalyses())
            analysis_items = []
            for analysis in analyses:
                analysis_type = _safe_com_call(analysis, "getType")
                needs_solving = _safe_com_call(analysis, "needsSolving", True, None)
                analysis_items.append(
                    {
                        "name": _safe_com_call(analysis, "getName"),
                        "analysis_type": _analysis_type_to_string(analysis_type) if analysis_type is not None else None,
                        "solved": not needs_solving if needs_solving is not None else "n/a",
                    }
                )
            data["analyses"] = {
                "count": len(analyses),
                "items": analysis_items,
            }
            if include_analysis_details:
                data["analysis_details"] = [lusas._analysis_info(analysis) for analysis in analyses]
        except Exception as e:
            logger.warning(f"Failed to read model analyses for overview: {str(e)}")
            diagnostics["analyses_error"] = str(e)

        try:
            loadsets = list(db.getLoadsets("all"))
            loadset_items = []
            for loadset in loadsets:
                loadset_type = _safe_com_call(loadset, "getTypeCode")
                loadset_items.append(
                    {
                        "id": _safe_com_call(loadset, "getID"),
                        "name": _safe_com_call(loadset, "getName"),
                        "loadset_type": _loadset_type_to_string(loadset_type) if loadset_type is not None else None,
                    }
                )
            data["loadsets"] = {
                "count": len(loadsets),
                "items": loadset_items,
            }
            if include_loadset_details:
                data["loadset_details"] = [lusas._loadset_info(loadset) for loadset in loadsets]
        except Exception as e:
            logger.warning(f"Failed to read model loadsets for overview: {str(e)}")
            diagnostics["loadsets_error"] = str(e)

        try:
            # Skip any group whose name is "Group"
            groups = [
                group
                for group in db.getObjects("Groups")
                if _safe_com_call(group, "getName") != "Group"
            ]
            data["groups"] = {
                "count": len(groups),
                "items": [
                    {
                        "name": _safe_com_call(group, "getName"),
                        "geometries": {
                            "points": _safe_com_call(group, "count", "Points"),
                            "lines": _safe_com_call(group, "count", "Lines"),
                            "surfaces": _safe_com_call(group, "count", "Surfaces"),
                            "volumes": _safe_com_call(group, "count", "Volumes"),
                        },
                    }
                    for group in groups
                ],
            }
            if include_group_details:
                data["group_details"] = [lusas._group_info(group) for group in groups]
        except Exception as e:
            logger.warning(f"Failed to read model groups for overview: {str(e)}")
            diagnostics["groups_error"] = str(e)

        data["diagnostics"] = diagnostics
        return ok(data)
    except Exception as e:
        logger.error(f"Error getting model overview: {str(e)}")
        return error("failed", "Failed to get model overview.", {"exception": str(e)})


# --- Save, Close Tools -------------------------------------------------------------

def save_model(lusas: LusasClient, save_location: str | None = None) -> dict:
    """Save the open model, optionally under a new absolute file path."""
    if save_location is not None:
        if not isinstance(save_location, str) or not save_location.strip():
            return error("validation_error", "'save_location' must be a non-empty absolute file path when provided.", {})
        save_location = save_location.strip()
        if not os.path.isabs(save_location):
            return error("validation_error", "'save_location' must be an absolute file path.", {"save_location": save_location})

    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error

    try:
        modeller = lusas.modeller
        if not modeller.existsDatabase():
            return error("not_found", "No model is currently open.", {})

        previous_location = modeller.db().getDBFilename()

        project = modeller.getProject()
        if save_location:
            project.saveAs(save_location)
        else:
            project.save()

        return ok({
            "model_name": modeller.db().getDBBasename(),
            "saved_to": save_location or modeller.db().getDBFilename() or previous_location,
            "saved_as": bool(save_location),
        })
    except Exception as e:
        logger.error("Error saving model: %s", e)
        return error("failed", "Failed to save the open model.", {"exception": str(e)})

def close_model(lusas: LusasClient, save_before_closing: bool = False) -> dict:
    """Close the open model, optionally saving it first."""
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error

    try:
        modeller = lusas.modeller
        if not modeller.existsDatabase():
            return error("not_found", "No model is currently open.", {})

        was_modified: bool = modeller.db().isModified()

        project = modeller.getProject()
        if save_before_closing:
            project.save()
        project.close()

        return ok({
            "message": "Model closed successfully.",
            "discarded_unsaved_changes": bool(was_modified and not save_before_closing),
        })
    except Exception as e:
        logger.error("Error closing model: %s", e)
        return error("failed", "Failed to close the open model.", {"exception": str(e)})

def close_lusas(lusas: LusasClient) -> dict:
    """Close LUSAS only when there is no open unsaved model."""
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error

    try:
        modeller = lusas.modeller
        if modeller.existsDatabase():
            model_location: str = modeller.db().getDBFilename()
            is_modified: bool = modeller.db().isModified()
            if not model_location or is_modified:
                return error("model_not_saved", "The open model must be saved or closed before closing LUSAS.")

        modeller.quit(False)
        return ok({"message": "LUSAS closed successfully."})
    except Exception as e:
        logger.error("Error closing LUSAS: %s", e)
        return error("failed", "Failed to close LUSAS.", {"exception": str(e)})


# --- Backup Tools -------------------------------------------------------------

def get_model_backups(lusas: LusasClient) -> dict:
    """List automatic backup model files for the currently open saved model."""
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error

    try:
        modeller = lusas.modeller
        if not modeller.existsDatabase():
            return error("not_found", "No model is currently open.", {})

        db = modeller.db()
        model_name = db.getDBBasename()
        model_filename = db.getDBFilename()
        if not model_filename:
            return error(
                "not_found",
                "The open model has not been saved, so no backups are available.",
                {"model_name": model_name},
            )

        db_folder = modeller.getSystemString("DBFolder")
        if not db_folder:
            return error("failed", "LUSAS has no dedicated folder for the open model.")
        backup_directory = os.path.join(db_folder, "Backups")

        backups = []
        if os.path.isdir(backup_directory):
            with os.scandir(backup_directory) as entries:
                for entry in entries:
                    if (
                        not entry.is_file()
                        or not entry.name.casefold().endswith(".mdl")
                        or not entry.name.casefold().startswith(model_name.casefold())
                    ):
                        continue
                    stat_result = entry.stat()
                    backups.append({
                        "filename": entry.name,
                        "created_at": toolsHelpers.file_created_at(stat_result),
                    })

        backups.sort(key=lambda backup: backup["filename"].casefold())
        return ok({
            "model_filepath": model_filename,
            "backup_directory": backup_directory,
            "count": len(backups),
            "backups": backups,
        })
    except OSError as e:
        logger.warning("Failed to inspect model backup directory: %s", e)
        return error("failed", "Failed to inspect the model backup directory.", {"exception": str(e)})
    except Exception as e:
        logger.error("Error getting model backups: %s", e)
        return error("failed", "Failed to get model backups.", {"exception": str(e)})

def load_model_backup(lusas: LusasClient, backup_filename: str, discard_unsaved_changes: bool = False) -> dict:
    """Open a copied automatic backup while preserving the original model file."""
    if not isinstance(backup_filename, str) or not backup_filename.strip():
        return error("validation_error", "'backup_filename' must be a non-empty filename.", {})

    backup_filename = backup_filename.strip()
    if backup_filename != os.path.basename(backup_filename):
        return error("validation_error", "'backup_filename' must not include a path.", {"backup_filename": backup_filename})
    if not backup_filename.casefold().endswith(".mdl"):
        backup_filename = backup_filename + ".mdl"

    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error

    try:
        modeller = lusas.modeller
        if not modeller.existsDatabase():
            return error("not_found", "No model is currently open.", {})

        db = modeller.db()
        model_name = db.getDBBasename()
        model_filename = db.getDBFilename()
        if not model_filename:
            return error("not_found", "The open model has not been saved.")

        if db.isModified() and not discard_unsaved_changes:
            return error("unsaved_changes", "The open model has unsaved changes. Save or discard them before loading a backup.")

        db_folder = modeller.getSystemString("DBFolder")
        source_path = os.path.join(db_folder, "Backups", backup_filename)
        destination_path = os.path.join(os.path.dirname(model_filename), backup_filename)
        if not os.path.isfile(source_path):
            return error("not_found", "The requested backup file was not found.")
        if os.path.exists(destination_path):
            return error(
                "already_exists",
                "A file with the backup name already exists beside the model.",
                {"destination_filepath": destination_path},
            )

        try:
            modeller.getProject().close()
        except Exception as e:
            return error("failed", "Failed to close the active model.", {"exception": str(e)})

        try:
            shutil.copy2(source_path, destination_path)
        except OSError as e:
            return error(
                "failed",
                "The active model was closed, but the backup could not be copied.",
                {"source_filepath": source_path, "destination_filepath": destination_path, "exception": str(e)},
            )

        try:
            modeller.openProject(destination_path, False)
        except Exception as e:
            return error(
                "failed",
                "The backup was copied, but LUSAS could not open it.",
                {"backup_filepath": destination_path, "exception": str(e)},
            )

        return ok({
            "model_name": model_name,
            "filepath": destination_path,
            "discarded_unsaved_changes": bool(discard_unsaved_changes),
        })
    except Exception as e:
        logger.error("Error loading model backup: %s", e)
        return error("failed", "Failed to load model backup.", {"exception": str(e)})


# --- Code execution Tools -------------------------------------------------------------

def execute_python_code(lusas: LusasClient, code: str) -> dict:
    """
    Execute arbitrary python code in the connected LUSAS instance.
    Do not use commands that are not documented in the LUSAS API. Use the tool search_lpi_commands() to find available commands.
    Parameters:
    code (str): The code to execute. 
    """
    # Ensure language is not vbscript
    code_lines = code.splitlines()
    for i in range(0, len(code_lines)):
        t_line = code_lines[i].strip().lower()
        if t_line.startswith("$engine=vbscript") or t_line.startswith("'"):
            return error("validation_error", "VBScript code execution is not supported. Please use Python code.")
    return _execute_code(lusas, code, "Python")

def execute_vbscript_code(lusas: LusasClient, code: str) -> dict:
    """
    Execute arbitrary VBScript code in the connected LUSAS instance.
    Do not use commands that are not documented in the LUSAS API. Use the tool search_lpi_commands() to find available commands.
    Parameters:
    code (str): The code to execute. 
    """
    # Ensure language is not python
    code_lines = code.splitlines()
    for i in range(0, len(code_lines)):
        t_line = code_lines[i].strip().lower()
        if t_line.startswith("$engine=lpython") or t_line.startswith("#"):
            return error("validation_error", "Python code execution is not supported. Please use VBScript code.")
    return _execute_code(lusas, code, "VBScript")


# --- Search Libraries Tools -------------------------------------------------------------

def search_section_libraries(lusas: LusasClient, query: str, max_results: int = 10) -> dict:
    """Search the active LUSAS section libraries with free text.

    ``query`` is matched case-insensitively across library name, section type,
    and section name (for example ``"UK RHS 500x200x8"``).
    ``max_results`` defaults to 10.
    """
    if not isinstance(query, str) or not query.strip():
        return error("validation_error", "'query' must be a non-empty string.", {"query": query})

    max_results = max(1, max_results)
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error

    try:
        libraries: list[str] = lusas.modeller.getSectionLibraries()
        tokens = toolsHelpers.tokenise_text(query)
        entries = []
        for lib in libraries:
            types_list: list[str] = lusas.modeller.getSectionTypes(lib)
            for section_type in types_list:
                for section_name in lusas.modeller.getSectionNames(lib, section_type):
                    entries.append(
                        {
                            "library": lib,
                            "type": section_type,
                            "section": section_name,
                            "record": toolsHelpers.SearchRecord(section_name, f"{lib} {section_type}", ""),
                        }
                    )

        frequencies = toolsHelpers.SearchRecord.getTFIDFFrequencies(
            [entry["record"] for entry in entries],
            tokens,
        )
        scored = []
        for entry in entries:
            score = entry["record"].getTFIDFScore(tokens, frequencies, require_all_terms=False)
            if score <= 0:
                continue
            scored.append({
                "library": entry["library"],
                "type": entry["type"],
                "section": entry["section"],
                "score": score,
            })

        scored.sort(key=lambda hit: (-hit["score"], hit["section"], hit["library"], hit["type"]))
        return ok({
            #"query": query,
            "tokens": tokens,
            "hits": scored[:max_results],
            "total": len(scored),
            "truncated": len(scored) > max_results,
        })
    except Exception as e:
        logger.error(f"Error searching section libraries: {str(e)}")
        return error("section_search_failed", "Failed to search section libraries.", {"exception": str(e)})


# --- Functions (not used) -------------------------------------------------------------

def set_ui_state(lusas: LusasClient, visible: bool = True, enable_ui: bool = True) -> dict:
    """Control LUSAS UI visibility and responsiveness."""
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error

    try:
        lusas.modeller.setVisible(visible)
        lusas.modeller.enableUI(enable_ui)
        return ok({"visible": visible, "enable_ui": enable_ui, "message": "LUSAS UI updated."})
    except Exception as e:
        logger.error(f"Error setting UI state: {str(e)}")
        return error("ui_update_failed", "Failed to update UI state.", {"exception": str(e)})

# TODO: This is not working - code is not executing
def execute_code_external(lusas: LusasClient, code: str, timeout_seconds: int = 30) -> dict:
    """Execute Python code in an external Python process connected to LUSAS via COM.

    The supplied code is run by the current Python interpreter in an external
    worker process, not inside the LUSAS scripting engine. The generated script
    imports pywin32, attaches to the running LUSAS Modeller COM object, and
    exposes ``modeller`` plus ``db`` to the supplied code.

    For faster and execution, wrap database changes in:
    db.beginCommandBatch("AI Task - description")
    code that modifies the database
    db.closeCommandBatch("AI Task - description")
    """
    if not isinstance(code, str) or len(code.strip()) == 0:
        return error("validation_error", "'code' must be a non-empty string.", {"code": code})
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        return error(
            "validation_error",
            "'timeout_seconds' must be a positive integer.",
            {"timeout_seconds": timeout_seconds},
        )

    if ".beginCommandBatch(" in code and ".closeCommandBatch(" not in code:
        return error(
            "validation_error",
            "If 'code' contains 'IFDatabase.beginCommandBatch()', it must also contain 'IFDatabase.closeCommandBatch()' to avoid leaving the database in a locked state.",
            {"code": code},
        )

    version = lusas.versionString
    script = f"""# Auto-generated by LUSAS MCP.
import sys
import traceback

pythoncom = None
modeller = None
db = None
exit_code = 0
user_code = {code!r}

try:
    import pythoncom
    import win32com.client as win32client

    pythoncom.CoInitialize()
    modeller = win32client.GetActiveObject("Lusas.Modeller.{version}")
    db = modeller.db() if modeller.existsDatabase() else None

    exec(compile(user_code, "<lusas_mcp_ai_code>", "exec"), globals(), globals())
except Exception:
    traceback.print_exc()
    exit_code = 1
finally:
    if db is not None:
        try:
            db.closeCommandBatch()
        except Exception:
            pass
    if pythoncom is not None:
        pythoncom.CoUninitialize()

sys.exit(exit_code)
"""

    if lusas.config.saveGeneratedScripts:
        toolsHelpers.cache_script(script, "py")
        toolsHelpers.cache_script(code, "py", suffix="_raw")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = os.path.abspath(os.path.join(temp_dir, "ai_external_code.py"))
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(script)

        logger.debug(f"External Python script path: {temp_path}")
        logger.debug(f"External Python code: {code}")

        python_executable = sys.executable or "python"
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                [python_executable, "-u", temp_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=temp_dir,
                creationflags=creation_flags,
            )
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                pass
            try:
                process.kill()
            except Exception:
                pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except Exception:
                stdout, stderr = "", ""
            return error(
                "execution_timeout",
                f"External Python code exceeded timeout of {timeout_seconds} seconds.",
                {
                    "python_executable": python_executable,
                    "pid": process.pid,
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                    "timeout_seconds": timeout_seconds,
                },
            )
        except Exception as e:
            logger.error(f"Error running external Python code: {str(e)}")
            return error(
                "execution_failed",
                "Failed to run external Python code.",
                {"exception": str(e), "python_executable": python_executable},
            )

    data = {
        "message": "External Python code executed successfully.",
        "python_executable": python_executable,
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
    }
    if returncode == 0:
        return ok(data)

    return error(
        "execution_failed",
        "External Python code exited with a non-zero return code.",
        data,
    )


# --- Main -------------------------------------------------------------

# Module-local logger
logger = logging.getLogger('lusas_mcp_server')
