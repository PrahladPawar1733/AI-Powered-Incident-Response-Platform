import { useState, useEffect } from 'react';
import { fetchCredentials, saveCredential } from '../api';

const CRED_TYPES = [
  {
    key: 'slack',
    label: 'Slack',
    icon: '💬',
    accent: '#4A154B',
    description: 'Incident notifications, approval workflows, and escalation alerts',
    fields: [
      { name: 'bot_token', label: 'Bot User OAuth Token', placeholder: 'xoxb-...', type: 'password',
        help: 'From Slack App → OAuth & Permissions → Bot User OAuth Token' },
      { name: 'incidents_channel', label: 'Incidents Channel', placeholder: '#incidents',
        help: 'Channel for triage, diagnosis, and resolution updates' },
      { name: 'approvals_channel', label: 'Approvals Channel', placeholder: '#incident-approvals',
        help: 'Channel where Approve/Reject buttons are posted' },
      { name: 'escalation_channel', label: 'Escalation Channel (optional)', placeholder: '#p1-escalations',
        help: 'Separate channel for P1 escalations. Falls back to incidents channel.' },
      { name: 'enabled', label: 'Enable Notifications', type: 'toggle' },
    ],
    setupGuide: {
      title: 'How to create a Slack Bot',
      steps: [
        'Go to api.slack.com/apps → Create New App → From Scratch',
        'Under OAuth & Permissions, add Bot Token Scopes: chat:write, channels:read, reactions:read, users:read',
        'Click "Install to Workspace" and authorize',
        'Copy the Bot User OAuth Token (starts with xoxb-)',
        'Invite the bot to your channels: /invite @YourBotName',
      ],
    },
  },
  {
    key: 'prometheus',
    label: 'Prometheus',
    icon: '📊',
    accent: '#E6522C',
    description: 'Metrics collection and alerting',
    fields: [
      { name: 'base_url', label: 'Base URL', placeholder: 'http://localhost:9090' },
      { name: 'auth_type', label: 'Auth Type', type: 'select', options: ['none', 'basic', 'bearer'] },
    ],
  },
  {
    key: 'loki',
    label: 'Grafana Loki',
    icon: '📜',
    accent: '#F2CC0C',
    description: 'Log aggregation and search',
    fields: [
      { name: 'base_url', label: 'Base URL', placeholder: 'http://localhost:3100' },
      { name: 'auth_type', label: 'Auth Type', type: 'select', options: ['none', 'basic', 'bearer'] },
    ],
  },
  {
    key: 'kubernetes',
    label: 'Kubernetes',
    icon: '☸️',
    accent: '#326CE5',
    description: 'Cluster diagnostics and remediation',
    fields: [
      { name: 'api_server_url', label: 'API Server URL', placeholder: 'https://kubernetes.docker.internal:6443' },
      { name: 'auth_type', label: 'Auth Type', type: 'select', options: ['token', 'kubeconfig', 'in_cluster'] },
      { name: 'default_namespace', label: 'Default Namespace', placeholder: 'default' },
    ],
  },
];

