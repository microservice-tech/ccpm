#!/usr/bin/env python3
"""
Configuration Manager for Claude Flow Service

Handles JSON configuration files and environment variable support
with validation and type coercion.
"""

import json
import os
import logging
from typing import Any, Dict, Optional, Union
from pathlib import Path


class ConfigManager:
    """Manages configuration from JSON files and environment variables"""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration manager
        
        Args:
            config_path: Path to JSON config file. Defaults to config.json in same directory.
        """
        self.logger = logging.getLogger(__name__)
        
        # Default config path
        if config_path is None:
            config_path = Path(__file__).parent / "config.json"
        
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self._defaults = self._get_default_config()
        
        # Load configuration
        self._load_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration values"""
        return {
            "github": {
                "token": "",
                "owner": "",
                "repo": "",
                "api_base_url": "https://api.github.com",
                "labels": ["ready-for-implementation"],
                "rate_limit_remaining_threshold": 10
            },
            "service": {
                "poll_interval_seconds": 300,  # 5 minutes
                "max_concurrent_issues": 3,
                "workspace_base_path": "/tmp/claude-flow-issues",
                "log_level": "INFO",
                "health_check_port": 8080,
                "cleanup_on_shutdown": True
            },
            "claude_flow": {
                "install_command": "npm install claude-flow@alpha",
                "init_command": "npx claude-flow init --force",
                "timeout_seconds": 3600,  # 1 hour
                "max_retries": 3
            },
            "systemd": {
                "service_name": "claude-flow-service",
                "restart_policy": "always",
                "restart_delay_seconds": 30,
                "log_rotation": True
            },
            "monitoring": {
                "health_check_enabled": True,
                "metrics_enabled": False,
                "status_file": "/var/lib/claude-flow/status.json"
            }
        }
    
    def _load_config(self) -> None:
        """Load configuration from JSON file and apply environment overrides"""
        # Start with defaults
        self._config = self._defaults.copy()
        
        # Load from JSON file if it exists
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    file_config = json.load(f)
                    self._merge_config(self._config, file_config)
                self.logger.info(f"Loaded configuration from {self.config_path}")
            except (json.JSONDecodeError, OSError) as e:
                self.logger.error(f"Failed to load config from {self.config_path}: {e}")
        else:
            self.logger.warning(f"Config file {self.config_path} not found, using defaults")
        
        # Apply environment variable overrides
        self._apply_env_overrides()
        
        # Validate configuration
        self._validate_config()
    
    def _merge_config(self, base: Dict[str, Any], override: Dict[str, Any]) -> None:
        """Recursively merge override config into base config"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value
    
    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides using dot notation"""
        env_mappings = {
            # GitHub settings
            "CLAUDE_FLOW_GITHUB_TOKEN": "github.token",
            "CLAUDE_FLOW_GITHUB_OWNER": "github.owner", 
            "CLAUDE_FLOW_GITHUB_REPO": "github.repo",
            "CLAUDE_FLOW_GITHUB_API_URL": "github.api_base_url",
            
            # Service settings
            "CLAUDE_FLOW_POLL_INTERVAL": "service.poll_interval_seconds",
            "CLAUDE_FLOW_MAX_CONCURRENT": "service.max_concurrent_issues",
            "CLAUDE_FLOW_WORKSPACE_PATH": "service.workspace_base_path",
            "CLAUDE_FLOW_LOG_LEVEL": "service.log_level",
            "CLAUDE_FLOW_HEALTH_PORT": "service.health_check_port",
            
            # Claude Flow settings
            "CLAUDE_FLOW_TIMEOUT": "claude_flow.timeout_seconds",
            "CLAUDE_FLOW_MAX_RETRIES": "claude_flow.max_retries",
        }
        
        for env_var, config_path in env_mappings.items():
            if env_var in os.environ:
                value = os.environ[env_var]
                self._set_nested_value(self._config, config_path, self._coerce_type(value))
                self.logger.debug(f"Applied environment override: {env_var} -> {config_path}")
    
    def _set_nested_value(self, config: Dict[str, Any], path: str, value: Any) -> None:
        """Set a nested configuration value using dot notation"""
        keys = path.split('.')
        current = config
        
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        current[keys[-1]] = value
    
    def _coerce_type(self, value: str) -> Union[str, int, bool, float]:
        """Coerce string values to appropriate types"""
        # Boolean values
        if value.lower() in ('true', 'yes', '1', 'on'):
            return True
        elif value.lower() in ('false', 'no', '0', 'off'):
            return False
        
        # Numeric values
        try:
            if '.' in value:
                return float(value)
            else:
                return int(value)
        except ValueError:
            pass
        
        # String value
        return value
    
    def _validate_config(self) -> None:
        """Validate required configuration values"""
        required_fields = [
            ("github.token", "GitHub token is required"),
            ("github.owner", "GitHub owner is required"),
            ("github.repo", "GitHub repository is required"),
        ]
        
        for field_path, error_msg in required_fields:
            if not self.get(field_path):
                raise ValueError(f"Configuration validation failed: {error_msg}")
        
        # Validate numeric ranges
        poll_interval = self.get("service.poll_interval_seconds", 300)
        if poll_interval < 60:
            raise ValueError("Poll interval must be at least 60 seconds")
        
        max_concurrent = self.get("service.max_concurrent_issues", 3)
        if max_concurrent < 1 or max_concurrent > 10:
            raise ValueError("Max concurrent issues must be between 1 and 10")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation
        
        Args:
            key: Configuration key using dot notation (e.g., 'github.token')
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key.split('.')
        value = self._config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, key: str, value: Any) -> None:
        """
        Set configuration value using dot notation
        
        Args:
            key: Configuration key using dot notation
            value: Value to set
        """
        self._set_nested_value(self._config, key, value)
    
    def reload(self) -> None:
        """Reload configuration from file and environment"""
        self._load_config()
        self.logger.info("Configuration reloaded")
    
    def save(self, path: Optional[str] = None) -> None:
        """
        Save current configuration to JSON file
        
        Args:
            path: Path to save config. Uses default path if None.
        """
        save_path = Path(path) if path else self.config_path
        
        try:
            # Create parent directory if it doesn't exist
            save_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(save_path, 'w') as f:
                json.dump(self._config, f, indent=2, sort_keys=True)
            
            self.logger.info(f"Configuration saved to {save_path}")
        except OSError as e:
            self.logger.error(f"Failed to save config to {save_path}: {e}")
            raise
    
    def get_all(self) -> Dict[str, Any]:
        """Get complete configuration dictionary"""
        return self._config.copy()
    
    def get_github_config(self) -> Dict[str, Any]:
        """Get GitHub-specific configuration"""
        return self.get("github", {})
    
    def get_service_config(self) -> Dict[str, Any]:
        """Get service-specific configuration"""
        return self.get("service", {})
    
    def get_claude_flow_config(self) -> Dict[str, Any]:
        """Get Claude Flow-specific configuration"""
        return self.get("claude_flow", {})
    
    def is_valid(self) -> bool:
        """Check if current configuration is valid"""
        try:
            self._validate_config()
            return True
        except ValueError:
            return False


# Global config instance for convenience
_config_instance = None

def get_config(config_path: Optional[str] = None) -> ConfigManager:
    """Get global configuration instance"""
    global _config_instance
    if _config_instance is None or config_path is not None:
        _config_instance = ConfigManager(config_path)
    return _config_instance


if __name__ == "__main__":
    # Example usage and testing
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    try:
        config = ConfigManager()
        print("Configuration loaded successfully")
        print(f"GitHub repo: {config.get('github.owner')}/{config.get('github.repo')}")
        print(f"Poll interval: {config.get('service.poll_interval_seconds')} seconds")
        print(f"Max concurrent: {config.get('service.max_concurrent_issues')}")
        
        # Test environment override
        os.environ["CLAUDE_FLOW_POLL_INTERVAL"] = "600"
        config.reload()
        print(f"Poll interval after env override: {config.get('service.poll_interval_seconds')} seconds")
        
    except Exception as e:
        print(f"Configuration error: {e}")
        sys.exit(1)