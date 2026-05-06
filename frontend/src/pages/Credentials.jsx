import { useState, useEffect } from 'react';
import { fetchCredentials, saveCredential } from '../api';

const CRED_TYPES = [
  {
    key: 'prometheus',
    label: 'Prometheus',
    icon: '📊',
    fields: [
      { name: 'base_url', label: 'Base URL', placeholder: 'http://localhost:9090' },
      { name: 'auth_type', label: 'Auth Type', type: 'select', options: ['none', 'basic', 'bearer'] },
    ],
  },
  {
    key: 'loki',
    label: 'Grafana Loki',
    icon: '📜',
    fields: [
      { name: 'base_url', label: 'Base URL', placeholder: 'http://localhost:3100' },
      { name: 'auth_type', label: 'Auth Type', type: 'select', options: ['none', 'basic', 'bearer'] },
    ],
  },
  {
    key: 'kubernetes',
    label: 'Kubernetes',
    icon: '☸️',
    fields: [
      { name: 'api_server_url', label: 'API Server URL', placeholder: 'https://kubernetes.docker.internal:6443' },
      { name: 'auth_type', label: 'Auth Type', type: 'select', options: ['token', 'kubeconfig', 'in_cluster'] },
      { name: 'default_namespace', label: 'Default Namespace', placeholder: 'default' },
    ],
  },
];

export default function Credentials() {
  const [current, setCurrent] = useState(null);
  const [forms, setForms] = useState({});
  const [result, setResult] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await fetchCredentials();
        setCurrent(data);
      } catch (e) {
        console.error(e);
      }
    })();
  }, []);

  const handleSave = async (type) => {
    const formData = forms[type] || {};
    try {
      const res = await saveCredential(type, formData);
      setResult({ type: 'success', message: res.message || `${type} credentials saved` });
      const data = await fetchCredentials();
      setCurrent(data);
    } catch (e) {
      setResult({ type: 'error', message: e.message });
    }
  };

  const updateField = (type, field, value) => {
    setForms((prev) => ({
      ...prev,
      [type]: { ...(prev[type] || {}), [field]: value },
    }));
  };

  return (
    <>
      <div className="page-header">
        <h2>Credentials</h2>
        <p>Register your infrastructure endpoints for the MCP diagnostic tools</p>
      </div>

      {result && (
        <div className={`toast ${result.type}`} style={{ position: 'relative', right: 'auto', top: 'auto', marginBottom: 16 }}>
          {result.message}
        </div>
      )}

      {/* Current Credentials */}
      {current && (
        <div className="detail-panel" style={{ marginBottom: 24 }}>
          <h3>📡 Currently Registered</h3>
          <div className="detail-grid">
            <div className="detail-item">
              <label>Prometheus</label>
              <span>{current.prometheus?.base_url || 'Not configured'}</span>
            </div>
            <div className="detail-item">
              <label>Loki</label>
              <span>{current.loki?.base_url || 'Not configured'}</span>
            </div>
            <div className="detail-item">
              <label>Kubernetes</label>
              <span>{current.kubernetes?.api_server_url || 'Not configured'}</span>
            </div>
            <div className="detail-item">
              <label>Tenant</label>
              <span>{current.tenant_id || 'default'}</span>
            </div>
          </div>
        </div>
      )}

      {/* Register Forms */}
      {CRED_TYPES.map((cred) => (
        <div className="form-card" key={cred.key} style={{ marginBottom: 16, maxWidth: '100%' }}>
          <h3 style={{ marginBottom: 16, fontSize: 15 }}>{cred.icon} {cred.label}</h3>
          {cred.fields.map((field) => (
            <div className="form-group" key={field.name}>
              <label>{field.label}</label>
              {field.type === 'select' ? (
                <select
                  value={forms[cred.key]?.[field.name] || ''}
                  onChange={(e) => updateField(cred.key, field.name, e.target.value)}
                >
                  <option value="">Select...</option>
                  {field.options.map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              ) : (
                <input
                  value={forms[cred.key]?.[field.name] || ''}
                  onChange={(e) => updateField(cred.key, field.name, e.target.value)}
                  placeholder={field.placeholder}
                />
              )}
            </div>
          ))}
          <button className="btn btn-primary btn-sm" onClick={() => handleSave(cred.key)}>
            Save {cred.label}
          </button>
        </div>
      ))}
    </>
  );
}
