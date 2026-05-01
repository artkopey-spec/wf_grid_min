"""
Configuration loading module.

This module handles loading and parsing YAML configuration files.
"""

from typing import Dict, Any
import yaml

from supertrend_optimizer.utils.exceptions import ConfigError


def load_config(path: str) -> Dict[str, Any]:
    """
    Load configuration from YAML file.

    Args:
        path: Path to the YAML configuration file

    Returns:
        Dictionary containing configuration values

    Raises:
        FileNotFoundError: If configuration file does not exist
        yaml.YAMLError: If YAML parsing fails
        ConfigError: If the YAML file is empty or does not contain a mapping
                     at the top level (e.g. null, list, scalar).
    """
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    if config is None:
        raise ConfigError(
            f"Configuration file '{path}' is empty or contains only comments. "
            "Expected a YAML mapping (dict) at the top level."
        )

    if not isinstance(config, dict):
        raise ConfigError(
            f"Configuration file '{path}' must contain a YAML mapping at the top "
            f"level, got {type(config).__name__!r}. "
            "Check that the file starts with key: value pairs, not a list or scalar."
        )

    return config

