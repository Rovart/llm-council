import { useState, useEffect } from 'react';
import './Sidebar.css';
import ProviderConfig from './ProviderConfig';

export default function Sidebar({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  provider,
  onProviderChange,
}) {
  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h1>LLM Council</h1>
        <button className="new-conversation-btn" onClick={onNewConversation}>
          + New Conversation
        </button>
      </div>

      <div className="provider-toggle">
        <label>
          <input
            type="radio"
            name="provider"
            value="openrouter"
            checked={provider === 'openrouter'}
            onChange={() => onProviderChange('openrouter')}
          />
          OpenRouter (paid)
        </label>
        <label>
          <input
            type="radio"
            name="provider"
            value="ollama"
            checked={provider === 'ollama'}
            onChange={() => onProviderChange('ollama')}
          />
          Ollama (local, free)
        </label>
      </div>

      <div className="conversation-list">
        {conversations.length === 0 ? (
          <div className="no-conversations">No conversations yet</div>
        ) : (
          conversations.map((conv) => (
            <div
              key={conv.id}
              className={`conversation-item ${
                conv.id === currentConversationId ? 'active' : ''
              }`}
              onClick={() => onSelectConversation(conv.id)}
            >
              <div className="conversation-title">
                {conv.title || 'New Conversation'}
              </div>
              <div className="conversation-meta">
                {conv.message_count} messages
              </div>
            </div>
          ))
        )}
      </div>
      <div className="sidebar-footer">
        <ProviderConfig provider={provider} />
      </div>
    </div>
  );
}
