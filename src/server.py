# ---------------------------------------------------------------------------
# LUSAS MCP Server
# Description:  The main server module for the LUSAS MCP (Model Creation Protocol) server.
#               This module handles the connection to LUSAS, manages the model, and provides the available interface commands.
# Copyright (c) 2026 Finite Element Analysis Ltd.
# All rights reserved.
# ---------------------------------------------------------------------------

import logging, os, argparse
from urllib.parse import unquote

from mcp.server.fastmcp import FastMCP
from mcp.types import Icon

from config import *
from LusasClient import *

from toolsResponses import *
import toolsValidate

# Tools and resources
import toolsLPI, toolsModeller, toolsHelpers

# --- Functions -------------------------------------------------------------

def safe_tool_wrapper(fn):
    """Wraps a tool function passing the LUSAS COM wrapper."""
    return toolsValidate.safe_tool_wrapper(fn, global_lusas=lusas)


# --- Main -------------------------------------------------------------

# Parse before importing server modules so --help exits without initialization.
parser = argparse.ArgumentParser(description="Run the LUSAS MCP server.")
parser.add_argument(
    "--config-filepath",
    dest="config_filepath",
    help="Filepath of a config.json file (defaults to src/config.json).",
)
args = parser.parse_args() if __name__ == "__main__" else parser.parse_args([])

# Load configuration and setup logging
config = Config(args.config_filepath)
try:
    config.validate()
except ValueError as exc:
    parser.error(str(exc))
toolsHelpers.configure_logging(config.logFilepath)
toolsLPI.configure_data(config)

logger = logging.getLogger('lusas_mcp_server')
logger.info("Config loaded from %s", config.filepath)
logger.info("Target LUSAS version is %s", config.lusasVersion)

# Load MCP instructions from file
logger.info("Loading instructions for LUSAS MCP server...")
_instructions_path = os.path.join(os.path.dirname(__file__), 'data', 'instructions.txt')
with open(_instructions_path, 'r', encoding='utf-8') as _f:
    instruction = _f.read()

# Initialize FastMCP server
logger.info("Initializing FastMCP server with loaded instructions...")
icon_path = os.path.join(os.path.dirname(__file__), '..', 'icon.png') # Get icon from the parent directory
icon = Icon(src=icon_path, mimeType="image/png", sizes=["48x48"])
mcp = FastMCP("lusas", instructions=instruction, dependencies=["pywin32"], icons=[icon])


# --- Register Static Resources ---
@mcp.resource("config://app")
def get_config() -> dict:
    """Static server configuration data"""
    return config.data

# --- Register Modeller MCP tools ---
lusas = LusasClient(config)

logger.info(f"Registering Modeller tools...")

get_connection_status = mcp.tool("Get_LUSAS_Status", "Get LUSAS Status", "Check the connection status with LUSAS")(safe_tool_wrapper(toolsModeller.get_connection_status))

get_model_overview = mcp.tool("Get_Model_Overview", "Get Model Overview", "Retrieve a LUSAS model overview with summaries; optional detail flags expand attributes, analyses, loadsets, and groups")(safe_tool_wrapper(toolsModeller.get_model_overview))

get_model_backups = mcp.tool("Get_Model_Backups", "Get Model Backups", "List automatic backup files for the open saved model, including file creation time when available.")(safe_tool_wrapper(toolsModeller.get_model_backups))

search_section_libraries = mcp.tool("Search_Section_Libraries", "Search Section Libraries", "Search the active section libraries with free text across library name, section type, and section name. Use a query such as 'UK RHS 500x200x8'.")(safe_tool_wrapper(toolsModeller.search_section_libraries))


