# ---------------------------------------------------------------------------
# LPI Tools and Resources
# Description:  LPI Tools and Resources for LUSAS MCP Server.
# Copyright (c) 2026 Finite Element Analysis Ltd.
# All rights reserved.
# ---------------------------------------------------------------------------

import json, re, os, logging
from copy import deepcopy
from config import Config
from toolsResponses import error, ok
from toolsHelpers import SearchRecord, first_sentence, get_snippet, text_from_object, tokenise_text


# --- Load Data -------------------------------------------------------------

def _get_data_filepath(config: Config, filename: str, data_path: str | None = None) -> str:
    """Resolve a data file for the configured LUSAS major version."""
    data_path = data_path or os.path.join(os.path.dirname(__file__), "data")
    try:
        configured_version = int(config.lusasVersionFloat)
    except (AttributeError, TypeError, ValueError):
        configured_version = 20

    candidates = [
        os.path.join(data_path, f"v{configured_version}", filename),
        os.path.join(data_path, filename),
        *(
            os.path.join(data_path, f"v{version}", filename)
            for version in range(configured_version - 1, 19, -1)
        ),
    ]
    for filepath in dict.fromkeys(candidates):
        if os.path.isfile(filepath):
            return filepath

    raise FileNotFoundError(
        f"Could not find {filename!r} for v{configured_version}, a previous version "
        "down to v20, or the shared data directory"
    )

def configure_data(config: Config) -> None:
    """Load all LPI datasets using the server's active configuration."""
    global lpi_methods_data, materials_data, manual_data, elements_data, examples_index_data
    global _active_lpi_example_language
    global _loaded_data_filepaths

    _active_lpi_example_language = (
        "python" if config.lusasVersionFloat >= 23.0 else "vbscript"
    )

    datasets = {
        "lpi_methods.json": "lpi_methods_data",
        "materials.json": "materials_data",
        "manual.json": "manual_data",
        "elements.json": "elements_data",
        "examples/examples_index.json": "examples_index_data",
    }
    for filename, variable_name in datasets.items():
        try:
            filepath = _get_data_filepath(config, filename)
            if _loaded_data_filepaths.get(variable_name) == filepath:
                continue
            with open(filepath, "r", encoding="utf-8") as f:
                globals()[variable_name] = json.load(f)
            _loaded_data_filepaths[variable_name] = filepath
            logger.info("Loaded %s from %s", filename, filepath)
        except Exception as e:
            logger.warning("Failed to load %s: %s", filename, e)
            globals()[variable_name] = {}
            _loaded_data_filepaths.pop(variable_name, None)

    _build_lpi_runtime_indexes()

def _build_lpi_runtime_indexes() -> None:
    """Build exact lookup maps after the versioned LPI data has loaded."""
    global _lpi_method_by_id, _examples_by_id
    methods_by_id = {
        method.get("id"): method
        for method in lpi_methods_data.get("methods", [])
        if method.get("id")
    }
    interfaces_by_id = {
        interface.get("name"): interface
        for interface in lpi_methods_data.get("interfaces", [])
        if interface.get("name")
    }
    _lpi_method_by_id = {**interfaces_by_id, **methods_by_id}
    _examples_by_id = {
        example.get("id"): example
        for example in examples_index_data.get("examples", [])
        if example.get("id")
    }

def _clean_manual_topic_metadata(topic: dict, include_images: bool = False) -> dict:
    metadata = deepcopy(topic)
    metadata.pop("search_text", None)
    if not include_images:
        metadata.pop("images", None)
    if len(metadata.get("links", [])) == 0:
        metadata.pop("links", None)
    if len(metadata.get("images", [])) == 0:
        metadata.pop("images", None)
    if len(metadata.get("headings", [])) == 0:
        metadata.pop("headings", None)
    if len(metadata.get("toc_path", [])) == 0:
        metadata.pop("toc_path", None)
    if metadata.get("heading_level") is None:
        metadata.pop("heading_level", None)
    if metadata.get("source_title", "") == "":
        metadata.pop("source_title", None)
    return metadata

