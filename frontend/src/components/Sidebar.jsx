import { useState, useEffect } from 'react';
import './Sidebar.css';
import HybridConfig from './HybridConfig';
import { api } from '../api';

export default function Sidebar({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  onConversationsChange,
  activeStreams,
}) {
  const deleteConversation = async (id) => {
    try {
      await api.deleteConversation(id);
      onConversationsChange(conversations.filter(c => c.id !== id));
      if (currentConversationId === id) {
        onSelectConversation(null);
      }
    } catch (e) {
      console.error('Failed to delete conversation', e);
    }
  };

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h1>LLM Council</h1>
        <button className="new-conversation-btn" onClick={onNewConversation}>
          + New Conversation
        </button>
      </div>

      <div className="conversation-list">
        {conversations.length === 0 ? (
          <div className="no-conversations">No conversations yet</div>
        ) : (
          conversations.map((conv) => {
            const isStreaming = activeStreams && activeStreams.has(conv.id);
            return (
              <div
                key={conv.id}
                className={`conversation-item ${
                  conv.id === currentConversationId ? 'active' : ''
                } ${isStreaming ? 'streaming' : ''}`}
                onClick={() => onSelectConversation(conv.id)}
              >
                <div className="conversation-content">
                  <div className="conversation-title">
                    {isStreaming && <span className="sidebar-spinner"></span>}
                    {conv.title || 'New Conversation'}
                  </div>
                  <div className="conversation-meta">
                    {conv.message_count} messages
                  </div>
                </div>
                <button className="delete-btn" onClick={(e) => { e.stopPropagation(); deleteConversation(conv.id); }}>×</button>
              </div>
            );
          })
        )}
      </div>
      <div className="sidebar-footer">
        <HybridConfig />
      </div>
    </div>
  );
}
