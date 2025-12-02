import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import Stage1 from './Stage1';
import Stage2 from './Stage2';
import Stage3 from './Stage3';
import './ChatInterface.css';

// How many messages to show initially (from the end)
const INITIAL_MESSAGES_COUNT = 20;
const LOAD_MORE_COUNT = 20;

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
  // Track which message we are replying to (contains the finalResponse object)
  const [replyingTo, setReplyingTo] = useState(null);
  // How many messages to show from the end (for lazy loading)
  const [visibleCount, setVisibleCount] = useState(INITIAL_MESSAGES_COUNT);
  const messagesEndRef = useRef(null);
  const messagesContainerRef = useRef(null);
  const inputRef = useRef(null);
  const messageRefs = useRef({});
  // Track which messages have been rendered (to avoid re-animating on re-render)
  const seenMessagesRef = useRef(new Set());

  // Helper to strip markdown formatting for plain text preview
  const stripMarkdown = (text) => {
    if (!text) return '';
    return text
      .replace(/\*\*(.+?)\*\*/g, '$1')  // bold
      .replace(/\*(.+?)\*/g, '$1')      // italic
      .replace(/__(.+?)__/g, '$1')      // bold
      .replace(/_(.+?)_/g, '$1')        // italic
      .replace(/~~(.+?)~~/g, '$1')      // strikethrough
      .replace(/`(.+?)`/g, '$1')        // inline code
      .replace(/^#+\s*/gm, '')          // headers
      .replace(/\[(.+?)\]\(.+?\)/g, '$1') // links
      .replace(/^[-*]\s+/gm, '')        // list items
      .replace(/^\d+\.\s+/gm, '');      // numbered lists
  };

  // Memoized filtered messages (without summaries) - stable reference for rendering
  // Each entry includes the original index for stable keys
  const filteredMessages = useMemo(() => {
    if (!conversation?.messages) return [];
    return conversation.messages
      .map((m, originalIndex) => ({ ...m, _originalIndex: originalIndex }))
      .filter((m) => !m.stage3?.metadata?.summarized_count);
  }, [conversation?.messages]);

  // Visible messages (for lazy loading) - show only the last N messages
  const visibleMessages = useMemo(() => {
    if (filteredMessages.length <= visibleCount) return filteredMessages;
    return filteredMessages.slice(-visibleCount);
  }, [filteredMessages, visibleCount]);

  // Check if there are more messages to load
  const hasMoreMessages = filteredMessages.length > visibleCount;

  // Load more messages when scrolling up
  const handleLoadMore = useCallback(() => {
    setVisibleCount(prev => Math.min(prev + LOAD_MORE_COUNT, filteredMessages.length));
  }, [filteredMessages.length]);

  // Scroll to a message by finding the assistant message that contains the reply_to text
  const scrollToReplySource = useCallback((replyToText) => {
    // Find the message in filtered messages whose stage3.response matches
    const msg = filteredMessages.find(
      (m) => m.role === 'assistant' && m.stage3?.response === replyToText
    );
    if (!msg) return;
    
    const originalIndex = msg._originalIndex;
    
    // If message is not in visible range, expand to show it
    const msgIndexInFiltered = filteredMessages.findIndex(m => m._originalIndex === originalIndex);
    const hiddenCount = filteredMessages.length - visibleCount;
    if (msgIndexInFiltered < hiddenCount) {
      // Need to load more messages to show this one
      setVisibleCount(filteredMessages.length - msgIndexInFiltered + 5);
      // Wait for render then scroll
      setTimeout(() => {
        const el = messageRefs.current[originalIndex];
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.classList.add('highlight-message');
          setTimeout(() => el?.classList.remove('highlight-message'), 1500);
        }
      }, 100);
    } else {
      // Message is already visible
      const el = messageRefs.current[originalIndex];
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('highlight-message');
        setTimeout(() => el?.classList.remove('highlight-message'), 1500);
      }
    }
  }, [filteredMessages, visibleCount]);

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

  // Clear replyingTo and reset visible count when conversation changes
  useEffect(() => {
    setReplyingTo(null);
    setVisibleCount(INITIAL_MESSAGES_COUNT);
    seenMessagesRef.current = new Set();
  }, [conversation?.id]);

  const handleReply = (finalResponse) => {
    setReplyingTo(finalResponse);
    // Focus the input
    setTimeout(() => {
      inputRef.current?.focus();
    }, 50);
  };

  const clearReply = () => {
    setReplyingTo(null);
  };

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

    // Pass replyTo context if replying to a message
    onSendMessage(input, provider, skipStagesToggle, replyingTo);
    setInput('');
    setReplyingTo(null);
  };

  const handleKeyDown = (e) => {
    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };
  // Compute lastUserIndex once for retry/edit controls (using original indices)
  const lastUserOriginalIndex = useMemo(() => {
    const lastUser = [...filteredMessages].reverse().find(m => m.role === 'user');
    return lastUser ? lastUser._originalIndex : -1;
  }, [filteredMessages]);

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
      <div className="messages-container" ref={messagesContainerRef}>
        {/* Load more button for lazy loading */}
        {hasMoreMessages && (
          <div className="load-more-container">
            <button className="load-more-button" onClick={handleLoadMore}>
              Load earlier messages ({filteredMessages.length - visibleCount} more)
            </button>
          </div>
        )}
        {filteredMessages.length === 0 ? (
          <div className="empty-state">
            <h2>Start a conversation</h2>
            <p>Ask a question to consult the LLM Council</p>
          </div>
        ) : (
          visibleMessages.map((msg) => {
            const originalIndex = msg._originalIndex;
            const isLastUser = originalIndex === lastUserOriginalIndex && msg.role === 'user';
            // Only animate if this message hasn't been seen yet
            const isNew = !seenMessagesRef.current.has(originalIndex);
            if (isNew) {
              seenMessagesRef.current.add(originalIndex);
            }
            return (
              <div 
                key={originalIndex} 
                className={`message-group${isNew ? ' animate-in' : ''}`} 
                ref={(el) => (messageRefs.current[originalIndex] = el)}
              >
                {msg.role === 'user' ? (
                  <div className="user-message">
                    <div className="message-label">You</div>
                    <div className="message-row">
                      <div className="message-content">
                        {/* Show reply reference if this message is a reply */}
                        {msg.reply_to && (
                          <div
                            className="message-reply-ref clickable"
                            onClick={() => scrollToReplySource(msg.reply_to)}
                            title="Click to scroll to original message"
                          >
                            <div className="reply-ref-bar"></div>
                            <div className="reply-ref-text">
                              {stripMarkdown(msg.reply_to).length > 150
                                ? stripMarkdown(msg.reply_to).substring(0, 150) + '...'
                                : stripMarkdown(msg.reply_to)}
                            </div>
                          </div>
                        )}
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
                              setEditingIndex(originalIndex);
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
                              const spinning = spinningIndex === originalIndex || (msg.status === 'pending' && loadingActive);
                              return (
                                <button
                                  className={`retry-button ${spinning ? 'spinning' : ''}`}
                                  aria-label="Retry message"
                                  title={msg.status === 'failed' ? 'Retry' : 'Retry (still processing)'}
                                  onClick={() => {
                                    // optimistic local spinning indicator until parent updates message loading
                                    setSpinningIndex(originalIndex);
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

                  {/* Show stages toggle when stage3 is complete and not skipping, and stages have actual content */}
                  {!msg.skipStages && msg.stage3?.response && (msg.stage1?.length > 0 || msg.stage2?.length > 0) && (
                    <button
                      className="stages-toggle"
                      onClick={() => setExpandedStages(prev => ({ ...prev, [originalIndex]: !prev[originalIndex] }))}
                    >
                      <svg
                        className={`toggle-chevron ${expandedStages[originalIndex] ? 'expanded' : ''}`}
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <polyline points="6 9 12 15 18 9"></polyline>
                      </svg>
                      {expandedStages[originalIndex] ? 'Hide deliberation stages' : 'Show deliberation stages'}
                    </button>
                  )}

                  {/* Only show stage details if not skipping AND (expanded OR still loading) */}
                  {!msg.skipStages && (expandedStages[originalIndex] || !msg.stage3?.response || msg.loading?.stage1 || msg.loading?.stage2 || msg.loading?.stage3) && (
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
                  {msg.stage3 && <Stage3 finalResponse={msg.stage3} isSkipped={msg.skipStages} onReply={handleReply} />}
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
        {/* WhatsApp-style reply placeholder */}
        {replyingTo && (
          <div className="reply-placeholder">
            <div className="reply-content">
              <div className="reply-label">
                Replying to {replyingTo.model.split('/')[1] || replyingTo.model}
              </div>
              <div className="reply-preview">
                {stripMarkdown(replyingTo.response).length > 150
                  ? stripMarkdown(replyingTo.response).substring(0, 150) + '...'
                  : stripMarkdown(replyingTo.response)}
              </div>
            </div>
            <button
              type="button"
              className="reply-close"
              onClick={clearReply}
              aria-label="Cancel reply"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
              </svg>
            </button>
          </div>
        )}
        <textarea
          ref={inputRef}
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