# --- LPI Commands -------------------------------------------------------------

def _lpi_method_record(method: dict) -> SearchRecord:
    """Project LPI method metadata into the shared search-record shape."""
    parameters = list(method.get("parameters", []))
    return_parameter = method.get("return_parameter")
    if return_parameter:
        parameters.append(return_parameter)
    return SearchRecord(
        high=f"{method.get('id', '')} {method.get('name', '')}",
        mid=" ".join(
            str(parameter.get("name", ""))
            for parameter in parameters
            if isinstance(parameter, dict)
        ),
        low=" ".join(
            [
                method.get("description", ""),
                *(
                    str(parameter.get("description", ""))
                    for parameter in parameters
                    if isinstance(parameter, dict)
                ),
                *method.get("examples", []),
            ]
        ),
    )

def search_lpi_commands(query: str, max_results: int = 10, skip_results: int = 0) -> dict:
    """
    Search LPI (LUSAS API) commands and optionally examples.

    The query is tokenised case-insensitively, including camelCase method names.
    Every meaningful term must match. Results use corpus-aware TF-IDF body
    scoring, with additional weight for method names and parameter labels.

    Parameters:
    - query: The search query string.
    - max_results: Maximum number of results to return (default 10, max 200).
    - skip_results: Number of results to skip (default 0, max 200).
    """
    # Validation
    if not isinstance(query, str) or not query.strip():
        return error("validation_error", "'query' must be a non-empty string.", {"query": query})
    
    max_results = max(1, min(max_results, 200))
    skip_results = max(0, skip_results)

    try:
        tokens = tokenise_text(query)
        if not tokens:
            return error("search_failed", "No valid search tokens found in query.")

        methods = lpi_methods_data.get("methods", [])
        method_records = [_lpi_method_record(method) for method in methods]
        frequencies = SearchRecord.getTFIDFFrequencies(method_records, tokens)
        scored = []
        for method, record in zip(methods, method_records):
            score = record.getTFIDFScore(tokens, frequencies)
            if score <= 0: continue

            args = [param.get("name", "_") for param in method.get("parameters", [])]

            hit = {
                "id": method.get("id", ""),
                "args": ",".join(args),
                "desc": first_sentence(method.get("description", "")),
                "score": score,
            }
            scored.append(hit)

        scored.sort(key=lambda h: (-h["score"]))

        truncated = len(scored) > max_results
        hits = scored[skip_results:skip_results + max_results]

        # Add index
        #for i, hit in enumerate(hits):
        #    hit["index"] = i
        
        return ok(
            {
                "query": query,
                "methods": hits,
                "total": len(scored),
                "truncated": truncated,
            }
        )
    except Exception as e:
        logger.exception("Error searching LPI commands")
        return error("search_failed", str(e))

def get_lpi_command_details(identifiers: list[str]) -> dict:
    """Return complete LPI documentation for one or more selected command IDs.

    Parameters:
    - identifiers: A list of command IDs to fetch documentation for i.e. ["IFGeometricLine.createGeometricLine", "createGeometricLine", "IFGeometricLine"]
    """
    if (
        not isinstance(identifiers, list)
        or len(identifiers) == 0
        or any(not isinstance(identifier, str) or not identifier.strip() for identifier in identifiers)
    ):
        return error(
            "validation_error",
            "'identifiers' must be a list containing non-empty command IDs.",
            {"identifiers": identifiers},
        )

    items = []
    for identifier in identifiers:
        requested_identifier = identifier.strip()
        identifier = requested_identifier

        if identifier not in _lpi_method_by_id:
            # Find a method or interface when the caller supplied an unqualified ID.
            identifier = next((key for key in _lpi_method_by_id if key.endswith(identifier)), None)

        command = _lpi_method_by_id.get(identifier)
        if command is None:
            items.append({"id": requested_identifier, "found": False})
            continue

        if "methods" in command:
            interface_name = command.get("name", "")
            method_prefix = f"{interface_name}."
            items.append({
                "id": identifier,
                "found": True,
                "kind": "interface",
                "name": interface_name,
                "base": command.get("base", ""),
                "title": command.get("title", ""),
                "description": command.get("description", ""),
                "methods": [
                    method.removeprefix(method_prefix)
                    for method in command.get("methods", [])
                ],
                "examples": command.get("examples", []),
            })
            continue

        method = command
        items.append({
            "id": identifier,
            "found": True,
            "interface": method.get("interface", ""),
            "name": method.get("name", ""),
            "signature": method.get("signature", ""),
            #"vbscript_signature": method.get("vbscript_signature", ""),
            "description": method.get("description", method.get("summary", "")),
            "return_type": method.get("return_type"),
            "parameters": method.get("parameters", []),
            "return_parameter": method.get("return_parameter"),
            "call_patterns": method.get("call_patterns", []),
            "references": method.get("references", []),
            "examples": method.get("examples", []),
        })
    return ok({"items": items})


