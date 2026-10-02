# ---------------------------------------------------------------------------
# LUSAS Client
# Description:  This module provides a Python interface to interact with the LUSAS Modeller using COM.
# Copyright (c) 2026 Finite Element Analysis Ltd.
# All rights reserved.
# ---------------------------------------------------------------------------

import sys, logging
from config import *
from toolsResponses import *
import win32com.client as win32client
from win32com.client import CDispatch as IFModeller


# --- Functions -------------------------------------------------------------

def _loadset_type_to_string(type_id: int) -> str:
    """Helper function to get loadset type as a string."""
    type_mapping = {
        0: "Loadcase",
        1: "Results Loadcase",
        2: "Basic combination",
        3: "Envelope",
        4: "Fatigue Loadcase",
        5: "IMD Loadcase",
        6: "Smart Combination",
        7: "Standard Load Curve",
        8: "Variation Load Curve",
        9: "Table Load Curve",
        11: "Target Value Loadcase",
        12: "Cable Tuning Loadcase",
        13: "DMI Influence Results Loadcase",
        14: "Target Values Loadcase",
        15: "Non-Linear Cable Tuning Loadcase",
        16: "RC Design Results Container",
        17: "Slab Design Results Container",
        18: "Results Container"
    }
    return type_mapping.get(type_id, f"Unknown({type_id})")

def _analysis_type_to_string(type_id: int) -> str:
    """Helper function to get analysis type as a string."""
    type_mapping = {
        1: "Structural",
        2: "Thermal",
        6: "Direct method influence",
        7: "Reciprocal theorem influence",
        10: "VLO",
        12: "Cable tuning"
    }
    return type_mapping.get(type_id, f"Unknown({type_id})")

def _safe_com_call(obj, method_name: str, *args, default=None):
    """Call an optional COM method without failing the whole tool response."""
    try:
        if not hasattr(obj, method_name):
            return default
        value = getattr(obj, method_name)(*args)
        # Some LUSAS COM methods return a tuple with the value and updated arguments,
        # so we take the first item if it's a tuple.
        if isinstance(value, tuple):
            return value[0] if len(value) > 0 else default
        return value
    except Exception:
        return default

