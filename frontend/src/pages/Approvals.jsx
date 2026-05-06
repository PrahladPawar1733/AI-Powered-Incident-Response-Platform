import { useState, useEffect } from 'react';
import { fetchPendingApprovals, approveAction, rejectAction } from '../api';

export default function Approvals() {
  const [approvals, setApprovals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actionResult, setActionResult] = useState(null);

  const load = async () => {
    try {
      const data = await fetchPendingApprovals();
      setApprovals(data.pending_approvals || []);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);
  useEffect(() => {
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);

  const handleApprove = async (id) => {
    try {
      const res = await approveAction(id);
      setActionResult({ type: 'success', message: `Approved: ${res.message}` });
      load();
    } catch (e) {
      setActionResult({ type: 'error', message: e.message });
    }
  };

  const handleReject = async (id) => {
    try {
      const res = await rejectAction(id);
      setActionResult({ type: 'error', message: `Rejected: ${res.message}` });
      load();
    } catch (e) {
      setActionResult({ type: 'error', message: e.message });
    }
  };

  return (
    <>
      <div className="page-header">
        <h2>Pending Approvals</h2>
        <p>Review and approve/reject remediation actions requiring human authorization</p>
      </div>

      {actionResult && (
        <div className={`toast ${actionResult.type}`} style={{ position: 'relative', right: 'auto', top: 'auto', marginBottom: 16 }}>
          {actionResult.message}
        </div>
      )}

      {loading ? (
        <div className="loading"><div className="spinner" /></div>
      ) : approvals.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">✅</div>
          <h3>No pending approvals</h3>
          <p>All remediation actions have been processed or are auto-executing (LOW risk)</p>
        </div>
      ) : (
        approvals.map((approval) => (
          <div className="approval-card" key={approval.request_id}>
            <div className="approval-header">
              <div>
                <span style={{ fontSize: 14, fontWeight: 600 }}>
                  🔔 Incident: {approval.incident_id?.slice(0, 8)}...
                </span>
                <span className={`badge badge-severity ${approval.risk_level === 'medium' ? 'P2' : 'P1'}`} style={{ marginLeft: 12 }}>
                  {approval.risk_level} risk
                </span>
              </div>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                {approval.remaining_seconds === 'no_expiry' ? 'No timeout' : `${approval.remaining_seconds}s remaining`}
              </span>
            </div>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Request ID: {approval.request_id}
            </p>
            <div className="approval-actions">
              <button className="btn btn-success btn-sm" onClick={() => handleApprove(approval.request_id)}>
                ✅ Approve
              </button>
              <button className="btn btn-danger btn-sm" onClick={() => handleReject(approval.request_id)}>
                ❌ Reject
              </button>
            </div>
          </div>
        ))
      )}
    </>
  );
}