# --- Examples (LPI) -------------------------------------------------------------

def _example_code(filepath: str) -> str:
    """Read an example whose path is relative to the bundled examples directory."""
    examples_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "data", "examples"))
    requested_path = os.path.abspath(os.path.join(examples_root, filepath))
    if os.path.commonpath([examples_root, requested_path]) != examples_root:
        raise ValueError("Example path is outside the bundled examples directory.")
    with open(requested_path, "r", encoding="utf-8") as source:
        return source.read()

# -- Examples Tools

def search_lpi_examples(query: str, limit: int = 5) -> dict:
    """Find bundled LPI examples for the language supported by the configured LUSAS version.

    LUSAS 23.0 and later returns LPython examples; earlier versions return
    VBScript examples. Use ``Get LPI Example`` to retrieve the source of a
    selected result.
    """
    if not isinstance(query, str) or not query.strip():
        return error("validation_error", "'query' must be a non-empty string.", {"query": query})
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 25:
        return error("validation_error", "'limit' must be an integer from 1 to 25.", {"limit": limit})
    try:
        tokens = tokenise_text(query)
        if not tokens:
            return ok({"query": query, "matches": [], "total": 0, "truncated": False})

        selected_language = _active_lpi_example_language
        examples = [
            example
            for example in examples_index_data.get("examples", [])
            if example.get("files", {}).get(selected_language)
        ]
        example_records = [
            SearchRecord(
                high=example.get("id", ""),
                mid="",
                low=" ".join(
                    [example.get("description", ""), " ".join(example.get("uses", []))]
                ),
            )
            for example in examples
        ]
        frequencies = SearchRecord.getTFIDFFrequencies(example_records, tokens)
        scored = []
        for example, record in zip(examples, example_records):
            # Example metadata is intentionally compact, so a natural-language
            # query may include a useful term absent from its short index entry.
            # Keep those partial matches, but rank them below complete matches.
            score = record.getTFIDFScore(tokens, frequencies, require_all_terms=False)
            if score <= 0:
                continue

            scored.append((score, {
                "id": example.get("id", ""),
                "description": example.get("description", ""),
            }))

        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        return ok(
            {
                "query": query,
                "language": selected_language,
                "matches": [match for _, match in scored[:limit]],
                "total": len(scored),
                "truncated": len(scored) > limit,
            }
        )
    except Exception as e:
        logger.exception("Error finding LPI examples")
        return error("example_search_failed", str(e))

# -- Examples Resources

def get_lpi_examples_index() -> dict:
    """Return compact metadata for every bundled LPI example, without source code."""
    examples = []
    for example in examples_index_data.get("examples", []):
        example_id = example.get("id", "")
        if not example_id:
            continue
        examples.append(
            {
                "id": example_id,
                "description": example.get("description", ""),
            }
        )
    return ok(
        {
            "language": _active_lpi_example_language,
            "examples": examples,
            "total": len(examples),
        }
    )

