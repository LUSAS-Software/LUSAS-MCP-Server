# ---------------------------------------------------------------------------
# Config Loader
# Description:  This module provides a configuration loader for the LUSAS MCP server. 
#               It reads configuration settings from a JSON file and provides access to these settings through properties.
# Copyright (c) 2026 Finite Element Analysis Ltd.
# All rights reserved.
# ---------------------------------------------------------------------------

import json, logging, os


# --- Classes -------------------------------------------------------------

class Config:

    def __init__(self, config_filepath=None):
        # Load config
        default_filepath = os.path.join(os.path.dirname(__file__), 'config.json')
        self.filepath = os.path.abspath(config_filepath or default_filepath)
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
            logger.info("Configuration loaded successfully.")
        except Exception as e:
            logger.error(f"Could not load config file: {str(e)}")
            # Return default config if loading fails
            self.data = {}

    @property
    def serverName(self) -> str:
        return self.get('server', {}).get('name', 'LUSAS MCP')

    @property
    def serverVersion(self) -> str:
        return self.get('server', {}).get('version', '1.0.0')
    
    @property
    def lusasVersion(self) -> str:
        return f"{self.lusasVersionFloat:.1f}"
    
    @property
    def lusasVersionFloat(self) -> float:
        try:
            version = float(self.data.get('lusas', {}).get('version'))
            if not version > 0:
                raise ValueError
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Configuration file '{self.filepath}' must set a positive numeric 'lusas.version'."
            ) from exc
        return version

    def validate(self) -> None:
        """Validate configuration required before starting the MCP server."""
        if not isinstance(self.data, dict):
            raise ValueError(
                f"Configuration file '{self.filepath}' must contain a JSON object."
            )
        _ = self.lusasVersionFloat

    @property
    def readOnlyMode(self) -> bool:
        return self.data.get('options', {}).get('read_only_mode', False)
    
    @property
    def saveGeneratedScripts(self) -> bool:
        return self.data.get('options', {}).get('save_generated_scripts', False)

    @property
    def logFilepath(self) -> str:
        return self.data.get('options', {}).get('log_filepath', '')

    @property
    def allowManualImagesReturn(self) -> bool:
        return self.data.get('options', {}).get('allow_manual_images_return', False)


# --- Main -------------------------------------------------------------

logger = logging.getLogger('lusas_mcp_server')

# This is for testing purposes only
if __name__ == "__main__":
    config = Config()
    print(f"Server Name: {config.serverName}")
    print(f"Server Version: {config.serverVersion}")
    print(f"LUSAS Version: {config.lusasVersion}")
    print(f"Read-Only Mode: {config.readOnlyMode}")
    print(f"Save Generated Scripts: {config.saveGeneratedScripts}")
    print(f"Log Filepath: {config.logFilepath}")
    print(f"Allow Manual Images Return: {config.allowManualImagesReturn}")
