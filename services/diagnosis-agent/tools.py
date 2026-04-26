"""
Diagnosis Agent — Tool Definitions & MCP Executor.

This module:
1. Defines the tool schemas that Claude sees (JSON Schema format)
2. Implements the httpx-based executor that routes Claude's tool_use
   requests to the correct MCP server
"""
from __future__ import annotations
import httpx
import json
from shared.config import settings
from shared.logger import get_logger

log = get_logger("diagnosis-tools")

# ─── Tool Schemas ─────────────────────────────────────────────────────
# These are sent to Claude so it knows what tools are available.
# Claude will return tool_use blocks referencing these names.

DIAGNOSTIC_TOOLS = [
    # ── K8s MCP Tools ──────────────────────────────────────────────
    {
        "name": "get_pod_status",
        "description": "Get the status of pods for a specific service in a Kubernetes namespace. Shows pod names, phase (Running/CrashLoopBackOff), restart counts, and creation time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string", "description": "The tenant ID from the incident context"},
                "namespace": {"type": "string", "description": "Kubernetes namespace (e.g. 'default', 'production')"},
                "service": {"type": "string", "description": "Service name to filter pods by (matches app= label)"},
            },
            "required": ["tenant_id", "namespace", "service"],
        },
    },
    {
        "name": "get_pod_logs",
        "description": "Get the last N log lines for a specific pod. Use previous=true to get logs from a crashed container.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "namespace": {"type": "string"},
                "pod_name": {"type": "string", "description": "Exact pod name"},
                "tail": {"type": "integer", "default": 100},
                "previous": {"type": "boolean", "default": False, "description": "If true, gets logs from the previous (crashed) container"},
            },
            "required": ["tenant_id", "namespace", "pod_name"],
        },
    },
    {
        "name": "get_recent_events",
        "description": "Get recent Kubernetes events filtered by service. Shows warnings, errors, and scheduling events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "namespace": {"type": "string"},
                "service": {"type": "string"},
                "minutes": {"type": "integer", "default": 30},
            },
            "required": ["tenant_id", "namespace", "service"],
        },
    },
    # ── DB MCP Tools ──────────────────────────────────────────────
    {
        "name": "get_connection_count",
        "description": "Get current database connections grouped by state (active, idle, idle_in_transaction). Useful for diagnosing connection pool exhaustion.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "db_name": {"type": "string", "description": "Database name to check"},
            },
            "required": ["tenant_id", "db_name"],
        },
    },
    {
        "name": "get_slow_queries",
        "description": "Get queries currently running longer than the threshold. Useful for diagnosing database latency issues.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "threshold_ms": {"type": "integer", "default": 1000},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["tenant_id"],
        },
    },
    {
        "name": "get_lock_waits",
        "description": "Get queries that are blocked waiting for locks. Critical for diagnosing deadlocks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
            },
            "required": ["tenant_id"],
        },
    },
    {
        "name": "check_table_bloat",
        "description": "Check tables with high dead tuple ratio indicating they need VACUUM. Useful for slow query diagnosis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "schema": {"type": "string", "default": "public"},
            },
            "required": ["tenant_id"],
        },
    },
    # ── Logs MCP Tools ────────────────────────────────────────────
    {
        "name": "search_logs",
        "description": "Search log lines matching a query string for a specific service. Returns timestamped log lines from Loki.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "service": {"type": "string", "description": "Service name to search logs for"},
                "query": {"type": "string", "description": "Search term (e.g. 'timeout', 'connection refused', 'OOMKilled')"},
                "minutes": {"type": "integer", "default": 30},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["tenant_id", "service", "query"],
        },
    },
    {
        "name": "get_error_rate",
        "description": "Get error log count and rate per minute for a service. Useful for determining if errors are increasing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "service": {"type": "string"},
                "minutes": {"type": "integer", "default": 30},
            },
            "required": ["tenant_id", "service"],
        },
    },
    {
        "name": "correlate_by_trace_id",
        "description": "Get all log lines across ALL services sharing a trace_id. Powerful for tracing a request across microservices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "trace_id": {"type": "string"},
            },
            "required": ["tenant_id", "trace_id"],
        },
    },
    # ── Metrics MCP Tools ─────────────────────────────────────────
    {
        "name": "query_prometheus",
        "description": "Execute a raw PromQL query against Prometheus. Returns JSON result. Use for custom metric queries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "promql": {"type": "string", "description": "PromQL query string"},
                "time_range_minutes": {"type": "integer", "default": 30},
            },
            "required": ["tenant_id", "promql"],
        },
    },
    {
        "name": "get_service_latency",
        "description": "Get p50/p95/p99 latency for a service. Shows if latency is spiking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "service": {"type": "string"},
                "percentile": {"type": "integer", "default": 99},
            },
            "required": ["tenant_id", "service"],
        },
    },
    {
        "name": "get_saturation_metrics",
        "description": "Get CPU usage, memory usage, and container restart counts for a service. USE method analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "service": {"type": "string"},
            },
            "required": ["tenant_id", "service"],
        },
    },
    {
        "name": "get_error_rate_history",
        "description": "Get the HTTP error rate percentage for a service over time from Prometheus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "service": {"type": "string"},
                "minutes": {"type": "integer", "default": 60},
            },
            "required": ["tenant_id", "service"],
        },
    },
]