def get_lpi_example(identifier: str) -> dict:
    """Return one complete bundled LPI example in the configured LUSAS language."""
    if not isinstance(identifier, str) or not identifier.strip():
        return error("validation_error", "'identifier' must be a non-empty string.", {"identifier": identifier})
    identifier = identifier.strip()
    example = _examples_by_id.get(identifier)
    if example is None:
        return error("not_found", f"LPI example '{identifier}' was not found.", {"identifier": identifier})

    selected_language = _active_lpi_example_language
    filepath = example.get("files", {}).get(selected_language)
    if not filepath:
        return error(
            "unsupported_lusas_version",
            f"LPI example '{identifier}' has no {selected_language} implementation.",
            {"identifier": identifier, "language": selected_language},
        )
    try:
        return ok({
            "id": identifier,
            "description": example.get("description", ""),
            "uses": example.get("uses", []),
            "language": selected_language,
            "path": filepath,
            "code": _example_code(filepath),
        })
    except (OSError, ValueError) as e:
        logger.exception("Unable to read LPI example %s", identifier)
        return error("example_read_failed", str(e), {"identifier": identifier, "path": filepath})


# --- Materials Library -------------------------------------------------------------

def search_materials_library(query: str, max_results: int = 10) -> dict:
    """Search the LUSAS materials library with free text.

    ``query`` is matched case-insensitively across library materials, regions, standards and grades.
    ``max_results`` defaults to 10.
    """
    if not isinstance(query, str) or not query.strip():
        return error("validation_error", "'query' must be a non-empty string.", {"query": query})

    if isinstance(max_results, bool) or not isinstance(max_results, int):
        return error("validation_error", "'max_results' must be an integer.", {"max_results": max_results})
    max_results = max(1, max_results)

    try:
        tokens = tokenise_text(query)
        if not tokens:
            return ok({"query": query, "hits": [], "total": 0, "truncated": False})

        materials = materials_data.get("materials", [])
        material_records = [
            SearchRecord(
                high=f"{material.get('material', '')} {material.get('design_code', '')} {material.get('grade', '')}",
                mid=material.get("region", ""),
                low="",
            )
            for material in materials
        ]
        frequencies = SearchRecord.getTFIDFFrequencies(material_records, tokens)
        scored = []
        for material, record in zip(materials, material_records):
            score = record.getTFIDFScore(tokens, frequencies)
            if score <= 0:
                continue
            scored.append({
                #"id": material.get("id"),
                "material": material.get("material", ""),
                "region": material.get("region", ""),
                "standard": material.get("design_code", ""),
                "grade": material.get("grade", ""),
                "score": score
            })

        scored.sort(key=lambda hit: (-hit["score"], hit["material"], hit["region"], hit["standard"], hit["grade"]))
        truncated = len(scored) > max_results
        hits = scored[:max_results]

        # Add index in each entry
        #for i, hit in enumerate(hits):
        #    hit["index"] = i

        return ok(
            {
                "query": query,
                "tokens": tokens,
                "hits": hits,
                "total": len(scored),
                "truncated": truncated
            }
        )
    except Exception as e:
        logger.exception("Error searching material library")
        return error("material_search_failed", str(e))

# --- Elements -------------------------------------------------------------

def _expanded_element_records() -> list[dict]:
    records = []
    for group in elements_data.get("groups", []):
        for name in group.get("names", []):
            record = {
                "name": name,
                "chapter": group.get("chapter"),
                "family": group.get("family"),
                "feature_type": group.get("feature_type"),
                "description": group.get("description"),
                "variation": group.get("variation", {}),
                "notes": group.get("notes"),
                "related_names": group.get("names", []),
            }
            records.append(record)
    return records