# Modeller internal code execution - faster but worse error handling + all messages are shown in LUSAS
if not config.readOnlyMode:

    load_model_backup = mcp.tool("Load_Model_Backup", "Load Model Backup", "Close the active saved model, copy a named automatic backup beside it, and open the copied backup. Refuses unsaved changes and existing destination files unless explicitly handled.")(safe_tool_wrapper(toolsModeller.load_model_backup))

    save_model = mcp.tool("Save_Model", "Save Model", "Save the open model. Optionally provide save_location to save under a new path; analysis results will be closed when saving as.")(safe_tool_wrapper(toolsModeller.save_model))

    close_model = mcp.tool("Close_Model", "Close Model", "Close the open model. Set save_before_closing to save first; if it is not saved, all changes will be lost.")(safe_tool_wrapper(toolsModeller.close_model))

    close_lusas = mcp.tool("Close_LUSAS", "Close LUSAS", "Close LUSAS. Any open model must already be saved or closed.")(safe_tool_wrapper(toolsModeller.close_lusas))
    
    # For LUSAS v22+, use LPython for better error handling
    if config.lusasVersionFloat >= 23.0:
        logger.info(f"execute_code set to Python execution...")
        execute_code = mcp.tool("LUSAS_Code_Interaction", "LUSAS Code Interaction", "Interact with LUSAS through API Python execution")(safe_tool_wrapper(toolsModeller.execute_python_code))
    else:
        logger.info(f"execute_code set to VBScript execution...")
        execute_code = mcp.tool("LUSAS_Code_Interaction", "LUSAS Code Interaction", "Interact with LUSAS through API VBScript execution")(safe_tool_wrapper(toolsModeller.execute_vbscript_code))
    
    # External python execution - Does not work, permissions issue?
    #execute_code = mcp.tool("LUSAS_Code_Interaction", "LUSAS Code Interaction", "Interact with LUSAS through API code execution")(safe_tool_wrapper(toolsModeller.execute_code_external))

    #set_ui_state = mcp.tool("Set_UI_State", "LUSAS UI Interaction", "Set the state of the LUSAS user interface")(safe_tool_wrapper(toolsModeller.set_ui_state))


# --- Register Data MCP tools ---
logger.info(f"Registering LPI tools...")

# LPI Commands
search_lpi_commands = mcp.tool("Search_LPI_Commands", "Search LPI commands", "Return compact LPI command matches. Use Get LPI Command Details for selected commands and Find LPI Examples for reusable workflows.")(toolsLPI.search_lpi_commands)

get_lpi_command_details = mcp.tool("Get_LPI_Command_Details", "Get LPI Command Details", "Return complete documentation for the given LPI command IDs.")(toolsLPI.get_lpi_command_details)


# LPI Examples
search_lpi_examples = mcp.tool("Search_LPI_Examples", "Search LPI Examples", "Return compact bundled-example matches for the configured LUSAS version. Use Get LPI Example to retrieve selected source code.")(toolsLPI.search_lpi_examples)

get_lpi_example = mcp.tool("Get_LPI_Example", "Get LPI Example", "Return one selected bundled LPI example in the language supported by the configured LUSAS version.")(toolsLPI.get_lpi_example)


# Material Library
search_materials_library = mcp.tool("Search_Materials_Library", "Search Materials Library", "Search the bundled materials library with free text across material, region, standard, and grade. Use a query such as 'Concrete Europe EN1992 C40'.")(toolsLPI.search_materials_library)


# Elements
search_elements = mcp.tool("Search_Elements", "Search Elements", "Search supported LUSAS element names for API inputs, with family, feature type, and stress variation metadata")(toolsLPI.search_elements)

get_element_details = mcp.tool("Get_Element_Details", "Get Element Details", "Return complete documentation for one or more exact element names.")(toolsLPI.get_element_details)


# --- Register Manual/Help MCP tools ---
if config.allowManualImagesReturn:
    logger.info(f"Registering manual search tools (with images)...")
    search_manual = mcp.tool("Search_Manual", "Search Manual", "Search the LUSAS manual index")(toolsLPI.search_manual)
    get_manual_entries_by_toc_path = mcp.tool("Get_Manual_Entries_By_TOC_Path", "Get Manual Entries By TOC Path", "Retrieve manual entries whose toc_path matches the specified ordered values")(toolsLPI.get_manual_entries_by_toc_path)
    get_manual_image = mcp.tool("Get_Manual_Image", "Get Manual Image", "Return a manual image by the image ID from Search Manual")(toolsHelpers.get_manual_image)

