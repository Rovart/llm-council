import { useState, useEffect } from 'react';
import { api } from '../api';
import './ProviderConfig.css';

export default function ProviderConfig({ provider }) {
  const [available, setAvailable] = useState([]);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

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
      setConfig(conf);
    } catch (e) {
      console.error('Failed to load provider config', e);
      setMessage('Failed to load provider info');
    } finally {
      setLoading(false);
    }
  };

  const isInstalled = (name) => available.includes(name);
  const isInCouncil = (name) => (config?.council_models || []).includes(name);

  const toggleCouncilModel = (name) => {
    const current = config?.council_models || [];
    let next;
    if (current.includes(name)) {
      next = current.filter((x) => x !== name);
    } else {
      next = [...current, name];
    }
    setConfig({ ...config, council_models: next });
  };

  const saveCouncil = async () => {
    try {
      const conf = { ...config };
      if (!conf.council_models) conf.council_models = [];
      await api.setCouncilConfig(conf);
      setMessage('Saved');
    } catch (e) {
      console.error(e);
      setMessage('Failed to save');
    }
  };

  const selectChairman = (model) => {
    setConfig({ ...config, chairman_model: model });
  };

  const requestInstall = async (model) => {
    setMessage(`Installing ${model} ...`);
    try {
      const res = await api.installOllamaModel(model);
      if (res.success) {
        setMessage(`Installed ${model}`);
        // reload models
        load();
      } else {
        setMessage(res.output || 'Install failed');
      }
    } catch (e) {
      console.error(e);
      setMessage('Install request failed');
    }
  };

  const recommended = (config && config.recommended_ollama_models) || [];

  if (!provider || provider !== 'ollama') return null;

  return (
    <div className="provider-config">
      <h3>Ollama Models</h3>
      {loading && <div>Loading...</div>}
      {message && <div className="message">{message}</div>}

      <div className="installed">
        <h4>Installed Models</h4>
        {available.length === 0 && <div>No models detected.</div>}
        <ul>
          {available.map((m) => (
            <li key={m}>
              <label>
                <input type="checkbox" checked={isInCouncil(m)} onChange={() => toggleCouncilModel(m)} />
                {m}
              </label>
            </li>
          ))}
        </ul>
      </div>

      <div className="recommended">
        <h4>Recommended models</h4>
        {recommended.length === 0 && <div>No recommendations.</div>}
        <ul>
          {recommended.map((m) => (
            <li key={m}>
              <span>{m}</span>
              {isInstalled(m) ? (
                <em> (installed)</em>
              ) : (
                <button onClick={() => requestInstall(m)}>Install</button>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="chairman">
        <h4>Chairman</h4>
        <select value={config?.chairman_model || ''} onChange={(e) => selectChairman(e.target.value)}>
          <option value="">-- choose chairman --</option>
          {(config?.council_models || []).map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </div>

      <div className="actions">
        <button onClick={saveCouncil}>Save Council</button>
      </div>
    </div>
  );
}