def _serialise_com_value(value):
    """Convert common COM return values into JSON-friendly values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_serialise_com_value(item) for item in value]
    if hasattr(value, "getID") or hasattr(value, "getName"):
        return {"id": _safe_com_call(value, "getID"), "name": _safe_com_call(value, "getName")}
    return str(value)


# --- Classes -------------------------------------------------------------

class LusasClient:
    """Provides a Python interface to interact with the LUSAS Modeller using COM."""
    def __init__(self, config: Config):
        self.config = config
        self.versionString = config.lusasVersion
        self.modeller : 'IFModeller' = None
        self._set_modeller()
    
    def _set_modeller(self) -> bool:
        """Checks LUSAS connection and returns True if connected."""
        # If win32com isn't available (non-Windows or missing pywin32), bail out early
        if win32client is None:
            logger.warning("win32com.client not available — LUSAS COM integration disabled.")
            return False

        if self.modeller:
            try:
                # Test if the connection is still alive by making a simple call
                self.modeller.getCWD()
                return True
            except Exception as e:
                logger.warning(f"Invalid modeller reference ({str(e)}). Trying to reconnect.")
                self.modeller = None

        # Attach to a running instance of LUSAS
        try:
            # Get the active LUSAS object
            self.modeller: 'IFModeller' = win32client.GetActiveObject("Lusas.Modeller." + self.versionString)
            logger.info(f"Successfully attached on LUSAS version {self.versionString}.")
        except Exception as e:
            logger.warning(f"No running instance of LUSAS version {self.versionString} found.")
            return False

        try:
            # Check if the connection is valid by making a simple call
            self.modeller.getCWD()
        except Exception as e:
            logger.warning(f"Error checking status of found LUSAS instance: {str(e)}")
            return False
        return True

    def require_connection(self) -> dict | None:
        if not self._set_modeller():
            return error(
                "connection_failed",
                f"Cannot connect on LUSAS version {self.versionString}.",
                {"version": self.versionString},
            )
        return None

    def _loadcase_analysis_name(self, loadset) -> str:
        """Helper function to get loadset analysis name."""
        lsType = loadset.getTypeCode()
        if lsType == 0: #Loadcase
            return loadset.getAnalysis().getName()
        if lsType == 1: #Results Loadcase
            return loadset.getLoadcase().getAnalysis().getName()
        
        return "None"

    def _point_coordinates(self, point, global_coordinates: bool = True) -> dict:
        do_transform = not global_coordinates
        return {
            "x": _safe_com_call(point, "getX", do_transform),
            "y": _safe_com_call(point, "getY", do_transform),
            "z": _safe_com_call(point, "getZ", do_transform),
        }

    def _point_info(self, point, global_coordinates: bool = True) -> dict:
        data = {
            "type": "point",
            "id": _safe_com_call(point, "getID"),
            "name": _safe_com_call(point, "getName"),
            "coordinates": self._point_coordinates(point, global_coordinates),
            "selected": _safe_com_call(point, "isSelected"),
        }
        return data

    def _lower_order_points_info(self, obj, global_coordinates: bool = True) -> list[dict]:
        try:
            points = self.modeller.newObjectSet().add(obj).addLOF("Points").getObjects("Points")
        except Exception:
            return []
        return [self._point_info(point, global_coordinates) for point in points]

    def _assignments_info(self, obj) -> list[dict]:
        try:
            assignments = obj.getAssignments()
        except Exception:
            return []

        data = []
        for assignment in assignments:
            attr = _safe_com_call(assignment, "getAttribute")
            loadset = _safe_com_call(assignment, "getLoadset")
            entry = {
                "attribute": self._attribute_info(attr) if attr is not None else None,
                "loadset": {"id": _safe_com_call(loadset, "getID"), "name": _safe_com_call(loadset, "getName")} if loadset is not None else None,
            }
            data.append(entry)
        return data

    def _attribute_values(self, attr) -> dict:
        value_names = _safe_com_call(attr, "getValueNames", default=[])
        if value_names is None:
            return {}
        values = {}
        for value_name in value_names:
            values[str(value_name)] = _serialise_com_value(_safe_com_call(attr, "getValue", value_name))
        return values

    def _attribute_info(self, attr) -> dict:
        #value_names = _safe_com_call(attr, "getValueNames", default=[])
        return {
            "type": "attribute",
            "id": _safe_com_call(attr, "getID"),
            "name": _safe_com_call(attr, "getName"),
            "attribute_type": _safe_com_call(attr, "getAttributeType"),
            #"description": _safe_com_call(attr, "getDescription"),
            #"values": self._attribute_values(attr),
        }

    def _geometry_info(
        self,
        geometry_type: str,
        object_id: int,
        include_assignments: bool = True,
        include_child_points: bool = True,
        global_coordinates: bool = True,
    ) -> dict:
        obj = self.modeller.db().getObject(geometry_type, object_id)
        data = {
            "type": geometry_type.lower(),
            "id": _safe_com_call(obj, "getID"),
            "name": _safe_com_call(obj, "getName"),
            "selected": _safe_com_call(obj, "isSelected"),
        }

        if geometry_type == "Point":
            data["coordinates"] = self._point_coordinates(obj, global_coordinates)
        elif geometry_type == "Line":
            start_point = _safe_com_call(obj, "getStartPoint")
            end_point = _safe_com_call(obj, "getEndPoint")
            data["length"] = _safe_com_call(obj, "getLineLength")
            data["start_point"] = self._point_info(start_point, global_coordinates) if start_point is not None else None
            data["end_point"] = self._point_info(end_point, global_coordinates) if end_point is not None else None
        elif geometry_type == "Surface":
            data["area"] = _safe_com_call(obj, "getArea")
        elif geometry_type == "Volume":
            data["volume"] = _safe_com_call(obj, "getVolume")
            data["volume_type_code"] = _safe_com_call(obj, "getVolumeTypeCode")

        if include_child_points and geometry_type in {"Line", "Surface", "Volume"}:
            data["points"] = self._lower_order_points_info(obj, global_coordinates)
        if include_assignments:
            data["assignments"] = self._assignments_info(obj)
        return data

    def _analysis_info(self, analysis) -> dict:
        analysis_type = _safe_com_call(analysis, "getType")
        needs_solving = _safe_com_call(analysis, "needsSolving", True, None)
        loadcases = []
        try:
            # getLoadcases returns a SAFEARRAY.  Unlike the methods handled by
            # _safe_com_call, a tuple here is the array itself, not a return
            # value followed by in/out arguments.  Do not unwrap it.
            raw_loadcases = analysis.getLoadcases() if hasattr(analysis, "getLoadcases") else []
            if raw_loadcases is None:
                raw_loadcases = []
            elif not isinstance(raw_loadcases, (list, tuple)):
                # pywin32 may expose a one-element SAFEARRAY as the dispatch
                # object itself, which cannot be enumerated.
                raw_loadcases = [raw_loadcases]
            loadcases = [_safe_com_call(loadcase, "getID") for loadcase in raw_loadcases]
        except Exception as e:
            logger.warning(f"Failed to read loadcases for analysis: {str(e)}")

        return {
            "type": "analysis",
            "name": _safe_com_call(analysis, "getName"),
            "analysis_type": _analysis_type_to_string(analysis_type) if analysis_type is not None else None,
            "solved": not needs_solving if needs_solving is not None else None,
            "loadcases": loadcases,
        }

    def _loadset_info(self, loadset) -> dict:
        loadset_type = _safe_com_call(loadset, "getTypeCode")
        try:
            analysis_name = self._loadcase_analysis_name(loadset) if loadset_type is not None else None
        except Exception as e:
            logger.warning(f"Failed to read loadset analysis name: {str(e)}")
            analysis_name = None

        return {
            "type": "loadset",
            "id": _safe_com_call(loadset, "getID"),
            "name": _safe_com_call(loadset, "getName"),
            "loadset_type": _loadset_type_to_string(loadset_type) if loadset_type is not None else None,
            "analysis_name": analysis_name,
        }

    def _group_info(self, group) -> dict:
        return {
            "type": "group",
            "name": _safe_com_call(group, "getName"),
            "geometries" : {
                "points": _safe_com_call(group, "count", "Points"),
                "lines": _safe_com_call(group, "count", "Lines"),
                "surfaces": _safe_com_call(group, "count", "Surfaces"),
                "volumes": _safe_com_call(group, "count", "Volumes"),
            }
        }

    # Sections Library

    def _list_section_libraries(self) -> list[str]:
        """List available section libraries in the current model."""
        libraries = []
        try:
            libraries : list[str] = self.modeller.getSectionLibraries()
        except Exception as e:
            logger.error(f"Error listing section libraries: {str(e)}")
        return libraries
    
    def _list_section_types(self, library: str) -> list[str]:
        """List available section types of a library in the current model."""
        types = []
        try:
            types : list[str] = self.modeller.getSectionTypes(library)
        except Exception as e:
            logger.error(f"Error listing section types of {library}: {str(e)}")
        return types
    
    def _list_section_names(self, library: str, section_type: str) -> list[str]:
        """List available section names of a type within a library in the current model."""
        names = []
        try:
            names : list[str] = self.modeller.getSectionNames(library, section_type)
        except Exception as e:
            logger.error(f"Error listing section names of {library}-{section_type}: {str(e)}")
        return names


# --- Main -------------------------------------------------------------

logger = logging.getLogger('lusas_mcp_server')

# This is for testing purposes only
if __name__ == "__main__":
    modeller = LusasClient().modeller
    if modeller is None:
        sys.exit("Failed to connect to LUSAS.")
        
    if not modeller.existsDatabase():
        print("Database not found, creating a project...")
        modeller.newProject()