export default function Credentials() {
  const [current, setCurrent] = useState(null);
  const [forms, setForms] = useState({ slack: { enabled: true } });
  const [result, setResult] = useState(null);
  const [expandedGuide, setExpandedGuide] = useState(false);
  const [saving, setSaving] = useState(null);
  const [showToken, setShowToken] = useState(false);

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

  // Auto-dismiss toast
  useEffect(() => {
    if (result) {
      const t = setTimeout(() => setResult(null), 4000);
      return () => clearTimeout(t);
    }
  }, [result]);

  const handleSave = async (type) => {
    const formData = forms[type] || {};
    setSaving(type);
    try {
      const res = await saveCredential(type, formData);
      setResult({ type: 'success', message: res.message || `${type} credentials saved` });
      const data = await fetchCredentials();
      setCurrent(data);
    } catch (e) {
      setResult({ type: 'error', message: e.message });
    } finally {
      setSaving(null);
    }
  };

  const updateField = (type, field, value) => {
    setForms((prev) => ({
      ...prev,
      [type]: { ...(prev[type] || {}), [field]: value },
    }));
  };

  const getStatusBadge = (key) => {
    if (!current) return null;
    const cred = current[key];
    if (!cred) return <span className="badge" style={styles.badgeNotConfigured}>Not configured</span>;

    // Slack-specific: check enabled state
    if (key === 'slack') {
      if (cred.enabled === false) {
        return <span className="badge" style={styles.badgePaused}>Paused</span>;
      }
      return <span className="badge" style={styles.badgeConnected}>● Connected</span>;
    }

    return <span className="badge" style={styles.badgeConnected}>● Connected</span>;
  };

  const getStatusSummary = (key) => {
    if (!current) return null;
    const cred = current[key];
    if (!cred) return null;

    switch (key) {
      case 'slack':
        return `${cred.incidents_channel} · ${cred.approvals_channel}`;
      case 'prometheus':
        return cred.base_url;
      case 'loki':
        return cred.base_url;
      case 'kubernetes':
        return cred.api_server_url;
      default:
        return null;
    }
  };

  return (
    <>
      <div className="page-header">
        <h2>Integrations & Credentials</h2>
        <p>Connect your infrastructure services — each tenant has isolated credentials</p>
      </div>

      {result && (
        <div className={`toast ${result.type}`}>{result.message}</div>
      )}

      {/* Status Overview */}
      <div style={styles.statusGrid}>
        {CRED_TYPES.map((cred) => (
          <div key={cred.key} style={{ ...styles.statusCard, borderLeftColor: cred.accent }}>
            <div style={styles.statusCardHeader}>
              <span style={{ fontSize: 20 }}>{cred.icon}</span>
              <div style={{ flex: 1 }}>
                <div style={styles.statusName}>{cred.label}</div>
                <div style={styles.statusSummary}>{getStatusSummary(cred.key) || cred.description}</div>
              </div>
              {getStatusBadge(cred.key)}
            </div>
          </div>
        ))}
      </div>

      {/* Credential Forms */}
      {CRED_TYPES.map((cred) => (
        <div key={cred.key} style={{ ...styles.formCard, borderTopColor: cred.accent }}>
          <div style={styles.formCardHeader}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 22 }}>{cred.icon}</span>
              <div>
                <h3 style={styles.formTitle}>{cred.label}</h3>
                <p style={styles.formDesc}>{cred.description}</p>
              </div>
            </div>
            {getStatusBadge(cred.key)}
          </div>

          {/* Setup Guide (Slack only) */}
          {cred.setupGuide && (
            <div style={styles.guideContainer}>
              <button
                style={styles.guideToggle}
                onClick={() => setExpandedGuide(!expandedGuide)}
              >
                <span>📋 {cred.setupGuide.title}</span>
                <span style={{ transform: expandedGuide ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>▼</span>
              </button>
              {expandedGuide && (
                <ol style={styles.guideSteps}>
                  {cred.setupGuide.steps.map((step, i) => (
                    <li key={i} style={styles.guideStep}>
                      <span style={styles.stepNumber}>{i + 1}</span>
                      {step}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}

          {/* Form fields */}
          <div style={styles.fieldsGrid}>
            {cred.fields.map((field) => (
              <div className="form-group" key={field.name} style={field.type === 'toggle' ? styles.toggleGroup : {}}>
                {field.type === 'toggle' ? (
                  <div style={styles.toggleRow}>
                    <label style={styles.toggleLabel}>{field.label}</label>
                    <button
                      style={{
                        ...styles.toggleBtn,
                        background: forms[cred.key]?.enabled !== false ? '#10b981' : '#374151',
                      }}
                      onClick={() => updateField(cred.key, field.name, forms[cred.key]?.enabled === false)}
                    >
                      <div style={{
                        ...styles.toggleKnob,
                        transform: forms[cred.key]?.enabled !== false ? 'translateX(20px)' : 'translateX(0)',
                      }} />
                    </button>
                    <span style={styles.toggleState}>
                      {forms[cred.key]?.enabled !== false ? 'Enabled' : 'Disabled'}
                    </span>
                  </div>
                ) : field.type === 'select' ? (
                  <>
                    <label>{field.label}</label>
                    <select
                      value={forms[cred.key]?.[field.name] || ''}
                      onChange={(e) => updateField(cred.key, field.name, e.target.value)}
                    >
                      <option value="">Select...</option>
                      {field.options.map((opt) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  </>
                ) : (
                  <>
                    <label>{field.label}</label>
                    <div style={{ position: 'relative' }}>
                      <input
                        type={field.type === 'password' && !showToken ? 'password' : 'text'}
                        value={forms[cred.key]?.[field.name] || ''}
                        onChange={(e) => updateField(cred.key, field.name, e.target.value)}
                        placeholder={field.placeholder}
                      />
                      {field.type === 'password' && (
                        <button
                          type="button"
                          style={styles.eyeBtn}
                          onClick={() => setShowToken(!showToken)}
                          title={showToken ? 'Hide token' : 'Show token'}
                        >
                          {showToken ? '🙈' : '👁️'}
                        </button>
                      )}
                    </div>
                    {field.help && <span style={styles.fieldHelp}>{field.help}</span>}
                  </>
                )}
              </div>
            ))}
          </div>

          <div style={styles.formActions}>
            <button
              className="btn btn-primary"
              onClick={() => handleSave(cred.key)}
              disabled={saving === cred.key}
            >
              {saving === cred.key ? (
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
                  Saving...
                </span>
              ) : (
                `Save ${cred.label}`
              )}
            </button>
            {current?.[cred.key] && (
              <span style={styles.lastSaved}>
                ✓ Currently configured
              </span>
            )}
          </div>
        </div>
      ))}
    </>
  );
}

const styles = {
  statusGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
    gap: 12,
    marginBottom: 32,
  },
  statusCard: {
    background: 'var(--bg-card)',
    border: '1px solid var(--border)',
    borderLeft: '3px solid',
    borderRadius: 'var(--radius-sm)',
    padding: '14px 16px',
    transition: 'all 0.2s ease',
  },
  statusCardHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  statusName: {
    fontSize: 13,
    fontWeight: 600,
    color: 'var(--text-primary)',
  },
  statusSummary: {
    fontSize: 11,
    color: 'var(--text-muted)',
    marginTop: 2,
  },
  badgeConnected: {
    background: 'rgba(16, 185, 129, 0.12)',
    color: '#10b981',
    fontSize: 11,
    padding: '4px 10px',
    borderRadius: 20,
    fontWeight: 600,
  },
  badgePaused: {
    background: 'rgba(245, 158, 11, 0.12)',
    color: '#f59e0b',
    fontSize: 11,
    padding: '4px 10px',
    borderRadius: 20,
    fontWeight: 600,
  },
  badgeNotConfigured: {
    background: 'rgba(100, 116, 139, 0.12)',
    color: '#64748b',
    fontSize: 11,
    padding: '4px 10px',
    borderRadius: 20,
    fontWeight: 600,
  },
  formCard: {
    background: 'var(--bg-card)',
    border: '1px solid var(--border)',
    borderTop: '3px solid',
    borderRadius: 'var(--radius)',
    padding: 24,
    marginBottom: 20,
  },
  formCardHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 20,
  },
  formTitle: {
    fontSize: 16,
    fontWeight: 600,
    marginBottom: 2,
  },
  formDesc: {
    fontSize: 12,
    color: 'var(--text-muted)',
    margin: 0,
  },
  guideContainer: {
    background: 'var(--bg-input)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    marginBottom: 20,
    overflow: 'hidden',
  },
  guideToggle: {
    width: '100%',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 16px',
    background: 'none',
    border: 'none',
    color: 'var(--accent)',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    fontFamily: 'inherit',
  },
  guideSteps: {
    padding: '0 16px 16px 16px',
    margin: 0,
    listStyle: 'none',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  guideStep: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    fontSize: 13,
    color: 'var(--text-secondary)',
    lineHeight: 1.5,
  },
  stepNumber: {
    flexShrink: 0,
    width: 22,
    height: 22,
    borderRadius: '50%',
    background: 'var(--accent-glow)',
    color: 'var(--accent)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 11,
    fontWeight: 700,
  },
  fieldsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
    gap: '0 20px',
  },
  toggleGroup: {
    gridColumn: '1 / -1',
  },
  toggleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '4px 0',
  },
  toggleLabel: {
    fontSize: 13,
    fontWeight: 500,
    color: 'var(--text-secondary)',
    margin: 0,
  },
  toggleBtn: {
    width: 44,
    height: 24,
    borderRadius: 12,
    border: 'none',
    cursor: 'pointer',
    position: 'relative',
    transition: 'background 0.2s ease',
    padding: 0,
    flexShrink: 0,
  },
  toggleKnob: {
    width: 18,
    height: 18,
    borderRadius: '50%',
    background: 'white',
    position: 'absolute',
    top: 3,
    left: 3,
    transition: 'transform 0.2s ease',
    boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
  },
  toggleState: {
    fontSize: 12,
    color: 'var(--text-muted)',
    fontWeight: 500,
  },
  fieldHelp: {
    display: 'block',
    fontSize: 11,
    color: 'var(--text-muted)',
    marginTop: 4,
    lineHeight: 1.4,
  },
  eyeBtn: {
    position: 'absolute',
    right: 10,
    top: '50%',
    transform: 'translateY(-50%)',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    fontSize: 14,
    padding: 4,
  },
  formActions: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    marginTop: 8,
    paddingTop: 16,
    borderTop: '1px solid var(--border)',
  },
  lastSaved: {
    fontSize: 12,
    color: 'var(--status-resolved)',
    fontWeight: 500,
  },
};
