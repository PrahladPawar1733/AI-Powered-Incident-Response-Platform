import { useState } from 'react';
import { fireAlert } from '../api';

const PRESETS = [
  {
    label: '🔴 P1 — Database Down',
    data: { name: 'DatabaseConnectionError', service: 'payment-api', environment: 'production', summary: 'Connection pool exhausted — all 100 connections in use, new requests failing with timeout' },
  },
  {
    label: '🟠 P2 — High Latency',
    data: { name: 'HighLatencyAlert', service: 'checkout-service', environment: 'production', summary: 'P99 latency above 5 seconds for the last 10 minutes' },
  },
  {
    label: '🟡 P2 — Pod CrashLoop',
    data: { name: 'PodCrashLooping', service: 'order-service', environment: 'staging', summary: 'Pod has restarted 15 times in the last 30 minutes with OOMKilled exit code' },
  },
  {
    label: '🔵 P3 — Disk Usage',
    data: { name: 'DiskUsageHigh', service: 'log-aggregator', environment: 'production', summary: 'Disk usage above 85% on /var/log volume' },
  },
];

export default function FireAlert() {
  const [form, setForm] = useState({ name: '', service: '', environment: 'production', summary: '' });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setResult(null);
    try {
      const res = await fireAlert(form);
      setResult({ type: 'success', message: res.message || 'Alert fired!', data: res });
    } catch (err) {
      setResult({ type: 'error', message: err.message });
    }
    setLoading(false);
  };

  const usePreset = (preset) => {
    setForm(preset.data);
    setResult(null);
  };

  return (
    <>
      <div className="page-header">
        <h2>Fire Alert</h2>
        <p>Manually trigger an alert to test the full pipeline</p>
      </div>

      {/* Presets */}
      <div style={{ marginBottom: 24 }}>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 12 }}>Quick presets:</p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {PRESETS.map((p, i) => (
            <button key={i} className="btn btn-sm" onClick={() => usePreset(p)}
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Form */}
      <form className="form-card" onSubmit={handleSubmit}>
        <div className="form-group">
          <label>Alert Name *</label>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required
            placeholder="e.g. HighLatencyAlert" />
        </div>
        <div className="form-group">
          <label>Service *</label>
          <input value={form.service} onChange={(e) => setForm({ ...form, service: e.target.value })} required
            placeholder="e.g. payment-api" />
        </div>
        <div className="form-group">
          <label>Environment</label>
          <select value={form.environment} onChange={(e) => setForm({ ...form, environment: e.target.value })}>
            <option value="production">production</option>
            <option value="staging">staging</option>
            <option value="development">development</option>
          </select>
        </div>
        <div className="form-group">
          <label>Summary</label>
          <textarea value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })}
            placeholder="Describe what's happening..." />
        </div>

        <button type="submit" className="btn btn-primary" disabled={loading || !form.name || !form.service}>
          {loading ? 'Firing...' : '🔥 Fire Alert'}
        </button>
      </form>

      {/* Result */}
      {result && (
        <div className={`toast ${result.type}`} style={{ position: 'relative', marginTop: 16, right: 'auto', top: 'auto' }}>
          {result.message}
          {result.data?.alert_id && <span style={{ marginLeft: 8, opacity: 0.8 }}>ID: {result.data.alert_id.slice(0, 8)}</span>}
        </div>
      )}
    </>
  );
}
