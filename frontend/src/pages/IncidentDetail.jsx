import { useState, useEffect } from 'react';
import { fetchIncidentDetail } from '../api';

export default function IncidentDetail({ id, onBack }) {
  const [incident, setIncident] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const data = await fetchIncidentDetail(id);
        setIncident(data);
      } catch (e) {
        console.error(e);
      }
      setLoading(false);
    })();
  }, [id]);

  if (loading) return <div className="loading"><div className="spinner" /></div>;
  if (!incident) return <div className="empty-state"><h3>Incident not found</h3></div>;

  const alert = incident.alert || {};
  const annotations = alert.annotations || {};

  return (
    <>
      <button className="back-link" onClick={onBack}>← Back to Incidents</button>

      {/* Header */}
      <div className="page-header">
        <h2>{alert.name || 'Unknown Alert'}</h2>
        <p style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 8 }}>
          <span className={`badge badge-status ${incident.status}`}>
            <span className="status-dot" />
            {incident.status}
          </span>
          <span className={`badge badge-severity ${incident.severity}`}>
            {incident.severity}
          </span>
          <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
            ID: {incident.incident_id?.slice(0, 8)}...
          </span>
        </p>
      </div>

      {/* Overview */}
      <div className="detail-panel">
        <h3>📋 Overview</h3>
        <div className="detail-grid">
          <div className="detail-item">
            <label>Service</label>
            <span>{alert.service}</span>
          </div>
          <div className="detail-item">
            <label>Environment</label>
            <span>{alert.environment || 'default'}</span>
          </div>
          <div className="detail-item">
            <label>Summary</label>
            <span>{annotations.summary || '—'}</span>
          </div>
          <div className="detail-item">
            <label>Description</label>
            <span>{annotations.description || '—'}</span>
          </div>
        </div>
      </div>

      {/* Triage */}
      <div className="detail-panel">
        <h3>🔍 Triage</h3>
        <div className="detail-grid">
          <div className="detail-item">
            <label>Triage Summary</label>
            <p>{incident.triage_summary || '—'}</p>
          </div>
          <div className="detail-item">
            <label>Confidence</label>
            <span>{incident.triage_confidence ? `${(incident.triage_confidence * 100).toFixed(0)}%` : '—'}</span>
          </div>
          <div className="detail-item">
            <label>Matched Runbook</label>
            <span>{incident.matched_runbook_id || 'None'}</span>
          </div>
          <div className="detail-item">
            <label>Similar Past Incidents</label>
            <span>{incident.similar_incident_ids?.length || 0} found</span>
          </div>
        </div>
      </div>

      {/* Diagnosis */}
      <div className="detail-panel">
        <h3>🔬 Diagnosis</h3>
        <div className="detail-grid">
          <div className="detail-item" style={{ gridColumn: '1 / -1' }}>
            <label>Root Cause</label>
            <p style={{ color: 'var(--status-escalated)', fontWeight: 500 }}>
              {incident.root_cause || '—'}
            </p>
          </div>
          <div className="detail-item">
            <label>Affected Services</label>
            <span>
              {incident.affected_services?.length
                ? incident.affected_services.join(', ')
                : '—'}
            </span>
          </div>
          <div className="detail-item">
            <label>Diagnosis Summary</label>
            <p>{incident.diagnosis_summary || '—'}</p>
          </div>
        </div>
      </div>

      {/* Evidence */}
      {incident.evidence?.length > 0 && (
        <div className="detail-panel">
          <h3>🧾 Evidence ({incident.evidence.length} items)</h3>
          <div className="evidence-list">
            {incident.evidence.map((ev, i) => (
              <div className="evidence-card" key={i}>
                <div className="ev-header">
                  <span className="ev-source">{ev.source}</span>
                  <span className="ev-tool">{ev.tool_name}</span>
                </div>
                <pre>{ev.content}</pre>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Remediation */}
      <div className="detail-panel">
        <h3>🛠️ Remediation</h3>
        {incident.remediation_plan?.length > 0 ? (
          <div className="evidence-list">
            {incident.remediation_plan.map((action, i) => (
              <div className="evidence-card" key={i}>
                <div className="ev-header">
                  <span className="ev-source">{action.tool_fn}</span>
                  <span className={`badge badge-severity ${action.risk_level === 'low' ? 'P4' : action.risk_level === 'medium' ? 'P2' : 'P1'}`}>
                    {action.risk_level} risk
                  </span>
                </div>
                <p style={{ fontSize: 13, marginBottom: 8, color: 'var(--text-secondary)' }}>
                  {action.reasoning}
                </p>
                <pre>{JSON.stringify(action.parameters, null, 2)}</pre>
                {action.result && (
                  <p style={{ marginTop: 8, fontSize: 12, color: action.result.startsWith('ERROR') ? 'var(--status-escalated)' : 'var(--status-resolved)' }}>
                    Result: {action.result}
                  </p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p style={{ color: 'var(--text-muted)' }}>No remediation plan generated</p>
        )}
        {incident.resolution_summary && (
          <div className="detail-item" style={{ marginTop: 16 }}>
            <label>Resolution Summary</label>
            <p>{incident.resolution_summary}</p>
          </div>
        )}
      </div>

      {/* Timeline */}
      <div className="detail-panel">
        <h3>⏱ Timeline</h3>
        <div className="detail-grid">
          <div className="detail-item">
            <label>Created</label>
            <span>{incident.created_at ? new Date(incident.created_at).toLocaleString() : '—'}</span>
          </div>
          <div className="detail-item">
            <label>Triaged</label>
            <span>{incident.triaged_at ? new Date(incident.triaged_at).toLocaleString() : '—'}</span>
          </div>
          <div className="detail-item">
            <label>Diagnosed</label>
            <span>{incident.diagnosed_at ? new Date(incident.diagnosed_at).toLocaleString() : '—'}</span>
          </div>
          <div className="detail-item">
            <label>Resolved</label>
            <span>{incident.resolved_at ? new Date(incident.resolved_at).toLocaleString() : '—'}</span>
          </div>
        </div>
      </div>
    </>
  );
}
