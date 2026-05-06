import { useState } from 'react';
import './index.css';
import { isLoggedIn, getCurrentUser, logout } from './api';
import Login from './pages/Login';
import IncidentList from './pages/IncidentList';
import IncidentDetail from './pages/IncidentDetail';
import FireAlert from './pages/FireAlert';
import Approvals from './pages/Approvals';
import Credentials from './pages/Credentials';

const PAGES = {
  incidents: { label: 'Incidents', icon: '🚨' },
  fire: { label: 'Fire Alert', icon: '🔥' },
  approvals: { label: 'Approvals', icon: '✅' },
  credentials: { label: 'Credentials', icon: '🔑' },
};

export default function App() {
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());
  const [page, setPage] = useState('incidents');
  const [selectedIncident, setSelectedIncident] = useState(null);

  const user = getCurrentUser();

  const handleLogin = () => {
    setLoggedIn(true);
  };

  const handleLogout = () => {
    logout();
    setLoggedIn(false);
  };

  const handleSelectIncident = (id) => {
    setSelectedIncident(id);
    setPage('detail');
  };

  const handleBack = () => {
    setSelectedIncident(null);
    setPage('incidents');
  };

  // Show login page if not authenticated
  if (!loggedIn) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <div className="logo-icon">⚡</div>
          <div>
            <h1>IRP</h1>
            <span>Incident Response Platform</span>
          </div>
        </div>

        <nav className="sidebar-nav">
          {Object.entries(PAGES).map(([key, { label, icon }]) => (
            <button
              key={key}
              className={`nav-item ${page === key ? 'active' : ''}`}
              onClick={() => { setPage(key); setSelectedIncident(null); }}
            >
              <span>{icon}</span>
              {label}
            </button>
          ))}
        </nav>

        {/* User info at bottom */}
        <div style={{ marginTop: 'auto', borderTop: '1px solid var(--border)', paddingTop: 16 }}>
          <div style={{ padding: '0 12px', marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
              {user.org_name || user.tenant_id}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
              Tenant: {user.tenant_id}
            </div>
          </div>
          <button
            className="nav-item"
            onClick={handleLogout}
            style={{ color: 'var(--status-escalated)' }}
          >
            <span>🚪</span>
            Sign Out
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="main-content">
        {page === 'incidents' && (
          <IncidentList onSelect={handleSelectIncident} />
        )}
        {page === 'detail' && selectedIncident && (
          <IncidentDetail id={selectedIncident} onBack={handleBack} />
        )}
        {page === 'fire' && <FireAlert />}
        {page === 'approvals' && <Approvals />}
        {page === 'credentials' && <Credentials />}
      </main>
    </div>
  );
}
