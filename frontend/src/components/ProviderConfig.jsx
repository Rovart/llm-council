import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api';
import './ProviderConfig.css';

export default function ProviderConfig({ provider }) {
  const [available, setAvailable] = useState([]);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [installing, setInstalling] = useState(false);
  const [installLogs, setInstallLogs] = useState([]);
  const [installErrorOutput, setInstallErrorOutput] = useState('');
  const [installAttempts, setInstallAttempts] = useState([]);
  const [candidateSelection, setCandidateSelection] = useState({});
  const [showConfigModal, setShowConfigModal] = useState(false);

  useEffect(() => {
    if (!provider) return;
    load();
  }, [provider]);

  const load = async () => {
    setLoading(true);
    try {
      const av = await api.listAvailableModels(provider);
      setAvailable(av.models || []);
      const conf = await api.getCouncilConfig();
      // If provider changed, update stored provider but do NOT reset existing council models
      if (conf.provider !== provider) {
        conf.provider = provider;
        try {
          await api.setCouncilConfig(conf);
        } catch (e) {
          console.error('Failed to save provider change', e);
        }
      }
      // For Ollama, filter council models to only available (installed) models
      if (provider === 'ollama') {
        conf.council_models = (conf.council_models || []).filter(m => (av.models || []).includes(m));
        if (conf.chairman_model && !conf.council_models.includes(conf.chairman_model)) {
          conf.chairman_model = conf.council_models.length > 0 ? conf.council_models[0] : '';
        }
      }
      setConfig(conf);
      // Initialize candidate selection using recommended objects
      const rec = conf && conf.recommended_ollama_models ? JSON.parse(JSON.stringify(conf.recommended_ollama_models)) : [];
      const sel = {};
      for (const r of rec) {
        const name = typeof r === 'string' ? r : r.name;
        if (typeof r === 'object' && r.candidates && r.candidates.length > 0) {
          sel[r.family || name] = r.name || r.candidates[0];
        }
      }
      // Do not add selection keys for raw installed model names (avoid duplicate keys)
      // The selection state is tracked per recommended family (by family key).
      setCandidateSelection(sel);
      // Optionally fetch remote registry suggestions for recommended entries
      for (const r of rec) {
        if (typeof r === 'object') {
          if ((!r.candidates || r.candidates.length === 0) && r.family) {
            // Attempt registry search
            try {
              const qres = await api.registrySearch(r.family);
              if (qres && qres.models && qres.models.length > 0) {
                // merge models into candidates, preferring remote results
                const merged = (qres.models || []).concat(r.candidates || []);
                // dedupe and limit to reasonable number
                const uniq = [];
                for (const m of merged) if (!uniq.includes(m)) uniq.push(m);
                r.candidates = uniq.slice(0, 8);
                // init candidate selection for family if not present and set to top candidate
                if (!sel[r.family]) sel[r.family] = r.candidates[0];
              }
            } catch (e) {
              // ignore search fail
            }
          }
        }
      }
      setCandidateSelection(sel);
      // Save back into config and set state so UI re-renders with new candidates
      conf.recommended_ollama_models = rec;
      setConfig(conf);
    } catch (e) {
      console.error('Failed to load provider config', e);
      setMessage('Failed to load provider info');
    } finally {
      setLoading(false);
    }
  };

  const findRecForModel = (model) => {
    const recs = (config?.recommended_ollama_models || []);
    for (const r of recs) {
      const rec = typeof r === 'string' ? { name: r, candidates: [r] } : r;
      if (rec.name === model) return rec;
      if (rec.candidates && rec.candidates.includes(model)) return rec;
      if (rec.family && model.includes(rec.family)) return rec;
    }
    return null;
  };

  const findInstalledInRec = (rec) => {
    if (!rec) return null;
    const list = available || [];
    // rec can be string or object
    const rr = typeof rec === 'string' ? { name: rec, candidates: [rec] } : rec;
    for (const m of list) {
      if (rr.candidates && rr.candidates.includes(m)) return m;
      if (rr.name === m) return m;
      if (rr.family && m.includes(rr.family)) return m;
    }
    return null;
  };

  const isInstalled = (name) => {
    if (!name) return false;
    // exact or substring match to handle tags like ':latest'
    const lc = name.toLowerCase();
    return available.some((a) => a.toLowerCase() === lc || a.toLowerCase().includes(lc) || lc.includes(a.toLowerCase()));
  };
  const isInCouncil = (name) => (config?.council_models || []).includes(name);

  const toggleCouncilModel = (name) => {
    const current = config?.council_models || [];
    let next;
    if (current.includes(name)) {
      next = current.filter((x) => x !== name);
    } else {
      next = [...current, name];
    }

    let updatedConfig = { ...config, council_models: next };

    // If the current chairman was removed, clear or pick a new chairman
    if (config && config.chairman_model && !next.includes(config.chairman_model)) {
      updatedConfig.chairman_model = next.length > 0 ? next[0] : '';
    }

    setConfig(updatedConfig);
    // Auto-save with the NEW config
    saveCouncil(updatedConfig);
  };

  const saveCouncil = async (configToSave = null) => {
    try {
      const conf = { ...(configToSave || config) };
      conf.provider = provider;
      if (!conf.council_models) conf.council_models = [];
      await api.setCouncilConfig(conf);
    } catch (e) {
      console.error(e);
    }
  };

  const selectChairman = async (model) => {
    const updatedConfig = { ...config, chairman_model: model };
    setConfig(updatedConfig);
    saveCouncil(updatedConfig);
  };

  const requestInstall = async (model) => {
    setMessage('');
    setInstallLogs([]);
    setInstallAttempts([]);
    setInstalling(true);
    try {
      // Use a promise wrapper so we can track the final success
      const success = await new Promise(async (resolve) => {
        await api.installOllamaModelStream(model, (type, data) => {
          // New structured event types: install_attempt_start, install_attempt_log, install_attempt_complete, install_complete
          if (type === 'install_attempt_start') {
            setInstallLogs((prev) => [...prev, `== Attempting: ${data.candidate} ==`]);
          } else if (type === 'install_attempt_log') {
            setInstallLogs((prev) => [...prev, `[${data.candidate}] ${data.line}`]);
          } else if (type === 'install_attempt_complete') {
            setInstallLogs((prev) => [...prev, `== Attempt ${data.candidate} complete: success=${data.success} ==`]);
          } else if (type === 'install_complete') {
            setInstallLogs((prev) => [...prev, `== completed: success=${data.success} ==`]);
            setInstallAttempts(data.attempts || []);
            // Save any structured attempts to error output if failed
            if (!data.success && data.attempts) {
              try {
                const summary = data.attempts.map(a => `${a.name}: ${a.success ? 'OK' : 'FAIL'}\n${a.output || ''}`).join('\n---\n');
                setInstallErrorOutput(summary);
              } catch (e) {
                setInstallErrorOutput(data.output || 'Install failed');
              }
            }
            // Resolve based on success flag
            resolve(Boolean(data.success));
          } else if (type === 'error') {
            setInstallLogs((prev) => [...prev, `ERROR: ${data.message}`]);
            setInstallErrorOutput(data.message || 'Error');
            resolve(false);
          }
        });
      });
      setInstalling(false);
      if (!success) {
        setInstallErrorOutput((prev) => prev || 'Install failed');
      } else {
        setInstallErrorOutput('');
        setInstallAttempts([]);
        setTimeout(() => load(), 800);
      }
    } catch (e) {
      console.error(e);
      setInstallLogs((prev) => [...prev, `Failed to start install: ${String(e)}`]);
      setInstalling(false);
    }
  };

  // tryAllCandidates removed

  const refreshModels = async () => {
    setLoading(true);
    try {
      const av = await api.listAvailableModels(provider);
      setAvailable(av.models || []);
    } catch (e) {
      console.error(e);
      setMessage('Failed to refresh models');
    } finally {
      setLoading(false);
    }
  };

  const recommended = (config && config.recommended_ollama_models) || [];

  if (!provider || provider !== 'ollama') return null;

  return (
    <div className="provider-config">
      <h3>
        Ollama Models
        <span className="header-actions">
          <button className="refresh-emoji" title="Refresh models" onClick={refreshModels} disabled={loading}>🔄</button>
          <button className="gear-btn" title="Configure models" onClick={() => setShowConfigModal(true)}>⚙️</button>
        </span>
      </h3>
      {loading && <div>Loading...</div>}
      {message && <div className="message">{message}</div>}

      <div className="installed">
        <h4>Installed Models</h4>
        {available.length === 0 && <div>No models detected.</div>}
        <ul className="installed-list">
          {available.map((m) => (
            <li key={m} style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'space-between' }}>
              <div className={`pill-toggle ${isInCouncil(m) ? 'active' : ''}`} onClick={() => toggleCouncilModel(m)} style={{ cursor: 'pointer' }}>
                <span className="pill-name">{m}</span>
              </div>
            </li>
          ))}
        </ul>
      </div>

      {/* Recommended models and install logs are intentionally hidden from main sidebar. Use gear button to open modal and manage installations. */}

      <div className="chairman">
        <h4>Chairman</h4>
        <select value={config?.chairman_model || ''} onChange={(e) => selectChairman(e.target.value)}>
          <option value="">-- choose chairman --</option>
          {(config?.council_models || []).map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </div>

      {/* Autosave is enabled; explicit Save button removed */}

      {showConfigModal ? createPortal(
        <div className="modal-backdrop" onClick={() => setShowConfigModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Ollama Model Manager</h3>
              <button className="modal-close" onClick={() => setShowConfigModal(false)}>✖</button>
            </div>
            <div className="modal-body">
              {/* Full installed UI in modal */}
              <div className="installed">
                <h4>Installed Models</h4>
                {available.length === 0 && <div>No models detected.</div>}
                <ul className="installed-list">
                  {available.map((m) => {
                    const inCouncil = isInCouncil(m);
                    return (
                      <li key={m}>
                        <div className={`pill-toggle ${inCouncil ? 'active' : ''}`} onClick={() => toggleCouncilModel(m)} style={{ cursor: 'pointer' }} title={inCouncil ? 'Remove from council' : 'Add to council'}>
                          <span className="pill-name">{m}</span>
                        </div>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                          {/* Version select for installed model */}
                          {/* For installed models in the sidebar we only show Uninstall in the modal installed list - no select */}
                          <div className="model-actions"><button className="uninstall-btn" onClick={() => api.uninstallOllamaModelStream(m, (t, d) => { if (t === 'uninstall_complete') { if (d.success) { setTimeout(() => load(), 800) } } })} disabled={installing}>Uninstall</button></div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>

              {/* Recommended + installation UI */}
              <div className="recommended">
                <h4>Recommended models</h4>
                {recommended.length === 0 && <div>No recommendations.</div>}
                <ul>
                  {recommended.map((r) => {
                    const rec = typeof r === 'string' ? { name: r, candidates: [r] } : r;
                    const familyKey = rec.family || rec.name;
                    const selected = candidateSelection[familyKey] || rec.name;
                    const installedVersion = findInstalledInRec(rec);
                    const installedFlag = Boolean(installedVersion);
                    return (
                      <li key={familyKey}>
                        <span className={`model-chip ${installedFlag ? 'in-council' : ''}`}>
                          <span className="name">{rec.family || rec.name}</span>
                          <span className="meta">{installedFlag ? 'installed' : (rec.name === selected ? 'suggested' : '')}</span>
                        </span>
                        {installedFlag ? (<span className="check-badge">✓</span>) : null}
                        {rec.candidates && rec.candidates.length > 0 ? (
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <select value={selected} onChange={(e) => setCandidateSelection((prev) => ({ ...prev, [familyKey]: e.target.value }))}>
                              {rec.candidates.filter(c => c !== rec.family).map((c) => {
                                const installedHere = available.some((a) => a.toLowerCase() === c.toLowerCase() || a.toLowerCase().includes(c.toLowerCase()));
                                return (
                                  <option key={c} value={c}>
                                    {c} {c === rec.name ? '(suggested)' : ''} {installedHere ? '(installed)' : ''}
                                  </option>
                                );
                              })}
                            </select>
                            <div className="model-actions">
                              <button onClick={() => requestInstall(selected)} disabled={installing || isInstalled(selected)}>Install</button>
                            </div>
                          </div>
                        ) : (
                          !isInstalled(rec.name) ? (
                            <div className="model-actions"><button onClick={() => requestInstall(rec.name)} disabled={installing}>Install</button></div>
                          ) : null
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>

              {installing || installLogs.length > 0 ? (
                <div className="install-logs">
                  <h4>Install logs</h4>
                  <div style={{ maxHeight: 160, overflow: 'auto', background: '#0f1720', color: '#e6f0ff', padding: 8, borderRadius: 6, fontSize: 12 }}>
                    <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                      {installLogs.length === 0 ? (installing ? 'Starting install...' : '') : installLogs.join('\n')}
                    </pre>
                  </div>
                  {installErrorOutput ? (
                    <div className="install-error">
                      <div className="install-error-header">Install failed</div>
                      <pre className="install-error-body">{installErrorOutput}</pre>
                      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                        <button onClick={() => { navigator.clipboard && navigator.clipboard.writeText(installErrorOutput) }}>Copy error</button>
                        <button onClick={() => { setInstallLogs([]); setInstallErrorOutput(''); setInstallAttempts([]); }}>Dismiss</button>
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {installAttempts.length > 0 ? (
                <div className="install-attempts">
                  <h4>Install Attempts</h4>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: '#f0f0f0' }}>
                        <th style={{ border: '1px solid #ccc', padding: 4 }}>Candidate</th>
                        <th style={{ border: '1px solid #ccc', padding: 4 }}>Success</th>
                        <th style={{ border: '1px solid #ccc', padding: 4 }}>Return Code</th>
                        <th style={{ border: '1px solid #ccc', padding: 4 }}>Output</th>
                      </tr>
                    </thead>
                    <tbody>
                      {installAttempts.map((attempt, idx) => (
                        <tr key={idx} style={{ background: attempt.success ? '#e8f5e8' : '#ffebee' }}>
                          <td style={{ border: '1px solid #ccc', padding: 4 }}>{attempt.name}</td>
                          <td style={{ border: '1px solid #ccc', padding: 4 }}>{attempt.success ? 'Yes' : 'No'}</td>
                          <td style={{ border: '1px solid #ccc', padding: 4 }}>{attempt.returncode !== null ? attempt.returncode : 'N/A'}</td>
                          <td style={{ border: '1px solid #ccc', padding: 4, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }} title={attempt.output}>
                            {attempt.output ? attempt.output.substring(0, 100) + (attempt.output.length > 100 ? '...' : '') : 'N/A'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          </div>
        </div>,
        document.body
      ) : null}
    </div>
  );
}
