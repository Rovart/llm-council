import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import Stage1 from './Stage1';
import Stage2 from './Stage2';
import Stage3 from './Stage3';
import './ChatInterface.css';

export default function ChatInterface({
  conversation,
  onSendMessage,
  onRetryPending,
  onSubmitEdited,
  isLoading,
  skipStages,
  provider,
}) {
  const [input, setInput] = useState('');
  const [editingIndex, setEditingIndex] = useState(null);
  const [spinningIndex, setSpinningIndex] = useState(null);
  const [skipStagesToggle, setSkipStagesToggle] = useState(false);
  // Track which message indices have their stages expanded (collapsed by default when complete)
  const [expandedStages, setExpandedStages] = useState({});
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [
    conversation?.messages?.length,
    // Trigger scroll when any stage finishes loading/streaming
    conversation?.messages?.[conversation.messages.length - 1]?.loading?.stage1,
    conversation?.messages?.[conversation.messages.length - 1]?.loading?.stage2,
    conversation?.messages?.[conversation.messages.length - 1]?.loading?.stage3,
    conversation?.messages?.[conversation.messages.length - 1]?.stage3?.streaming
  ]);

  // Clear optimistic spinning state when conversation messages update
  useEffect(() => {
    setSpinningIndex(null);
  }, [conversation?.messages]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    if (editingIndex !== null) {
      // Submit edited message: call parent handler to remove original and resend
      if (onSubmitEdited) onSubmitEdited(editingIndex, input);
      // clear editing state
      setEditingIndex(null);
      setInput('');
      return;
    }

    onSendMessage(input, provider, skipStagesToggle);
    setInput('');
  };

  const handleKeyDown = (e) => {
    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  if (!conversation) {
    return (
      <div className="chat-interface">
        <div className="empty-state">
          <h2>Welcome to LLM Council</h2>
          <p>Create a new conversation to get started</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-interface">
      <div className="messages-container">
        {conversation.messages.length === 0 ? (
          <div className="empty-state">
            <h2>Start a conversation</h2>
            <p>Ask a question to consult the LLM Council</p>
          </div>
        ) : (
          conversation.messages.map((msg, index) => {
            // determine if this message is the last user message for showing retry/edit controls
            const lastUserIndex = conversation.messages.map(m => m.role).lastIndexOf('user');
            const isLastUser = index === lastUserIndex && msg.role === 'user';
            return (
              <div key={index} className="message-group">
                {msg.role === 'user' ? (
                  <div className="user-message">
                    <div className="message-label">You</div>
                    <div className="message-row">
                      <div className="message-content">
                        <div className="markdown-content">
                          <ReactMarkdown>{msg.content}</ReactMarkdown>
                        </div>
                      </div>

                      {/* Controls for failed messages only: edit (pen) and retry (wheel) */}
                      {/* Hide when loading or editing */}
                      {isLastUser && msg.status === 'failed' && !isLoading && editingIndex === null && (
                        <div className="user-controls">
                          <button
                            className="edit-button"
                            aria-label="Edit message"
                            title="Edit message"
                            onClick={() => {
                              setEditingIndex(index);
                              setInput(msg.content || '');
                              setTimeout(() => {
                                const textarea = document.querySelector('.message-input');
                                if (textarea) textarea.focus();
                              }, 50);
                            }}
                          >
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                              <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25z" fill="currentColor" />
                              <path d="M20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" fill="currentColor" />
                            </svg>
                          </button>
                          {
                            // Determine spinning state: derived from message loading/status or optimistic local state
                            (() => {
                              const loadingActive = !!(msg.loading?.stage1 || msg.loading?.stage2 || msg.loading?.stage3);
                              const spinning = spinningIndex === index || (msg.status === 'pending' && loadingActive);
                              return (
                                <button
                                  className={`retry-button ${spinning ? 'spinning' : ''}`}
                                  aria-label="Retry message"
                                  title={msg.status === 'failed' ? 'Retry' : 'Retry (still processing)'}
                                  onClick={() => {
                                    // optimistic local spinning indicator until parent updates message loading
                                    setSpinningIndex(index);
                                    if (onRetryPending) onRetryPending(false);
                                  }}
                                >
                                  <svg className="retry-icon" width="18" height="18" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                                    <path fillRule="evenodd" clipRule="evenodd" d="M12 2a1 1 0 0 1 1 1v2.09a7.002 7.002 0 1 1-6.9 8.93 1 1 0 0 1-1.98.14A9.002 9.002 0 1 0 12 2z" />
                                  </svg>
                                </button>
                              );
                            })()
                          }
                        </div>
                      )}
                    </div>
                    </div>
                ) : (
                  <div className="assistant-message">
                    <div className="message-label">{msg.skipStages ? 'Assistant' : 'LLM Council'}</div>

                  {msg.stage3?.metadata?.summarized_count && (
                    <div className="summary-indicator">
                      <strong>Summary:</strong>{' '}
                      Summarized {msg.stage3.metadata.summarized_count} messages
                      {msg.stage3.metadata.chairman_model ? ` — by ${msg.stage3.metadata.chairman_model}` : ''}
                      {msg.stage3.metadata.summary_generated_at ? ` — ${new Date(msg.stage3.metadata.summary_generated_at).toLocaleString()}` : ''}
                    </div>
                  )}

                  {/* Show stages toggle when stage3 is complete and not skipping */}
                  {!msg.skipStages && msg.stage3?.response && (msg.stage1 || msg.stage2) && (
                    <button
                      className="stages-toggle"
                      onClick={() => setExpandedStages(prev => ({ ...prev, [index]: !prev[index] }))}
                    >
                      <svg
                        className={`toggle-chevron ${expandedStages[index] ? 'expanded' : ''}`}
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <polyline points="6 9 12 15 18 9"></polyline>
                      </svg>
                      {expandedStages[index] ? 'Hide deliberation stages' : 'Show deliberation stages'}
                    </button>
                  )}

                  {/* Only show stage details if not skipping AND (expanded OR still loading) */}
                  {!msg.skipStages && (expandedStages[index] || !msg.stage3?.response || msg.loading?.stage1 || msg.loading?.stage2 || msg.loading?.stage3) && (
                    <>
                      {/* Stage 1 */}
                      {msg.loading?.stage1 && (
                        <div className="stage-loading">
                          <div className="spinner"></div>
                          <span>Running Stage 1: Collecting individual responses...</span>
                        </div>
                      )}
                      {msg.stage1 && <Stage1 responses={msg.stage1} />}

                      {/* Stage 2 */}
                      {msg.loading?.stage2 && (
                        <div className="stage-loading">
                          <div className="spinner"></div>
                          <span>Running Stage 2: Peer rankings...</span>
                        </div>
                      )}
                      {msg.stage2 && (
                        <Stage2
                          rankings={msg.stage2}
                          labelToModel={msg.metadata?.label_to_model}
                          aggregateRankings={msg.metadata?.aggregate_rankings}
                        />
                      )}

                      {/* Stage 3 */}
                      {msg.loading?.stage3 && (
                        <div className="stage-loading">
                          <div className="spinner"></div>
                          <span>Running Stage 3: Final synthesis...</span>
                        </div>
                      )}
                    </>
                  )}

                  {/* Always show final response (Stage 3), but without loading indicator if skipping */}
                  {msg.skipStages && msg.loading?.stage3 && (
                    <div className="loading-indicator">
                      <div className="spinner"></div>
                      <span>Thinking...</span>
                    </div>
                  )}
                  {msg.stage3 && <Stage3 finalResponse={msg.stage3} isSkipped={msg.skipStages} />}
                </div>
                )}
              </div>
            );
          })
        )}

        {isLoading && !skipStages && (
          <div className="loading-indicator">
            <div className="spinner"></div>
            <span>Consulting the council...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <form className="input-form" onSubmit={handleSubmit}>
        <textarea
          className="message-input"
          placeholder={conversation.messages.length === 0 ? "Ask your question... (Shift+Enter for new line, Enter to send)" : "Continue the conversation... (Shift+Enter for new line, Enter to send)"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          rows={3}
        />
        <div className="input-controls">
          <button
            type="submit"
            className="send-button"
            disabled={!input.trim() || isLoading}
          >
            Send
          </button>
          <label className="skip-stages-toggle">
            <input
              type="checkbox"
              checked={skipStagesToggle}
              onChange={(e) => setSkipStagesToggle(e.target.checked)}
              disabled={isLoading}
            />
            <span className="toggle-label">Skip to Chairman</span>
          </label>
        </div>
      </form>
    </div>
  );
}