# ─── Tool → MCP Server Routing ───────────────────────────────────────
# Maps each tool name to the MCP server URL that can execute it.

TOOL_ROUTES: dict[str, str] = {
    # K8s MCP (port 8001)
    "get_pod_status":      settings.mcp_k8s_url.replace("/mcp", ""),
    "get_pod_logs":        settings.mcp_k8s_url.replace("/mcp", ""),
    "get_recent_events":   settings.mcp_k8s_url.replace("/mcp", ""),
    # DB MCP (port 8002)
    "get_connection_count": settings.mcp_db_url.replace("/mcp", ""),
    "get_slow_queries":    settings.mcp_db_url.replace("/mcp", ""),
    "get_lock_waits":      settings.mcp_db_url.replace("/mcp", ""),
    "check_table_bloat":   settings.mcp_db_url.replace("/mcp", ""),
    # Logs MCP (port 8003)
    "search_logs":         settings.mcp_logs_url.replace("/mcp", ""),
    "get_error_rate":      settings.mcp_logs_url.replace("/mcp", ""),
    "correlate_by_trace_id": settings.mcp_logs_url.replace("/mcp", ""),
    # Metrics MCP (port 8004)
    "query_prometheus":    settings.mcp_metrics_url.replace("/mcp", ""),
    "get_service_latency": settings.mcp_metrics_url.replace("/mcp", ""),
    "get_saturation_metrics": settings.mcp_metrics_url.replace("/mcp", ""),
    "get_error_rate_history": settings.mcp_metrics_url.replace("/mcp", ""),
}

# Which MCP source name to use for Evidence logging
TOOL_SOURCE: dict[str, str] = {
    "get_pod_status": "k8s-mcp", "get_pod_logs": "k8s-mcp",
    "get_recent_events": "k8s-mcp",
    "get_connection_count": "db-mcp", "get_slow_queries": "db-mcp",
    "get_lock_waits": "db-mcp", "check_table_bloat": "db-mcp",
    "search_logs": "logs-mcp", "get_error_rate": "logs-mcp",
    "correlate_by_trace_id": "logs-mcp",
    "query_prometheus": "metrics-mcp", "get_service_latency": "metrics-mcp",
    "get_saturation_metrics": "metrics-mcp", "get_error_rate_history": "metrics-mcp",
}


async def execute_tool(tool_name: str, tool_input: dict) -> str:
    """
    Execute a tool by directly calling the MCP server function.

    We import the MCP server modules and call the tool functions directly.
    This avoids the complexity of establishing SSE sessions with FastMCP
    and is the most reliable approach for a monorepo architecture.
    """
    if tool_name not in TOOL_SOURCE:
        return f"ERROR: Unknown tool '{tool_name}'"

    try:
        func = _get_tool_function(tool_name)
        result = await func(**tool_input)
        log.info("tool_executed", tool=tool_name, result_length=len(str(result)))
        return str(result)
    except Exception as e:
        error_msg = f"ERROR executing {tool_name}: {str(e)}"
        log.error("tool_execution_failed", tool=tool_name, error=str(e))
        return error_msg


def _get_tool_function(tool_name: str):
    """
    Get the actual Python function for a tool by importing from the MCP server module.
    Uses sys.path manipulation to handle the hyphenated 'mcp-servers' directory name.
    """
    import sys
    import os
    import importlib.util

    source = TOOL_SOURCE[tool_name]
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Map source to file path
    source_to_path = {
        "k8s-mcp": os.path.join(base_dir, "services", "mcp-servers", "k8s", "main.py"),
        "db-mcp": os.path.join(base_dir, "services", "mcp-servers", "db", "main.py"),
        "logs-mcp": os.path.join(base_dir, "services", "mcp-servers", "logs", "main.py"),
        "metrics-mcp": os.path.join(base_dir, "services", "mcp-servers", "metrics", "main.py"),
    }

    file_path = source_to_path.get(source)
    if not file_path or not os.path.exists(file_path):
        raise ImportError(f"MCP server module not found at {file_path}")

    # Use a unique module name to avoid collisions
    module_name = f"_mcp_{source.replace('-', '_')}"

    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

    module = sys.modules[module_name]
    func = getattr(module, tool_name, None)
    if func is None:
        raise AttributeError(f"Tool '{tool_name}' not found in {source}")

    return func

