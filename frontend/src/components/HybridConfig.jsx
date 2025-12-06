import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api';
import './ProviderConfig.css';

export default function HybridConfig() {
  const [councilConfig, setCouncilConfig] = useState(null);
  const [ollamaModels, setOllamaModels] = useState([]);
  const [openrouterModels, setOpenrouterModels] = useState([]);
  const [customModels, setCustomModels] = useState([]);
  const [openrouterConfig, setOpenrouterConfig] = useState(null);
  const [customApiConfig, setCustomApiConfig] = useState(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [showConfigModal, setShowConfigModal] = useState(false);
  
  // OpenRouter form state
  const [orApiKeyInput, setOrApiKeyInput] = useState('');
  const [orValidating, setOrValidating] = useState(false);
  const [orValidationResult, setOrValidationResult] = useState(null);
  
  // Custom API form state
  const [customUrlInput, setCustomUrlInput] = useState('');
  const [customKeyInput, setCustomKeyInput] = useState('');
  const [customValidating, setCustomValidating] = useState(false);
  const [customValidationResult, setCustomValidationResult] = useState(null);
  
  // Ollama install state (for Local tab)
  const [installing, setInstalling] = useState(false);
  const [installLogs, setInstallLogs] = useState([]);
  const [installErrorOutput, setInstallErrorOutput] = useState('');
  const [installAttempts, setInstallAttempts] = useState([]);
  const [candidateSelection, setCandidateSelection] = useState({});
  
  const [modelSearch, setModelSearch] = useState('');
  const [activeTab, setActiveTab] = useState('all');

  useEffect(() => {
    load();
  }, []);

  const load = async () => {
    setLoading(true);
    try {
      const [ccConfig, avModels, orConfig, caConfig] = await Promise.all([
        api.getCouncilConfig(),
        api.listAvailableModels('hybrid'),
        api.getOpenRouterConfig().catch(() => null),
        api.getCustomApiConfig().catch(() => null),
      ]);
      setCouncilConfig(ccConfig);
      setOllamaModels(avModels.ollama_models || []);
      setOpenrouterModels(avModels.openrouter_models || []);
      setCustomModels(avModels.custom_models || []);
      if (orConfig) {
        setOpenrouterConfig(orConfig);
      }
      if (caConfig) {
        setCustomApiConfig(caConfig);
        setCustomUrlInput(caConfig.api_url || '');
      }
      
      // Initialize candidate selection for recommended models
      const rec = ccConfig?.recommended_ollama_models || [];
      const sel = {};
      for (const r of rec) {
        const name = typeof r === 'string' ? r : r.name;
        if (typeof r === 'object' && r.candidates && r.candidates.length > 0) {
          sel[r.family || name] = r.name || r.candidates[0];
        }
      }
      setCandidateSelection(sel);
    } catch (e) {
      console.error('Failed to load config', e);
      setMessage('Failed to load configuration');
    } finally {
      setLoading(false);
    }
  };

  const validateOpenRouterKey = async () => {
    setOrValidating(true);
    setOrValidationResult(null);
    try {
      const result = await api.validateOpenRouterKey(orApiKeyInput || undefined);
      setOrValidationResult(result);
      if (result.valid) {
        await api.setOpenRouterConfig({ api_key: orApiKeyInput });
        setOrApiKeyInput('');
        const avModels = await api.listAvailableModels('hybrid');
        setOpenrouterModels(avModels.openrouter_models || []);
        const orConfig = await api.getOpenRouterConfig();
        setOpenrouterConfig(orConfig);
      }
    } catch (e) {
      setOrValidationResult({ valid: false, message: `Error: ${e.message}` });
    } finally {
      setOrValidating(false);
    }
  };

  const validateCustomApi = async () => {
    setCustomValidating(true);
    setCustomValidationResult(null);
    try {
      const result = await api.validateCustomApi(customUrlInput || undefined, customKeyInput || undefined);
      setCustomValidationResult(result);
      if (result.valid) {
        await api.setCustomApiConfig({ api_url: customUrlInput, api_key: customKeyInput });
        setCustomKeyInput('');
        const avModels = await api.listAvailableModels('hybrid');
        setCustomModels(avModels.custom_models || []);
        const caConfig = await api.getCustomApiConfig();
        setCustomApiConfig(caConfig);
      }
    } catch (e) {
      setCustomValidationResult({ valid: false, message: `Error: ${e.message}` });
    } finally {
      setCustomValidating(false);
    }
  };

  const isInCouncil = (name) => (councilConfig?.council_models || []).includes(name);
  const isInstalled = (name) => {
    if (!name) return false;
    const lc = name.toLowerCase();
    return ollamaModels.some((a) => a.toLowerCase() === lc || a.toLowerCase().includes(lc) || lc.includes(a.toLowerCase()));
  };

  const toggleCouncilModel = async (name) => {
    const current = councilConfig?.council_models || [];
    let next = current.includes(name) 
      ? current.filter((x) => x !== name)
      : [...current, name];

    let updatedConfig = { ...councilConfig, council_models: next };
    if (councilConfig?.chairman_model && !next.includes(councilConfig.chairman_model)) {
      updatedConfig.chairman_model = next.length > 0 ? next[0] : '';
    }

    setCouncilConfig(updatedConfig);
    try {
      await api.setCouncilConfig(updatedConfig);
    } catch (e) {
      console.error('Failed to save council config', e);
    }
  };

  const selectChairman = async (model) => {
    const updatedConfig = { ...councilConfig, chairman_model: model };
    setCouncilConfig(updatedConfig);
    try {
      await api.setCouncilConfig(updatedConfig);
    } catch (e) {
      console.error('Failed to save chairman', e);
    }
  };

  const refreshModels = async () => {
    setLoading(true);
    try {
      const avModels = await api.listAvailableModels('hybrid');
      setOllamaModels(avModels.ollama_models || []);
      setOpenrouterModels(avModels.openrouter_models || []);
      setCustomModels(avModels.custom_models || []);
    } catch (e) {
      setMessage('Failed to refresh models');
    } finally {
      setLoading(false);
    }
  };

  // Ollama install functions
  const requestInstall = async (model) => {
    setMessage('');
    setInstallLogs([]);
    setInstallAttempts([]);
    setInstalling(true);
    try {
      const success = await new Promise(async (resolve) => {
        await api.installOllamaModelStream(model, (type, data) => {
          if (type === 'install_attempt_start') {
            setInstallLogs((prev) => [...prev, `== Attempting: ${data.candidate} ==`]);
          } else if (type === 'install_attempt_log') {
            setInstallLogs((prev) => [...prev, `[${data.candidate}] ${data.line}`]);
          } else if (type === 'install_attempt_complete') {
            setInstallLogs((prev) => [...prev, `== Attempt ${data.candidate} complete: success=${data.success} ==`]);
          } else if (type === 'install_complete') {
            setInstallLogs((prev) => [...prev, `== completed: success=${data.success} ==`]);
            setInstallAttempts(data.attempts || []);
            if (!data.success && data.attempts) {
              try {
                const summary = data.attempts.map(a => `${a.name}: ${a.success ? 'OK' : 'FAIL'}\n${a.output || ''}`).join('\n---\n');
                setInstallErrorOutput(summary);
              } catch (e) {
                setInstallErrorOutput(data.output || 'Install failed');
              }
            }
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

  const findInstalledInRec = (rec) => {
    if (!rec) return null;
    const rr = typeof rec === 'string' ? { name: rec, candidates: [rec] } : rec;
    for (const m of ollamaModels) {
      if (rr.candidates && rr.candidates.includes(m)) return m;
      if (rr.name === m) return m;
      if (rr.family && m.includes(rr.family)) return m;
    }
    return null;
  };

  const getModelProvider = (modelId) => {
    if (ollamaModels.includes(modelId)) return 'ollama';
    if (openrouterModels.includes(modelId)) return 'openrouter';
    if (customModels.includes(modelId)) return 'custom';
    if (modelId.includes('/')) return 'openrouter';
    return 'ollama';
  };

  const getProviderIcon = (prov) => {
    if (prov === 'ollama') return '💻';
    if (prov === 'openrouter') return '🌐';
    if (prov === 'custom') return '🔧';
    return '❓';
  };

  const allModels = [
    ...ollamaModels.map(m => ({ id: m, provider: 'ollama' })),
    ...openrouterModels.map(m => ({ id: m, provider: 'openrouter' })),
    ...customModels.map(m => ({ id: m, provider: 'custom' })),
  ];

  const getFilteredModels = () => {
    let models = allModels;
    if (activeTab === 'local') models = models.filter(m => m.provider === 'ollama');
    if (activeTab === 'openrouter') models = models.filter(m => m.provider === 'openrouter');
    if (activeTab === 'custom') models = models.filter(m => m.provider === 'custom');
    if (modelSearch) {
      models = models.filter(m => m.id.toLowerCase().includes(modelSearch.toLowerCase()));
    }
    models.sort((a, b) => {
      const aIn = isInCouncil(a.id);
      const bIn = isInCouncil(b.id);
      if (aIn && !bIn) return -1;
      if (!aIn && bIn) return 1;
      return a.id.localeCompare(b.id);
    });
    return models;
  };

  const filteredModels = getFilteredModels();
  const councilModels = councilConfig?.council_models || [];
  const recommended = councilConfig?.recommended_ollama_models || [];

  // Render Local tab content (same as Ollama config)
  const renderLocalTabContent = () => (
    <>
      {/* Installed Models */}
      <div className="installed">
        <h4>Installed Models</h4>
        {ollamaModels.length === 0 && <div>No models detected.</div>}
        <ul className="installed-list">
          {ollamaModels.map((m) => {
            const inCouncil = isInCouncil(m);
            return (
              <li key={m}>
                <div className={`pill-toggle ${inCouncil ? 'active' : ''}`} onClick={() => toggleCouncilModel(m)} style={{ cursor: 'pointer' }} title={inCouncil ? 'Remove from council' : 'Add to council'}>
                  <span className="pill-name">{m}</span>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <div className="model-actions">
                    <button className="uninstall-btn" onClick={() => api.uninstallOllamaModelStream(m, (t, d) => { if (t === 'uninstall_complete' && d.success) setTimeout(() => load(), 800); })} disabled={installing}>Uninstall</button>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Recommended models */}
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
                        const installedHere = ollamaModels.some((a) => a.toLowerCase() === c.toLowerCase() || a.toLowerCase().includes(c.toLowerCase()));
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

      {/* Install logs */}
      {(installing || installLogs.length > 0) && (
        <div className="install-logs">
          <h4>Install logs</h4>
          <div style={{ maxHeight: 160, overflow: 'auto', background: '#0f1720', color: '#e6f0ff', padding: 8, borderRadius: 6, fontSize: 12 }}>
            <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
              {installLogs.length === 0 ? (installing ? 'Starting install...' : '') : installLogs.join('\n')}
            </pre>
          </div>
          {installErrorOutput && (
            <div className="install-error">
              <div className="install-error-header">Install failed</div>
              <pre className="install-error-body">{installErrorOutput}</pre>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button onClick={() => { navigator.clipboard && navigator.clipboard.writeText(installErrorOutput) }}>Copy error</button>
                <button onClick={() => { setInstallLogs([]); setInstallErrorOutput(''); setInstallAttempts([]); }}>Dismiss</button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );

  // Render OpenRouter/Custom/All tab content
  const renderCloudTabContent = () => (
    <>
      {/* API Configuration when on openrouter or custom tab */}
      {activeTab === 'openrouter' && (
        <div className="api-config-section">
          <h4>🌐 OpenRouter API</h4>
          <div className="api-status">
            {openrouterConfig?.api_key_set ? (
              <span className="status-ok">✓ API key set ({openrouterModels.length} models)</span>
            ) : (
              <span className="status-warn">⚠️ No API key configured</span>
            )}
          </div>
          <div className="api-form">
            <input
              type="password"
              placeholder={openrouterConfig?.api_key_set ? `Current: ${openrouterConfig.api_key_masked}` : 'Enter API key'}
              value={orApiKeyInput}
              onChange={(e) => setOrApiKeyInput(e.target.value)}
            />
            <button onClick={validateOpenRouterKey} disabled={orValidating}>
              {orValidating ? '...' : 'Save'}
            </button>
          </div>
          {orValidationResult && (
            <div className={`validation-msg ${orValidationResult.valid ? 'valid' : 'invalid'}`}>
              {orValidationResult.message}
            </div>
          )}
          <small>Get key from <a href="https://openrouter.ai/keys" target="_blank" rel="noopener noreferrer">openrouter.ai/keys</a></small>
        </div>
      )}

      {activeTab === 'custom' && (
        <div className="api-config-section">
          <h4>🔧 Custom API</h4>
          <div className="api-status">
            {customApiConfig?.api_url ? (
              <span className="status-ok">✓ Configured ({customModels.length} models)</span>
            ) : (
              <span className="status-info">Add OpenAI-compatible endpoint</span>
            )}
          </div>
          <div className="api-form">
            <input
              type="text"
              placeholder="API URL (e.g., http://localhost:8080/v1/chat/completions)"
              value={customUrlInput}
              onChange={(e) => setCustomUrlInput(e.target.value)}
              style={{ flex: 2 }}
            />
          </div>
          <div className="api-form">
            <input
              type="password"
              placeholder="API Key (optional)"
              value={customKeyInput}
              onChange={(e) => setCustomKeyInput(e.target.value)}
            />
            <button onClick={validateCustomApi} disabled={customValidating || !customUrlInput}>
              {customValidating ? '...' : 'Save'}
            </button>
          </div>
          {customValidationResult && (
            <div className={`validation-msg ${customValidationResult.valid ? 'valid' : 'invalid'}`}>
              {customValidationResult.message}
            </div>
          )}
        </div>
      )}

      {/* Model list for cloud models */}
      <div className="installed">
        <h4>Available Models</h4>
        <input
          type="text"
          className="model-search"
          placeholder="Search models..."
          value={modelSearch}
          onChange={(e) => setModelSearch(e.target.value)}
          style={{ marginBottom: 12 }}
        />
        {filteredModels.length === 0 && (
          <div>No models found{modelSearch ? ` for "${modelSearch}"` : ''}</div>
        )}
        <ul className="installed-list">
          {filteredModels.slice(0, 100).map((m) => {
            const inCouncil = isInCouncil(m.id);
            return (
              <li key={`${m.provider}-${m.id}`}>
                <div 
                  className={`pill-toggle ${inCouncil ? 'active' : ''}`} 
                  onClick={() => toggleCouncilModel(m.id)} 
                  style={{ cursor: 'pointer' }} 
                  title={inCouncil ? 'Remove from council' : 'Add to council'}
                >
                  <span style={{ marginRight: 6 }}>{getProviderIcon(m.provider)}</span>
                  <span className="pill-name">{m.id}</span>
                </div>
              </li>
            );
          })}
        </ul>
        {filteredModels.length > 100 && (
          <p className="hint">Showing 100 of {filteredModels.length}. Use search.</p>
        )}
      </div>
    </>
  );

  return (
    <div className="provider-config">
      <h3>
        Models
        <span className="header-actions">
          <button className="refresh-emoji" title="Refresh models" onClick={refreshModels} disabled={loading}>🔄</button>
          <button className="gear-btn" title="Configure APIs" onClick={() => setShowConfigModal(true)}>⚙️</button>
        </span>
      </h3>
      {loading && <div>Loading...</div>}
      {message && <div className="message">{message}</div>}

      <div className="installed">
        <h4>Council Models</h4>
        {councilModels.length === 0 && <div>No models selected. Click ⚙️ to add.</div>}
        <ul className="installed-list">
          {councilModels.map((m) => {
            const prov = getModelProvider(m);
            return (
              <li key={m}>
                <div className={`pill-toggle active`} onClick={() => toggleCouncilModel(m)} style={{ cursor: 'pointer' }}>
                  <span style={{ marginRight: 6 }}>{getProviderIcon(prov)}</span>
                  <span className="pill-name">{m}</span>
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="chairman">
        <h4>Chairman</h4>
        <select value={councilConfig?.chairman_model || ''} onChange={(e) => selectChairman(e.target.value)}>
          <option value="">-- choose chairman --</option>
          {councilModels.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </div>

      {showConfigModal && createPortal(
        <div className="modal-backdrop" onClick={() => setShowConfigModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Model Manager</h3>
              <button className="modal-close" onClick={() => setShowConfigModal(false)}>✖</button>
            </div>
            <div className="modal-body">
              {/* Tabs */}
              <div className="model-tabs">
                <button className={activeTab === 'all' ? 'active' : ''} onClick={() => setActiveTab('all')}>
                  All ({allModels.length})
                </button>
                <button className={activeTab === 'local' ? 'active' : ''} onClick={() => setActiveTab('local')}>
                  💻 Local ({ollamaModels.length})
                </button>
                <button className={activeTab === 'openrouter' ? 'active' : ''} onClick={() => setActiveTab('openrouter')}>
                  🌐 OpenRouter ({openrouterModels.length})
                </button>
                <button className={activeTab === 'custom' ? 'active' : ''} onClick={() => setActiveTab('custom')}>
                  🔧 Custom ({customModels.length})
                </button>
              </div>

              {/* Tab Content */}
              {activeTab === 'local' ? renderLocalTabContent() : renderCloudTabContent()}
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
