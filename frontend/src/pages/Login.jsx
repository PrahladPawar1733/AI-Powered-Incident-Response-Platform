import { useState } from 'react';
import { login, register } from '../api';

export default function Login({ onLogin }) {
  const [mode, setMode] = useState('login'); // 'login' or 'register'
  const [form, setForm] = useState({ tenant_id: '', password: '', org_name: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      let data;
      if (mode === 'login') {
        data = await login(form.tenant_id, form.password);
      } else {
        data = await register(form.tenant_id, form.org_name, form.password);
      }
      onLogin(data);
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg-primary)',
    }}>
      <div style={{
        width: 420,
        background: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        padding: 40,
        boxShadow: '0 25px 50px -12px rgba(0,0,0,0.5)',
      }}>
        {/* Logo */}
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{
            width: 56, height: 56, margin: '0 auto 16px',
            background: 'linear-gradient(135deg, var(--accent), #a855f7)',
            borderRadius: 16,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 28,
          }}>⚡</div>
          <h1 style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.03em' }}>
            Incident Response Platform
          </h1>
          <p style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 4 }}>
            AI-Powered Autonomous Incident Management
          </p>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 0, marginBottom: 24, borderRadius: 'var(--radius-sm)', overflow: 'hidden', border: '1px solid var(--border)' }}>
          <button
            onClick={() => setMode('login')}
            style={{
              flex: 1, padding: '10px', border: 'none', cursor: 'pointer',
              fontSize: 13, fontWeight: 600, fontFamily: 'inherit',
              background: mode === 'login' ? 'var(--accent)' : 'transparent',
              color: mode === 'login' ? 'white' : 'var(--text-muted)',
              transition: 'all 0.15s ease',
            }}
          >Sign In</button>
          <button
            onClick={() => setMode('register')}
            style={{
              flex: 1, padding: '10px', border: 'none', cursor: 'pointer',
              fontSize: 13, fontWeight: 600, fontFamily: 'inherit',
              background: mode === 'register' ? 'var(--accent)' : 'transparent',
              color: mode === 'register' ? 'white' : 'var(--text-muted)',
              transition: 'all 0.15s ease',
            }}
          >Register Tenant</button>
        </div>

        {/* Error */}
        {error && (
          <div style={{
            background: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderRadius: 'var(--radius-sm)',
            padding: '10px 14px',
            marginBottom: 16,
            color: 'var(--status-escalated)',
            fontSize: 13,
          }}>{error}</div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Tenant ID</label>
            <input
              value={form.tenant_id}
              onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}
              placeholder="e.g. acme_corp"
              required
              autoFocus
            />
          </div>

          {mode === 'register' && (
            <div className="form-group">
              <label>Organization Name</label>
              <input
                value={form.org_name}
                onChange={(e) => setForm({ ...form, org_name: e.target.value })}
                placeholder="e.g. Acme Corporation"
                required
              />
            </div>
          )}

          <div className="form-group">
            <label>Password</label>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              placeholder="••••••••"
              required
            />
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            disabled={loading}
            style={{ width: '100%', marginTop: 8 }}
          >
            {loading ? 'Please wait...' : mode === 'login' ? 'Sign In' : 'Create Tenant Account'}
          </button>
        </form>

        {/* Hint */}
        <p style={{ textAlign: 'center', marginTop: 20, fontSize: 12, color: 'var(--text-muted)' }}>
          Default tenant: <code style={{ color: 'var(--accent)' }}>default</code> / <code style={{ color: 'var(--accent)' }}>admin</code>
        </p>
      </div>
    </div>
  );
}
