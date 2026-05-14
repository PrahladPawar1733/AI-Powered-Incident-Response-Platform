import { useState, useEffect, useRef } from 'react';
import { fetchIncidents, fetchStats } from '../api';

const STATUS_FILTERS = [null, 'triaging', 'diagnosing', 'remediating', 'resolved', 'escalated'];

export default function IncidentList({ onSelect }) {
  const [incidents, setIncidents] = useState([]);
  const [stats, setStats] = useState(null);
  const [filter, setFilter] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const isFirstLoad = useRef(true);

  const load = async (showSpinner = false) => {
    if (showSpinner || isFirstLoad.current) setLoading(true);
    try {
      const [incData, statData] = await Promise.all([
        fetchIncidents(filter),
        fetchStats(),
      ]);
      setIncidents(incData.incidents || []);
      setStats(statData);
      setError(null);
      isFirstLoad.current = false;
    } catch (e) {
      console.error(e);
      setError(e.message || 'Failed to load incidents');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    isFirstLoad.current = true;
    load(true);
  }, [filter]);

  // Auto-refresh every 5s — no spinner, silent background refresh
  useEffect(() => {
    const timer = setInterval(() => load(false), 5000);
    return () => clearInterval(timer);
  }, [filter]);

  const timeAgo = (dateStr) => {
    if (!dateStr) return '—';
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };

  return (
    <>
      <div className="page-header">
        <h2>Incidents</h2>
        <p>Real-time view of all incidents flowing through the pipeline</p>
      </div>

      {/* Stats */}
      {stats && (
        <div className="stats-grid">
          <div className="stat-card">
            <div className="stat-label">Total Incidents</div>
            <div className="stat-value">{stats.total_incidents || 0}</div>
            <div className="stat-sub">{stats.last_24h || 0} in last 24h</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Resolved</div>
            <div className="stat-value" style={{ color: 'var(--status-resolved)' }}>
              {stats.status_breakdown?.resolved || 0}
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Escalated</div>
            <div className="stat-value" style={{ color: 'var(--status-escalated)' }}>
              {stats.status_breakdown?.escalated || 0}
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Avg MTTR</div>
            <div className="stat-value">
              {stats.mttr?.avg_seconds ? `${Math.round(stats.mttr.avg_seconds)}s` : '—'}
            </div>
            <div className="stat-sub">Mean Time To Resolve</div>
          </div>
        </div>
      )}

      {/* Incident Table */}
      <div className="incidents-table">
        <div className="table-header">
          <h3>All Incidents</h3>
          <div className="filter-group">
            {STATUS_FILTERS.map((s) => (
              <button
                key={s || 'all'}
                className={`filter-btn ${filter === s ? 'active' : ''}`}
                onClick={() => setFilter(s)}
              >
                {s || 'All'}
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="loading"><div className="spinner" /></div>
        ) : error ? (
          <div className="empty-state">
            <div className="empty-icon">⚠️</div>
            <h3>Could not load incidents</h3>
            <p style={{ color: 'var(--status-escalated)' }}>{error}</p>
            <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={() => load(true)}>
              Retry
            </button>
          </div>
        ) : incidents.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">📋</div>
            <h3>No incidents found</h3>
            <p>Fire an alert to see it flow through the pipeline</p>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Severity</th>
                <th>Alert</th>
                <th>Service</th>
                <th>Root Cause</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((inc) => (
                <tr key={inc.incident_id} onClick={() => onSelect(inc.incident_id)}>
                  <td>
                    <span className={`badge badge-status ${inc.status}`}>
                      <span className="status-dot" />
                      {inc.status}
                    </span>
                  </td>
                  <td>
                    <span className={`badge badge-severity ${inc.severity}`}>
                      {inc.severity || '—'}
                    </span>
                  </td>
                  <td style={{ fontWeight: 500 }}>{inc.alert_name}</td>
                  <td style={{ color: 'var(--text-secondary)' }}>{inc.service}</td>
                  <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
                    {inc.root_cause || '—'}
                  </td>
                  <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>{timeAgo(inc.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