def search_elements(query: str = "", family: str = None, feature_type: str = None, variation: str = None, max_results: int = 20) -> dict:
    """
    Search supported LUSAS element names that can be used as API command inputs.
    Every meaningful query term must match an identifier, classification, or
    description; identifiers and classifications are weighted above body text.

    Parameters:
    - query: Optional element name or text query, e.g. "BMI21", "thick beam", "linear stress".
    - family: Optional family filter: "bar_cable", "beam", "continuum_2d", "plate", "shell", "membrane", "continuum_3d", "joint", "thermal_field", "hygro_thermal", "interface", "non_structural_mass", "rigid_slideline", "phreatic"
    - feature_type: Optional feature type filter, e.g. "point", "line", "surface", "volume".
    - variation: Optional variation filter, e.g. "constant", "linear", "singular".
    - max_results: Maximum number of results to return (default 20, max 200).
    """
    max_results = max(1, min(max_results, 200))
    if query is not None and not isinstance(query, str):
        return error("validation_error", "'query' must be a string if provided.", {"query": query})
    if family is not None and not isinstance(family, str):
        return error("validation_error", "'family' must be a string if provided.", {"family": family})
    if feature_type is not None and not isinstance(feature_type, str):
        return error("validation_error", "'feature_type' must be a string if provided.", {"feature_type": feature_type})
    if variation is not None and not isinstance(variation, str):
        return error("validation_error", "'variation' must be a string if provided.", {"variation": variation})

    try:
        records = _expanded_element_records()
        if not records:
            return error(
                "element_reference_not_loaded",
                "LUSAS element reference not loaded. Check src/data/elements.json.",
            )

        family_filter = family.strip().lower() if family and family.strip() else None
        feature_type_filter = feature_type.strip().lower() if feature_type and feature_type.strip() else None
        variation_filter = variation.strip().lower() if variation and variation.strip() else None
        query = query.strip() if query else ""
        tokens = tokenise_text(query) if query else []

        scoring_records = [
            SearchRecord(
                high=f"{record.get('name', '')} {record.get('description', '')}",
                mid=" ".join(
                    str(record.get(field, ""))
                    for field in ("chapter", "family", "feature_type")
                ),
                low=" ".join(
                    [
                        text_from_object(record.get("variation", {})),
                        text_from_object(record.get("notes", "")),
                        " ".join(record.get("related_names", [])),
                    ]
                ),
            )
            for record in records
        ]
        frequencies = SearchRecord.getTFIDFFrequencies(scoring_records, tokens) if tokens else {}

        scored = []
        for record, scoring_record in zip(records, scoring_records):
            if family_filter and family_filter not in str(record.get("family", "")).lower():
                continue
            if feature_type_filter and feature_type_filter not in str(record.get("feature_type", "")).lower():
                continue
            if variation_filter and variation_filter not in text_from_object(record.get("variation", {})).lower():
                continue

            if tokens:
                score = scoring_record.getTFIDFScore(tokens, frequencies)
                if score <= 0:
                    continue
            else:
                score = 1

            metadata = deepcopy(record)
            metadata.pop("search_text", None)
            scored.append({**metadata, "score": score})

        scored.sort(key=lambda hit: (-hit["score"], hit["name"]))
        truncated = len(scored) > max_results
        hits = scored[:max_results]
        for i, hit in enumerate(hits):
            hit["index"] = i

        return ok(
            {
                "query": query,
                "tokens": tokens,
                "filters": {
                    "family": family_filter,
                    "feature_type": feature_type_filter,
                    "variation": variation_filter,
                },
                "hits": hits,
                "total": len(scored),
                "truncated": truncated,
            }
        )
    except Exception as e:
        logger.exception("Error searching LUSAS elements")
        return error("element_search_failed", str(e))

def get_element_details(element_names: str | list[str]) -> dict | None | list[dict | None]:
    """Return element documentation for one name or a list of names.

    A list response preserves the input order.  An unknown element is returned
    as ``None`` at its corresponding position, matching the single-name lookup
    behaviour.
    """
    is_multiple = isinstance(element_names, list)
    names = element_names if is_multiple else [element_names]
    if any(not isinstance(name, str) or not name.strip() for name in names):
        return None

    records_by_name = {
        record["name"].upper(): record
        for record in _expanded_element_records()
    }
    details = [
        deepcopy(records_by_name.get(name.strip().upper()))
        for name in names
    ]
    return details if is_multiple else details[0]


# --- Manual/Help -------------------------------------------------------------

