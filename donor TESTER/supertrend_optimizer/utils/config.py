"""
Configuration loading module.

This module handles loading and parsing YAML configuration files.
"""

from typing import Dict, Any
import yaml


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
    """
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    return config