else:
    logger.info(f"Registering manual search tools (without images)...")
    search_manual = mcp.tool("Search_Manual", "Search Manual", "Search the LUSAS manual index")(toolsLPI.search_manual_no_images)
    get_manual_entries_by_toc_path = mcp.tool("Get_Manual_Entries_By_TOC_Path", "Get Manual Entries By TOC Path", "Retrieve manual entries whose toc_path matches the specified ordered values")(toolsLPI.get_manual_entries_by_toc_path_no_images)


# --- Register MCP Resources ---

logger.info(f"Registering LPI resources...")

@mcp.resource("lusas://lpi/{identifier}")
def get_lpi_command_resource(identifier: str) -> dict:
    """Complete LPI documentation for one command ID."""
    return toolsLPI.get_lpi_command_details([unquote(identifier)])

@mcp.resource("lusas://examples/index")
def get_lpi_examples_index_resource() -> dict:
    """Compact index of all bundled LPI examples, without source code."""
    return toolsLPI.get_lpi_examples_index()

@mcp.resource("lusas://examples/{identifier}")
def get_lpi_example_resource(identifier: str) -> dict:
    """One complete bundled LPI example in the configured language."""
    return toolsLPI.get_lpi_example(unquote(identifier))

@mcp.resource("lusas://sections/index")
def get_lusas_section_categories() -> dict:
    """Available active-model section-library categories."""
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error
    try:
        libraries = list(lusas.modeller.getSectionLibraries())
        return ok({"libraries": libraries, "total": len(libraries)})
    except Exception as e:
        logger.error("Error retrieving section-library categories: %s", e)
        return error("section_category_index_failed", "Failed to retrieve section-library categories.", {"exception": str(e)})


@mcp.resource("lusas://sections/{library}")
def get_lusas_section_types(library: str) -> dict:
    """Available types for one exact active-model section-library category."""
    library = unquote(library)
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error
    try:
        if library not in lusas.modeller.getSectionLibraries():
            section_types = None
        else:
            section_types = {"library": library, "types": list(lusas.modeller.getSectionTypes(library))}
    except Exception as e:
        logger.error("Error retrieving section-library types: %s", e)
        return error("section_type_index_failed", "Failed to retrieve section-library types.", {"exception": str(e)})
    if section_types is None:
        return {"ok": False, "error": {"code": "not_found", "message": f"Section library '{library}' not found.", "details": {"library": library}}}
    return {"ok": True, "data": section_types}


@mcp.resource("lusas://sections/{library}/{section_type}")
def get_lusas_section_names(library: str, section_type: str) -> dict:
    """All section names for one exact active-model library and section type."""
    library, section_type = map(unquote, (library, section_type))
    connection_error = lusas.require_connection()
    if connection_error:
        return connection_error
    try:
        if library not in lusas.modeller.getSectionLibraries():
            return {"ok": False, "error": {"code": "not_found", "message": f"Section library '{library}' not found.", "details": {"library": library}}}
        if section_type not in lusas.modeller.getSectionTypes(library):
            return {"ok": False, "error": {"code": "not_found", "message": f"Section type '{section_type}' not found in library '{library}'.", "details": {"library": library, "section_type": section_type}}}
        names = list(lusas.modeller.getSectionNames(library, section_type))
        return {"ok": True, "data": {"library": library, "section_type": section_type, "names": names, "total": len(names)}}
    except Exception as e:
        logger.error("Error retrieving section-library names: %s", e)
        return error("section_name_index_failed", "Failed to retrieve section-library names.", {"exception": str(e)})


# Initialize and run the server
if __name__ == "__main__":
    mcp.run(transport='stdio')