def search_manual(
    query: str,
    max_results: int = 10,
    context: int = -1,
    return_images: bool = False,
) -> dict:
    """
    Search the generated LUSAS manual index from ``src/data/manual.json``.
    Every meaningful query term must match a title, TOC path, source path, or
    body. Rare terms receive more weight, with titles and TOC paths favoured
    over incidental body mentions.

    Parameters:
    - query: The search query string.
    - max_results: Maximum number of results to return (default 10, max 200).
    - context: Number of surrounding characters to include in snippets (default -1 to return full text).
    - return_images: Include embedded manual images in matching results (default False to reduce size).
    """
    if not isinstance(query, str) or not query.strip():
        return error("validation_error", "'query' must be a non-empty string.", {"query": query})

    max_results = max(1, min(max_results, 200))

    try:
        topics = manual_data.get("topics", [])
        if not topics:
            return error(
                "manual_not_loaded",
                "Manual index not loaded. Generate src/data/manual.json with scripts/generate_manual_data.py.",
            )

        tokens = tokenise_text(query)
        if not tokens:
            return ok({"query": query, "hits": [], "total": 0, "truncated": False})

        topic_records = [
            SearchRecord(
                high=f"{topic.get('title', '')} {topic.get('path', '')}",
                mid=" ".join(topic.get("toc_path", [])),
                # search_text includes title and TOC metadata, which are already
                # separate scoring surfaces. Keeping body text distinct prevents
                # duplicated metadata from artificially inflating its density.
                low=topic.get("text", "") or topic.get("search_text", ""),
            )
            for topic in topics
        ]
        frequencies = SearchRecord.getTFIDFFrequencies(topic_records, tokens)
        scored = []
        for topic_index, (topic, record) in enumerate(zip(topics, topic_records)):
            toc_path = topic.get("toc_path", [])
            score = record.getTFIDFScore(tokens, frequencies)
            if score <= 0:
                continue

            hit = {
                "title": topic.get("title", ""),
                #"path": topic.get("path", ""),
                "toc_path": toc_path,
                #"headings": topic.get("headings", []),
                "snippet": get_snippet(topic.get("text", ""), tokens, context=context),
                #"links": topic.get("links", [])[:10],
                "score": score,
            }
            if return_images and topic.get("images"):
                hit["images"] = [
                    f"{topic_index}:{image_index}"
                    for image_index in range(len(topic["images"]))
                ]
            # Skip table of contents
            if hit["title"].lower() == "table of contents": continue
            # Add to scored list
            scored.append(hit)

        scored.sort(key=lambda hit: (-hit["score"], hit["title"]))
        truncated = len(scored) > max_results
        hits = scored[:max_results]
        for i, hit in enumerate(hits):
            hit["index"] = i

        return ok(
            {
                "query": query,
                "tokens": tokens,
                "hits": hits,
                "total": len(scored),
                "truncated": truncated,
            }
        )
    except Exception as e:
        logger.exception("Error searching manual")
        return error("manual_search_failed", str(e))

def get_manual_image_data(image_id: str) -> str | None:
    """Resolve a manual image ID returned by ``search_manual`` to its data URI."""
    if not isinstance(image_id, str):
        return None

    match = re.fullmatch(r"(\d+):(\d+)", image_id)
    if not match:
        return None

    topic_index, image_index = map(int, match.groups())
    topics = manual_data.get("topics", [])
    if topic_index >= len(topics):
        return None

    images = topics[topic_index].get("images", [])
    if image_index >= len(images):
        return None

    image = images[image_index]
    return image if isinstance(image, str) else None

def search_manual_no_images(
    query: str,
    max_results: int = 10,
    context: int = 240
) -> dict:
    """
    Search the generated LUSAS manual index from ``src/data/manual.json`` (images will not be returned).

    Parameters:
    - query: The search query string.
    - max_results: Maximum number of results to return (default 10, max 200).
    - context: Number of surrounding characters to include in snippets (default 240).
    """
    return search_manual(query, max_results=max_results, context=context)

