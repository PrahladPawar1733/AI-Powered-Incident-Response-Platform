from datetime import datetime, timezone
from shared.models.alert import AlertEvent, AlertSource, AlertAnnotation

def normalize_prometheus(raw: dict) -> list[AlertEvent]:
    """
    Normalize a Prometheus Alertmanager webhook payload.
    Alertmanager batches alerts, so this returns a list.
    """
    events = []
    
    # Prometheus groups alerts in an array under "alerts"
    for alert in raw.get("alerts", []):
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        
        # Extract fields, falling back safely
        name = labels.get("alertname", "UnknownAlert")
        service = labels.get("service", labels.get("job", "unknown-service"))
        environment = labels.get("env", labels.get("environment", "production"))
        
        # Ensure startsAt has timezone info if missing, though prometheus usually sends it in RFC3339
        fired_at_str = alert.get("startsAt")
        if fired_at_str:
            # Simple handling for 'Z' suffix or basic parsing; Python 3.11+ handles Z correctly in fromisoformat
            try:
                fired_at = datetime.fromisoformat(fired_at_str.replace('Z', '+00:00'))
            except ValueError:
                fired_at = datetime.now(timezone.utc)
        else:
            fired_at = datetime.now(timezone.utc)
            
        events.append(AlertEvent(
            source=AlertSource.PROMETHEUS,
            name=name,
            service=service,
            environment=environment,
            labels=labels,
            annotations=AlertAnnotation(
                summary=annotations.get("summary", ""),
                description=annotations.get("description", ""),
                runbook_url=annotations.get("runbook_url", "")
            ),
            raw_payload=alert,
            fired_at=fired_at
        ))
        
    return events


def normalize_grafana(raw: dict) -> list[AlertEvent]:
    """
    Normalize a Grafana Alerting webhook payload.
    Grafana also batches alerts in an array under "alerts".
    """
    events = []
    
    for alert in raw.get("alerts", []):
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        
        name = labels.get("alertname", "UnknownAlert")
        service = labels.get("service", labels.get("job", "unknown-service"))
        environment = labels.get("env", labels.get("environment", "production"))
        
        events.append(AlertEvent(
            source=AlertSource.GRAFANA,
            name=name,
            service=service,
            environment=environment,
            labels=labels,
            annotations=AlertAnnotation(
                summary=annotations.get("summary", ""),
                description=annotations.get("description", ""),
                runbook_url=annotations.get("runbook_url", alert.get("panelURL", ""))
            ),
            raw_payload=alert,
        ))
        
    return events


def normalize_manual(raw: dict) -> AlertEvent:
    """
    Normalize a manual curl/HTTP post.
    Expects a flat JSON structure.
    """
    return AlertEvent(
        source=AlertSource.MANUAL,
        name=raw.get("name", "ManualAlert"),
        service=raw.get("service", "unknown-service"),
        environment=raw.get("environment", "production"),
        labels=raw.get("labels", {}),
        annotations=AlertAnnotation(
            summary=raw.get("summary", "Manually triggered alert"),
            description=raw.get("description", ""),
            runbook_url=raw.get("runbook_url", "")
        ),
        raw_payload=raw
    )


def normalize_webhook(raw: dict) -> AlertEvent:
    """
    Generic webhook normalization for arbitrary JSON.
    Tries its best to find relevant fields.
    """
    name = raw.get("name") or raw.get("alertname") or raw.get("title") or "WebhookAlert"
    service = raw.get("service") or raw.get("app") or raw.get("component") or "unknown-service"
    
    return AlertEvent(
        source=AlertSource.WEBHOOK,
        name=str(name),
        service=str(service),
        environment=raw.get("environment", "production"),
        annotations=AlertAnnotation(
            summary=raw.get("summary") or raw.get("message") or "Webhook alert received",
            description=raw.get("description", ""),
        ),
        raw_payload=raw
    )
