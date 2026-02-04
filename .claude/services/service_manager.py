#!/usr/bin/env python3
"""
Service Manager for Claude Flow Service

Handles systemd service operations: start, stop, restart, status, and health checks.
"""

import json
import logging
import subprocess
import time
import signal
import psutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from enum import Enum

from config_manager import ConfigManager


class ServiceStatus(Enum):
    """Service status states"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    STARTING = "starting"
    STOPPING = "stopping"
    UNKNOWN = "unknown"


class ServiceManager:
    """Manages Claude Flow systemd service lifecycle and monitoring"""
    
    def __init__(self, config: Optional[ConfigManager] = None):
        """
        Initialize service manager
        
        Args:
            config: Configuration manager instance. Creates new one if None.
        """
        self.logger = logging.getLogger(__name__)
        self.config = config or ConfigManager()
        
        # Service configuration
        self.service_name = self.config.get("systemd.service_name", "claude-flow-service")
        self.status_file = Path(self.config.get("monitoring.status_file", "/var/lib/claude-flow/status.json"))
        self.health_check_enabled = self.config.get("monitoring.health_check_enabled", True)
        self.health_check_port = self.config.get("service.health_check_port", 8080)
        
        # Ensure status file directory exists
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
    
    def start(self, wait_for_active: bool = True, timeout: int = 30) -> bool:
        """
        Start the systemd service
        
        Args:
            wait_for_active: Wait for service to become active
            timeout: Timeout in seconds for waiting
            
        Returns:
            True if service started successfully
        """
        try:
            self.logger.info(f"Starting service: {self.service_name}")
            
            # Start the service
            result = subprocess.run([
                "sudo", "systemctl", "start", self.service_name
            ], capture_output=True, text=True, timeout=timeout)
            
            if result.returncode != 0:
                self.logger.error(f"Failed to start service: {result.stderr}")
                return False
            
            # Wait for service to become active if requested
            if wait_for_active:
                return self._wait_for_status(ServiceStatus.ACTIVE, timeout)
            
            return True
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout starting service after {timeout} seconds")
            return False
        except Exception as e:
            self.logger.error(f"Error starting service: {e}")
            return False
    
    def stop(self, graceful: bool = True, timeout: int = 30) -> bool:
        """
        Stop the systemd service
        
        Args:
            graceful: Use graceful shutdown (SIGTERM) vs force (SIGKILL)
            timeout: Timeout in seconds for graceful shutdown
            
        Returns:
            True if service stopped successfully
        """
        try:
            self.logger.info(f"Stopping service: {self.service_name}")
            
            if graceful:
                # Try graceful shutdown first
                result = subprocess.run([
                    "sudo", "systemctl", "stop", self.service_name
                ], capture_output=True, text=True, timeout=timeout)
                
                if result.returncode == 0:
                    return self._wait_for_status(ServiceStatus.INACTIVE, timeout)
            
            # Force stop if graceful failed or not requested
            self.logger.warning("Using force stop")
            result = subprocess.run([
                "sudo", "systemctl", "kill", "--signal=SIGKILL", self.service_name
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                self.logger.error(f"Failed to stop service: {result.stderr}")
                return False
            
            return self._wait_for_status(ServiceStatus.INACTIVE, 10)
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout stopping service after {timeout} seconds")
            return False
        except Exception as e:
            self.logger.error(f"Error stopping service: {e}")
            return False
    
    def restart(self, timeout: int = 60) -> bool:
        """
        Restart the systemd service
        
        Args:
            timeout: Total timeout for restart operation
            
        Returns:
            True if service restarted successfully
        """
        try:
            self.logger.info(f"Restarting service: {self.service_name}")
            
            result = subprocess.run([
                "sudo", "systemctl", "restart", self.service_name
            ], capture_output=True, text=True, timeout=timeout)
            
            if result.returncode != 0:
                self.logger.error(f"Failed to restart service: {result.stderr}")
                return False
            
            # Wait for service to become active
            return self._wait_for_status(ServiceStatus.ACTIVE, timeout // 2)
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout restarting service after {timeout} seconds")
            return False
        except Exception as e:
            self.logger.error(f"Error restarting service: {e}")
            return False
    
    def reload_config(self) -> bool:
        """
        Reload service configuration without full restart
        
        Returns:
            True if config reloaded successfully
        """
        try:
            self.logger.info(f"Reloading configuration for service: {self.service_name}")
            
            # Send SIGHUP to reload config
            result = subprocess.run([
                "sudo", "systemctl", "kill", "--signal=SIGHUP", self.service_name
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                self.logger.error(f"Failed to reload config: {result.stderr}")
                return False
            
            self.logger.info("Configuration reload signal sent")
            return True
            
        except Exception as e:
            self.logger.error(f"Error reloading config: {e}")
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive service status information
        
        Returns:
            Dictionary containing service status information
        """
        status_info = {
            "service_name": self.service_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "systemd_status": self._get_systemd_status(),
            "process_info": self._get_process_info(),
            "health_check": self._get_health_status(),
            "resource_usage": self._get_resource_usage(),
            "configuration": self._get_config_summary(),
            "logs": self._get_recent_logs(10)
        }
        
        # Save status to file
        self._save_status(status_info)
        
        return status_info
    
    def is_active(self) -> bool:
        """Check if service is currently active"""
        systemd_status = self._get_systemd_status()
        return systemd_status.get("status") == ServiceStatus.ACTIVE.value
    
    def is_healthy(self) -> bool:
        """Check if service is healthy (active and responding to health checks)"""
        if not self.is_active():
            return False
        
        if not self.health_check_enabled:
            return True
        
        health_status = self._get_health_status()
        return health_status.get("healthy", False)
    
    def enable_autostart(self) -> bool:
        """Enable automatic startup on boot"""
        try:
            result = subprocess.run([
                "sudo", "systemctl", "enable", self.service_name
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                self.logger.error(f"Failed to enable autostart: {result.stderr}")
                return False
            
            self.logger.info(f"Autostart enabled for {self.service_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error enabling autostart: {e}")
            return False
    
    def disable_autostart(self) -> bool:
        """Disable automatic startup on boot"""
        try:
            result = subprocess.run([
                "sudo", "systemctl", "disable", self.service_name
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                self.logger.error(f"Failed to disable autostart: {result.stderr}")
                return False
            
            self.logger.info(f"Autostart disabled for {self.service_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error disabling autostart: {e}")
            return False
    
    def _get_systemd_status(self) -> Dict[str, Any]:
        """Get systemd service status"""
        try:
            result = subprocess.run([
                "systemctl", "show", self.service_name,
                "--property=ActiveState,SubState,LoadState,UnitFileState,ExecMainStartTimestamp,ExecMainPID"
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                return {"status": ServiceStatus.UNKNOWN.value, "error": result.stderr}
            
            # Parse systemctl output
            properties = {}
            for line in result.stdout.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    properties[key] = value
            
            # Map systemd states to our enum
            active_state = properties.get('ActiveState', 'unknown')
            status_mapping = {
                'active': ServiceStatus.ACTIVE,
                'inactive': ServiceStatus.INACTIVE,
                'failed': ServiceStatus.FAILED,
                'activating': ServiceStatus.STARTING,
                'deactivating': ServiceStatus.STOPPING,
            }
            
            return {
                "status": status_mapping.get(active_state, ServiceStatus.UNKNOWN).value,
                "sub_state": properties.get('SubState', 'unknown'),
                "load_state": properties.get('LoadState', 'unknown'),
                "enabled": properties.get('UnitFileState', 'unknown') == 'enabled',
                "pid": int(properties.get('ExecMainPID', 0)) or None,
                "start_time": properties.get('ExecMainStartTimestamp', 'unknown')
            }
            
        except Exception as e:
            self.logger.error(f"Error getting systemd status: {e}")
            return {"status": ServiceStatus.UNKNOWN.value, "error": str(e)}
    
    def _get_process_info(self) -> Optional[Dict[str, Any]]:
        """Get process information if service is running"""
        systemd_status = self._get_systemd_status()
        pid = systemd_status.get('pid')
        
        if not pid:
            return None
        
        try:
            process = psutil.Process(pid)
            return {
                "pid": pid,
                "ppid": process.ppid(),
                "name": process.name(),
                "cmdline": process.cmdline(),
                "status": process.status(),
                "create_time": process.create_time(),
                "num_threads": process.num_threads(),
                "memory_info": process.memory_info()._asdict(),
                "cpu_percent": process.cpu_percent(),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            self.logger.warning(f"Could not get process info: {e}")
            return {"error": str(e)}
    
    def _get_health_status(self) -> Dict[str, Any]:
        """Get health check status"""
        if not self.health_check_enabled:
            return {"enabled": False}
        
        try:
            import requests
            response = requests.get(
                f"http://localhost:{self.health_check_port}/health",
                timeout=5
            )
            
            return {
                "enabled": True,
                "healthy": response.status_code == 200,
                "status_code": response.status_code,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
                "details": response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
            }
        except Exception as e:
            return {
                "enabled": True,
                "healthy": False,
                "error": str(e)
            }
    
    def _get_resource_usage(self) -> Dict[str, Any]:
        """Get system resource usage for the service"""
        systemd_status = self._get_systemd_status()
        pid = systemd_status.get('pid')
        
        if not pid:
            return {"available": False}
        
        try:
            process = psutil.Process(pid)
            
            # Get process and children resource usage
            children = process.children(recursive=True)
            total_memory = process.memory_info().rss
            total_cpu = process.cpu_percent()
            
            for child in children:
                try:
                    total_memory += child.memory_info().rss
                    total_cpu += child.cpu_percent()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            return {
                "available": True,
                "memory_bytes": total_memory,
                "memory_mb": round(total_memory / (1024 * 1024), 2),
                "cpu_percent": round(total_cpu, 2),
                "num_processes": 1 + len(children),
                "open_files": len(process.open_files()),
                "connections": len(process.connections())
            }
        except Exception as e:
            return {"available": False, "error": str(e)}
    
    def _get_config_summary(self) -> Dict[str, Any]:
        """Get configuration summary"""
        return {
            "config_valid": self.config.is_valid(),
            "github_configured": bool(self.config.get("github.token") and 
                                    self.config.get("github.owner") and 
                                    self.config.get("github.repo")),
            "poll_interval": self.config.get("service.poll_interval_seconds"),
            "max_concurrent": self.config.get("service.max_concurrent_issues"),
            "log_level": self.config.get("service.log_level")
        }
    
    def _get_recent_logs(self, num_lines: int = 10) -> List[str]:
        """Get recent service logs"""
        try:
            result = subprocess.run([
                "journalctl", "-u", self.service_name, "--no-pager", 
                "-n", str(num_lines), "--output=short-iso"
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                return result.stdout.strip().split('\n')
            else:
                return [f"Error getting logs: {result.stderr}"]
        except Exception as e:
            return [f"Error getting logs: {str(e)}"]
    
    def _save_status(self, status_info: Dict[str, Any]) -> None:
        """Save status information to file"""
        try:
            with open(self.status_file, 'w') as f:
                json.dump(status_info, f, indent=2, default=str)
        except Exception as e:
            self.logger.warning(f"Could not save status to file: {e}")
    
    def _wait_for_status(self, expected_status: ServiceStatus, timeout: int) -> bool:
        """Wait for service to reach expected status"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            systemd_status = self._get_systemd_status()
            current_status = systemd_status.get("status")
            
            if current_status == expected_status.value:
                return True
            elif current_status == ServiceStatus.FAILED.value:
                self.logger.error("Service failed during status wait")
                return False
            
            time.sleep(1)
        
        self.logger.error(f"Timeout waiting for status {expected_status.value}")
        return False


def main():
    """Command-line interface for service management"""
    import argparse
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description='Claude Flow Service Manager')
    parser.add_argument('command', choices=['start', 'stop', 'restart', 'status', 'reload', 'enable', 'disable', 'health'],
                       help='Service command to execute')
    parser.add_argument('--timeout', type=int, default=30, help='Timeout in seconds')
    parser.add_argument('--config', help='Path to configuration file')
    parser.add_argument('--json', action='store_true', help='Output status in JSON format')
    
    args = parser.parse_args()
    
    try:
        config = ConfigManager(args.config) if args.config else ConfigManager()
        manager = ServiceManager(config)
        
        if args.command == 'start':
            success = manager.start(timeout=args.timeout)
            print(f"Service start: {'SUCCESS' if success else 'FAILED'}")
            sys.exit(0 if success else 1)
            
        elif args.command == 'stop':
            success = manager.stop(timeout=args.timeout)
            print(f"Service stop: {'SUCCESS' if success else 'FAILED'}")
            sys.exit(0 if success else 1)
            
        elif args.command == 'restart':
            success = manager.restart(timeout=args.timeout)
            print(f"Service restart: {'SUCCESS' if success else 'FAILED'}")
            sys.exit(0 if success else 1)
            
        elif args.command == 'reload':
            success = manager.reload_config()
            print(f"Config reload: {'SUCCESS' if success else 'FAILED'}")
            sys.exit(0 if success else 1)
            
        elif args.command == 'enable':
            success = manager.enable_autostart()
            print(f"Autostart enable: {'SUCCESS' if success else 'FAILED'}")
            sys.exit(0 if success else 1)
            
        elif args.command == 'disable':
            success = manager.disable_autostart()
            print(f"Autostart disable: {'SUCCESS' if success else 'FAILED'}")
            sys.exit(0 if success else 1)
            
        elif args.command == 'status':
            status = manager.get_status()
            if args.json:
                print(json.dumps(status, indent=2, default=str))
            else:
                print(f"Service: {status['service_name']}")
                print(f"Status: {status['systemd_status']['status']}")
                print(f"Healthy: {manager.is_healthy()}")
                if status['process_info']:
                    print(f"PID: {status['process_info']['pid']}")
                    print(f"Memory: {status['resource_usage']['memory_mb']} MB")
                    print(f"CPU: {status['resource_usage']['cpu_percent']}%")
            sys.exit(0)
            
        elif args.command == 'health':
            healthy = manager.is_healthy()
            print(f"Service health: {'HEALTHY' if healthy else 'UNHEALTHY'}")
            sys.exit(0 if healthy else 1)
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()