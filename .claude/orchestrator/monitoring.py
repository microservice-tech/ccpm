#!/usr/bin/env python3
"""
Monitoring - Comprehensive health checks, metrics, and monitoring integration

Provides centralized monitoring capabilities for the orchestrator system including:
- Health checks for all components
- Performance metrics collection
- Alerting and notification systems
- Status dashboards and reporting
- Integration with external monitoring systems
"""

import logging
import time
import threading
import json
import statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Tuple, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import deque, defaultdict
import psutil

from service_bridge import ServiceBridge, BridgeStatus, ServiceHealth
from workflow_bridge import WorkflowBridge, WorkflowState, ExecutionStats


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class MetricType(Enum):
    """Types of metrics collected"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class Alert:
    """Alert notification"""
    id: str
    severity: AlertSeverity
    title: str
    description: str
    component: str
    timestamp: datetime
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthCheckResult:
    """Result of a health check"""
    component: str
    healthy: bool
    response_time_ms: float
    details: Dict[str, Any]
    timestamp: datetime
    error_message: Optional[str] = None


@dataclass
class MetricPoint:
    """Single metric data point"""
    name: str
    value: Union[int, float]
    timestamp: datetime
    labels: Dict[str, str] = field(default_factory=dict)
    metric_type: MetricType = MetricType.GAUGE


@dataclass
class MonitoringConfig:
    """Configuration for monitoring system"""
    health_check_interval_seconds: int = 30
    metrics_collection_interval_seconds: int = 60
    alert_retention_days: int = 30
    metrics_retention_days: int = 7
    enable_system_metrics: bool = True
    enable_performance_profiling: bool = True
    external_monitoring_url: Optional[str] = None
    webhook_urls: List[str] = field(default_factory=list)


class MonitoringSystem:
    """
    Comprehensive monitoring system for the orchestrator
    
    Features:
    - Continuous health monitoring
    - Performance metrics collection
    - Alert management and notifications
    - Historical data tracking
    - Integration with external monitoring systems
    """
    
    def __init__(self,
                 service_bridge: ServiceBridge,
                 workflow_bridge: WorkflowBridge,
                 config: Optional[MonitoringConfig] = None):
        """
        Initialize the monitoring system
        
        Args:
            service_bridge: Service bridge for health checks
            workflow_bridge: Workflow bridge for execution metrics
            config: Monitoring configuration
        """
        self.logger = logging.getLogger(__name__)
        self.service_bridge = service_bridge
        self.workflow_bridge = workflow_bridge
        self.config = config or MonitoringConfig()
        
        # Data storage
        self._alerts: Dict[str, Alert] = {}
        self._health_history: deque = deque(maxlen=1000)
        self._metrics: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._system_metrics: deque = deque(maxlen=1000)
        
        # Threading
        self._lock = threading.RLock()
        self._should_stop = threading.Event()
        
        # Health check registry
        self._health_checks: Dict[str, Callable[[], HealthCheckResult]] = {}
        self._register_default_health_checks()
        
        # Metric collectors
        self._metric_collectors: Dict[str, Callable[[], List[MetricPoint]]] = {}
        self._register_default_metric_collectors()
        
        # Alert handlers
        self._alert_handlers: List[Callable[[Alert], None]] = []
        self._register_default_alert_handlers()
        
        # Background threads
        self._health_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self._metrics_thread = threading.Thread(target=self._metrics_collection_loop, daemon=True)
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        
        # Start monitoring
        self.start()
        
        self.logger.info("Monitoring system initialized")
    
    def start(self) -> None:
        """Start monitoring threads"""
        if not self._should_stop.is_set():
            self.logger.warning("Monitoring already started")
            return
        
        self._should_stop.clear()
        
        if not self._health_thread.is_alive():
            self._health_thread = threading.Thread(target=self._health_check_loop, daemon=True)
            self._health_thread.start()
        
        if not self._metrics_thread.is_alive():
            self._metrics_thread = threading.Thread(target=self._metrics_collection_loop, daemon=True)
            self._metrics_thread.start()
        
        if not self._cleanup_thread.is_alive():
            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self._cleanup_thread.start()
        
        self.logger.info("Monitoring threads started")
    
    def stop(self) -> None:
        """Stop monitoring threads"""
        self._should_stop.set()
        self.logger.info("Monitoring threads stopped")
    
    def add_health_check(self, name: str, check_func: Callable[[], HealthCheckResult]) -> None:
        """Add a custom health check"""
        with self._lock:
            self._health_checks[name] = check_func
            self.logger.info(f"Added health check: {name}")
    
    def add_metric_collector(self, name: str, collector_func: Callable[[], List[MetricPoint]]) -> None:
        """Add a custom metric collector"""
        with self._lock:
            self._metric_collectors[name] = collector_func
            self.logger.info(f"Added metric collector: {name}")
    
    def add_alert_handler(self, handler: Callable[[Alert], None]) -> None:
        """Add a custom alert handler"""
        with self._lock:
            self._alert_handlers.append(handler)
            self.logger.info("Added alert handler")
    
    def get_current_health(self) -> Dict[str, HealthCheckResult]:
        """Get current health status for all components"""
        results = {}
        
        for name, check_func in self._health_checks.items():
            try:
                start_time = time.time()
                result = check_func()
                if result.response_time_ms == 0:
                    result.response_time_ms = (time.time() - start_time) * 1000
                results[name] = result
            except Exception as e:
                results[name] = HealthCheckResult(
                    component=name,
                    healthy=False,
                    response_time_ms=0,
                    details={},
                    timestamp=datetime.now(timezone.utc),
                    error_message=str(e)
                )
        
        return results
    
    def get_overall_health_status(self) -> Tuple[BridgeStatus, Dict[str, Any]]:
        """Get overall system health status"""
        health_results = self.get_current_health()
        
        healthy_count = sum(1 for r in health_results.values() if r.healthy)
        total_count = len(health_results)
        
        if total_count == 0:
            return BridgeStatus.UNKNOWN, {"error": "No health checks configured"}
        
        health_ratio = healthy_count / total_count
        
        if health_ratio >= 1.0:
            status = BridgeStatus.HEALTHY
        elif health_ratio >= 0.7:
            status = BridgeStatus.DEGRADED
        else:
            status = BridgeStatus.UNHEALTHY
        
        summary = {
            "status": status.value,
            "healthy_components": healthy_count,
            "total_components": total_count,
            "health_ratio": health_ratio,
            "component_details": {name: asdict(result) for name, result in health_results.items()},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return status, summary
    
    def get_alerts(self, severity: Optional[AlertSeverity] = None, 
                   resolved: Optional[bool] = None) -> List[Alert]:
        """
        Get alerts with optional filtering
        
        Args:
            severity: Filter by severity level
            resolved: Filter by resolved status
            
        Returns:
            List of matching alerts
        """
        with self._lock:
            alerts = list(self._alerts.values())
        
        if severity is not None:
            alerts = [a for a in alerts if a.severity == severity]
        
        if resolved is not None:
            alerts = [a for a in alerts if a.resolved == resolved]
        
        # Sort by timestamp, most recent first
        alerts.sort(key=lambda a: a.timestamp, reverse=True)
        
        return alerts
    
    def get_metrics(self, metric_name: Optional[str] = None,
                   since: Optional[datetime] = None,
                   limit: Optional[int] = None) -> Dict[str, List[MetricPoint]]:
        """
        Get collected metrics
        
        Args:
            metric_name: Specific metric to retrieve (None for all)
            since: Only return metrics after this timestamp
            limit: Maximum number of points per metric
            
        Returns:
            Dictionary mapping metric names to lists of metric points
        """
        results = {}
        
        with self._lock:
            metric_names = [metric_name] if metric_name else list(self._metrics.keys())
            
            for name in metric_names:
                if name not in self._metrics:
                    continue
                
                points = list(self._metrics[name])
                
                if since:
                    points = [p for p in points if p.timestamp >= since]
                
                if limit:
                    points = points[-limit:]
                
                results[name] = points
        
        return results
    
    def get_system_metrics(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Get system performance metrics"""
        with self._lock:
            metrics = list(self._system_metrics)
        
        if since:
            metrics = [m for m in metrics if datetime.fromisoformat(m['timestamp'].replace('Z', '+00:00')) >= since]
        
        return metrics
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive dashboard data"""
        overall_status, health_summary = self.get_overall_health_status()
        workflow_stats = self.workflow_bridge.get_execution_stats()
        queue_status = self.workflow_bridge.get_queue_status()
        recent_alerts = self.get_alerts(resolved=False)[:10]  # Last 10 unresolved
        
        # Get recent metrics
        now = datetime.now(timezone.utc)
        hour_ago = now - timedelta(hours=1)
        recent_metrics = self.get_metrics(since=hour_ago)
        
        return {
            "overall_status": overall_status.value,
            "health_summary": health_summary,
            "workflow_stats": asdict(workflow_stats),
            "queue_status": queue_status,
            "recent_alerts": [asdict(alert) for alert in recent_alerts],
            "metrics_summary": {
                name: {
                    "count": len(points),
                    "latest_value": points[-1].value if points else None,
                    "latest_timestamp": points[-1].timestamp.isoformat() if points else None
                }
                for name, points in recent_metrics.items()
            },
            "timestamp": now.isoformat()
        }
    
    def trigger_alert(self, 
                     alert_id: str,
                     severity: AlertSeverity,
                     title: str,
                     description: str,
                     component: str,
                     metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Trigger a new alert
        
        Args:
            alert_id: Unique identifier for the alert
            severity: Alert severity level
            title: Alert title
            description: Alert description
            component: Component that triggered the alert
            metadata: Additional metadata
        """
        alert = Alert(
            id=alert_id,
            severity=severity,
            title=title,
            description=description,
            component=component,
            timestamp=datetime.now(timezone.utc),
            metadata=metadata or {}
        )
        
        with self._lock:
            # Check if alert already exists and is unresolved
            existing = self._alerts.get(alert_id)
            if existing and not existing.resolved:
                self.logger.debug(f"Alert {alert_id} already active")
                return
            
            self._alerts[alert_id] = alert
        
        # Notify handlers
        for handler in self._alert_handlers:
            try:
                handler(alert)
            except Exception as e:
                self.logger.error(f"Error in alert handler: {e}")
        
        self.logger.warning(f"ALERT [{severity.value.upper()}] {title}: {description}")
    
    def resolve_alert(self, alert_id: str) -> bool:
        """
        Resolve an active alert
        
        Args:
            alert_id: ID of alert to resolve
            
        Returns:
            True if alert was resolved
        """
        with self._lock:
            alert = self._alerts.get(alert_id)
            if not alert or alert.resolved:
                return False
            
            alert.resolved = True
            alert.resolved_at = datetime.now(timezone.utc)
        
        self.logger.info(f"Resolved alert: {alert_id}")
        return True
    
    def _register_default_health_checks(self) -> None:
        """Register default health checks"""
        
        def service_bridge_health() -> HealthCheckResult:
            try:
                status, summary = self.service_bridge.get_overall_health()
                return HealthCheckResult(
                    component="service_bridge",
                    healthy=(status == BridgeStatus.HEALTHY),
                    response_time_ms=0,  # Will be measured by caller
                    details=summary,
                    timestamp=datetime.now(timezone.utc)
                )
            except Exception as e:
                return HealthCheckResult(
                    component="service_bridge",
                    healthy=False,
                    response_time_ms=0,
                    details={},
                    timestamp=datetime.now(timezone.utc),
                    error_message=str(e)
                )
        
        def workflow_bridge_health() -> HealthCheckResult:
            try:
                queue_status = self.workflow_bridge.get_queue_status()
                stats = self.workflow_bridge.get_execution_stats()
                
                # Consider healthy if not completely overloaded
                queue_healthy = queue_status["queue_size"] < queue_status["max_queue_size"] * 0.9
                execution_healthy = queue_status["running_count"] <= queue_status["max_concurrent"]
                
                healthy = queue_healthy and execution_healthy
                
                return HealthCheckResult(
                    component="workflow_bridge",
                    healthy=healthy,
                    response_time_ms=0,
                    details={"queue_status": queue_status, "stats": asdict(stats)},
                    timestamp=datetime.now(timezone.utc)
                )
            except Exception as e:
                return HealthCheckResult(
                    component="workflow_bridge", 
                    healthy=False,
                    response_time_ms=0,
                    details={},
                    timestamp=datetime.now(timezone.utc),
                    error_message=str(e)
                )
        
        def system_resources_health() -> HealthCheckResult:
            try:
                cpu_percent = psutil.cpu_percent(interval=1)
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage('/')
                
                # Thresholds for health
                cpu_healthy = cpu_percent < 80
                memory_healthy = memory.percent < 85
                disk_healthy = disk.percent < 90
                
                healthy = cpu_healthy and memory_healthy and disk_healthy
                
                details = {
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "disk_percent": disk.percent,
                    "memory_available_gb": memory.available / (1024**3),
                    "disk_free_gb": disk.free / (1024**3)
                }
                
                return HealthCheckResult(
                    component="system_resources",
                    healthy=healthy,
                    response_time_ms=0,
                    details=details,
                    timestamp=datetime.now(timezone.utc)
                )
            except Exception as e:
                return HealthCheckResult(
                    component="system_resources",
                    healthy=False,
                    response_time_ms=0,
                    details={},
                    timestamp=datetime.now(timezone.utc),
                    error_message=str(e)
                )
        
        self._health_checks["service_bridge"] = service_bridge_health
        self._health_checks["workflow_bridge"] = workflow_bridge_health
        if self.config.enable_system_metrics:
            self._health_checks["system_resources"] = system_resources_health
    
    def _register_default_metric_collectors(self) -> None:
        """Register default metric collectors"""
        
        def workflow_metrics() -> List[MetricPoint]:
            try:
                stats = self.workflow_bridge.get_execution_stats()
                queue_status = self.workflow_bridge.get_queue_status()
                timestamp = datetime.now(timezone.utc)
                
                return [
                    MetricPoint("workflow_total_tasks", stats.total_tasks, timestamp, metric_type=MetricType.GAUGE),
                    MetricPoint("workflow_pending_tasks", stats.pending_tasks, timestamp, metric_type=MetricType.GAUGE),
                    MetricPoint("workflow_running_tasks", stats.running_tasks, timestamp, metric_type=MetricType.GAUGE),
                    MetricPoint("workflow_completed_tasks", stats.completed_tasks, timestamp, metric_type=MetricType.COUNTER),
                    MetricPoint("workflow_failed_tasks", stats.failed_tasks, timestamp, metric_type=MetricType.COUNTER),
                    MetricPoint("workflow_success_rate", stats.success_rate, timestamp, metric_type=MetricType.GAUGE),
                    MetricPoint("workflow_avg_execution_time", stats.average_execution_time, timestamp, metric_type=MetricType.GAUGE),
                    MetricPoint("workflow_tasks_per_hour", stats.tasks_per_hour, timestamp, metric_type=MetricType.GAUGE),
                    MetricPoint("workflow_queue_size", queue_status["queue_size"], timestamp, metric_type=MetricType.GAUGE)
                ]
            except Exception as e:
                self.logger.error(f"Error collecting workflow metrics: {e}")
                return []
        
        def service_metrics() -> List[MetricPoint]:
            try:
                service_health = self.service_bridge.get_service_health()
                timestamp = datetime.now(timezone.utc)
                
                metrics = []
                for name, health in service_health.items():
                    healthy_value = 1 if health.status == BridgeStatus.HEALTHY else 0
                    metrics.append(MetricPoint(
                        f"service_{name}_healthy", 
                        healthy_value, 
                        timestamp, 
                        labels={"component": name},
                        metric_type=MetricType.GAUGE
                    ))
                
                return metrics
            except Exception as e:
                self.logger.error(f"Error collecting service metrics: {e}")
                return []
        
        self._metric_collectors["workflow"] = workflow_metrics
        self._metric_collectors["service"] = service_metrics
    
    def _register_default_alert_handlers(self) -> None:
        """Register default alert handlers"""
        
        def log_alert_handler(alert: Alert) -> None:
            level = {
                AlertSeverity.INFO: logging.INFO,
                AlertSeverity.WARNING: logging.WARNING, 
                AlertSeverity.ERROR: logging.ERROR,
                AlertSeverity.CRITICAL: logging.CRITICAL
            }.get(alert.severity, logging.INFO)
            
            self.logger.log(level, f"ALERT: {alert.title} - {alert.description}")
        
        self._alert_handlers.append(log_alert_handler)
    
    def _health_check_loop(self) -> None:
        """Background thread for continuous health checking"""
        self.logger.info("Health check loop started")
        
        while not self._should_stop.wait(self.config.health_check_interval_seconds):
            try:
                health_results = self.get_current_health()
                
                with self._lock:
                    # Store health history
                    health_snapshot = {
                        "timestamp": datetime.now(timezone.utc),
                        "results": health_results
                    }
                    self._health_history.append(health_snapshot)
                
                # Check for health issues and trigger alerts
                for name, result in health_results.items():
                    alert_id = f"health_check_{name}"
                    
                    if not result.healthy:
                        self.trigger_alert(
                            alert_id=alert_id,
                            severity=AlertSeverity.ERROR,
                            title=f"Health Check Failed: {name}",
                            description=result.error_message or f"Component {name} is unhealthy",
                            component=name,
                            metadata={"health_result": asdict(result)}
                        )
                    else:
                        # Resolve alert if component is now healthy
                        self.resolve_alert(alert_id)
                
            except Exception as e:
                self.logger.error(f"Error in health check loop: {e}")
        
        self.logger.info("Health check loop stopped")
    
    def _metrics_collection_loop(self) -> None:
        """Background thread for metrics collection"""
        self.logger.info("Metrics collection loop started")
        
        while not self._should_stop.wait(self.config.metrics_collection_interval_seconds):
            try:
                # Collect custom metrics
                for name, collector in self._metric_collectors.items():
                    try:
                        metrics = collector()
                        for metric in metrics:
                            with self._lock:
                                self._metrics[metric.name].append(metric)
                    except Exception as e:
                        self.logger.error(f"Error collecting {name} metrics: {e}")
                
                # Collect system metrics if enabled
                if self.config.enable_system_metrics:
                    try:
                        system_data = {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "cpu_percent": psutil.cpu_percent(),
                            "memory": psutil.virtual_memory()._asdict(),
                            "disk": psutil.disk_usage('/')._asdict(),
                            "network": psutil.net_io_counters()._asdict(),
                            "processes": len(psutil.pids())
                        }
                        
                        with self._lock:
                            self._system_metrics.append(system_data)
                    except Exception as e:
                        self.logger.error(f"Error collecting system metrics: {e}")
                
            except Exception as e:
                self.logger.error(f"Error in metrics collection loop: {e}")
        
        self.logger.info("Metrics collection loop stopped")
    
    def _cleanup_loop(self) -> None:
        """Background thread for data cleanup"""
        self.logger.info("Cleanup loop started")
        
        while not self._should_stop.wait(3600):  # Run every hour
            try:
                current_time = datetime.now(timezone.utc)
                
                # Clean up old alerts
                alert_cutoff = current_time - timedelta(days=self.config.alert_retention_days)
                with self._lock:
                    expired_alerts = [
                        alert_id for alert_id, alert in self._alerts.items()
                        if alert.resolved and alert.resolved_at and alert.resolved_at < alert_cutoff
                    ]
                    for alert_id in expired_alerts:
                        del self._alerts[alert_id]
                    
                    if expired_alerts:
                        self.logger.info(f"Cleaned up {len(expired_alerts)} expired alerts")
                
                # Clean up old metrics (handled by deque maxlen, but log it)
                total_metrics = sum(len(deque) for deque in self._metrics.values())
                self.logger.debug(f"Currently storing {total_metrics} metric points")
                
            except Exception as e:
                self.logger.error(f"Error in cleanup loop: {e}")
        
        self.logger.info("Cleanup loop stopped")
    
    def export_metrics(self, format_type: str = "json") -> str:
        """
        Export metrics in various formats
        
        Args:
            format_type: Export format ("json", "prometheus", "csv")
            
        Returns:
            Formatted metrics string
        """
        if format_type == "json":
            return self._export_json_metrics()
        elif format_type == "prometheus":
            return self._export_prometheus_metrics()
        elif format_type == "csv":
            return self._export_csv_metrics()
        else:
            raise ValueError(f"Unsupported format: {format_type}")
    
    def _export_json_metrics(self) -> str:
        """Export metrics as JSON"""
        with self._lock:
            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": {
                    name: [asdict(point) for point in points]
                    for name, points in self._metrics.items()
                }
            }
            return json.dumps(data, indent=2, default=str)
    
    def _export_prometheus_metrics(self) -> str:
        """Export metrics in Prometheus format"""
        lines = []
        
        with self._lock:
            for name, points in self._metrics.items():
                if not points:
                    continue
                
                latest_point = points[-1]
                # Convert name to Prometheus format
                prom_name = name.replace("-", "_").replace(".", "_")
                
                # Add help and type comments
                lines.append(f"# HELP {prom_name} {name}")
                lines.append(f"# TYPE {prom_name} {latest_point.metric_type.value}")
                
                # Add metric line
                labels_str = ",".join([f'{k}="{v}"' for k, v in latest_point.labels.items()])
                if labels_str:
                    lines.append(f"{prom_name}{{{labels_str}}} {latest_point.value}")
                else:
                    lines.append(f"{prom_name} {latest_point.value}")
        
        return "\n".join(lines)
    
    def _export_csv_metrics(self) -> str:
        """Export metrics as CSV"""
        lines = ["timestamp,metric_name,value,labels"]
        
        with self._lock:
            for name, points in self._metrics.items():
                for point in points:
                    labels_str = ";".join([f"{k}={v}" for k, v in point.labels.items()])
                    lines.append(f"{point.timestamp.isoformat()},{name},{point.value},{labels_str}")
        
        return "\n".join(lines)