def get_manual_entries_by_toc_path_no_images(
    toc_path: list[str],
    prefix_match: bool = False,
    max_results: int = 20
) -> dict:
    """
    Retrieve manual entries by exact or prefix ``toc_path`` match.

    Parameters:
    - toc_path: Ordered list of toc path segments to match.
    - prefix_match: If True, return entries whose toc_path starts with the supplied path.
    - max_results: Maximum number of results to return (default 20, max 200).
    """
    return get_manual_entries_by_toc_path(
        toc_path=toc_path,
        prefix_match=prefix_match,
        max_results=max_results,
        include_images=False
    )

def get_manual_entries_by_toc_path(
    toc_path: list[str],
    prefix_match: bool = False,
    max_results: int = 20,
    include_images: bool = False,
) -> dict:
    """
    Retrieve manual entries by exact or prefix ``toc_path`` match.

    Parameters:
    - toc_path: Ordered list of toc path segments to match.
    - prefix_match: If True, return entries whose toc_path starts with the supplied path.
    - max_results: Maximum number of results to return (default 20, max 200).
    - include_images: Include embedded image data when present (default False).
    """
    if not isinstance(toc_path, list) or not toc_path or not all(isinstance(part, str) for part in toc_path):
        return error("validation_error", "'toc_path' must be a non-empty list of strings.", {"toc_path": toc_path})

    wanted = [part.strip() for part in toc_path if part and part.strip()]
    if not wanted:
        return error("validation_error", "'toc_path' must contain at least one non-empty string.", {"toc_path": toc_path})

    max_results = max(1, min(max_results, 200))

    try:
        topics = manual_data.get("topics", [])
        if not topics:
            return error(
                "manual_not_loaded",
                "Manual index not loaded. Generate src/data/manual.json with scripts/generate_manual_data.py.",
            )

        hits = []
        for topic in topics:
            current = [str(part).strip() for part in topic.get("toc_path", []) if str(part).strip()]
            if prefix_match:
                is_match = current[: len(wanted)] == wanted
            else:
                is_match = current == wanted
            if not is_match:
                continue

            hits.append(_clean_manual_topic_metadata(topic, include_images=include_images))

        truncated = len(hits) > max_results
        return ok(
            {
                "toc_path": wanted,
                "prefix_match": prefix_match,
                "hits": hits[:max_results],
                "total": len(hits),
                "truncated": truncated,
            }
        )
    except Exception as e:
        logger.exception("Error retrieving manual entries by toc_path")
        return error("manual_lookup_failed", str(e))

    """Get searchable LUSAS LPI examples database."""
    if not lpi_methods_data.get("examples"):
        return "LPI examples not loaded"
    
    try:
        # Return formatted examples listing
        output = []
        output.append("# LUSAS LPI VBScript Examples")
        output.append("")
        output.append(f"Total Examples: {len(lpi_methods_data.get('examples', []))}")
        output.append("")

        examples_by_group: dict[str, list[dict]] = {}
        for example in lpi_methods_data.get("examples", []):
            examples_by_group.setdefault(example.get("category", "examples"), []).append(example)

        for group_name, examples in examples_by_group.items():
            output.append(f"## {group_name.upper()} Examples")
            output.append("")

            for example in examples[:10]:
                output.append(f"### {example.get('title', example.get('id', 'Example'))}")
                output.append(f"**File**: `{example.get('path', '')}`")
                output.append(f"**Category**: {example.get('category', '')}")
                uses = example.get("uses", [])
                if uses:
                    output.append(f"**Uses**: {', '.join(uses)}")
                output.append("")
        
        return "\n".join(output)
    except Exception as e:
        logger.exception("Error generating examples reference")
        return f"Error generating examples reference: {str(e)}"


# --- Main -------------------------------------------------------------

logger = logging.getLogger('lusas_mcp_server')
_loaded_data_filepaths = {}

# Load the default configuration for direct module use. The server calls this
# again with its argument-selected configuration before registering the tools.
configure_data(Config())
