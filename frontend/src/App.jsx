import { useState, useEffect, useRef } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [loadingConversationId, setLoadingConversationId] = useState(null); // Track which conversation is loading
  const [currentSkipStages, setCurrentSkipStages] = useState(false);
  const [activeStreams, setActiveStreams] = useState(new Set()); // For sidebar display

  // Cache for in-flight conversation states (streaming updates stored by conversation ID)
  const conversationCacheRef = useRef({});
  // Track which conversations have active streams
  const activeStreamsRef = useRef(new Set());

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
  }, []);

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      // Check if we have a cached version with in-flight streaming state
      if (conversationCacheRef.current[id]) {
        setCurrentConversation(conversationCacheRef.current[id]);
        return;
      }
      const conv = await api.getConversation(id);
      // Check for incomplete messages (interrupted by reload)
      const msgs = conv.messages || [];
      if (msgs.length > 0) {
        const lastMsg = msgs[msgs.length - 1];
        
        // If last message is assistant with complete stage3, ensure preceding user is marked complete
        if (lastMsg.role === 'assistant' && lastMsg.stage3?.response) {
          // Find the preceding user message and mark it complete if it's pending/failed
          for (let i = msgs.length - 2; i >= 0; i--) {
            if (msgs[i].role === 'user') {
              if (msgs[i].status === 'pending' || msgs[i].status === 'failed') {
                msgs[i].status = 'complete';
                try {
                  await api.markUserMessageStatus(id, 'complete');
                } catch (e) {
                  console.warn('Failed to mark message as complete on backend', e);
                }
              }
              break;
            }
          }
        }
        // If last message is user with pending status and no assistant response follows, mark as failed
        else if (lastMsg.role === 'user' && lastMsg.status === 'pending') {
          lastMsg.status = 'failed';
          try {
            await api.markUserMessageStatus(id, 'failed');
          } catch (e) {
            console.warn('Failed to mark message as failed on backend', e);
          }
        }
        // If last message is assistant but incomplete (missing stage3.response), mark preceding user as failed
        else if (lastMsg.role === 'assistant' && (!lastMsg.stage3 || !lastMsg.stage3.response)) {
          // Remove incomplete assistant message and mark user as failed
          msgs.pop();
          const userMsg = msgs.length > 0 ? msgs[msgs.length - 1] : null;
          if (userMsg && userMsg.role === 'user') {
            userMsg.status = 'failed';
            try {
              await api.markUserMessageStatus(id, 'failed');
            } catch (e) {
              console.warn('Failed to mark message as failed on backend', e);
            }
          }
        }
      }
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  // Helper to update conversation state both in currentConversation and cache
  const updateConversationState = (targetConversationId, updater) => {
    // Always update the cache
    const cached = conversationCacheRef.current[targetConversationId];
    if (cached) {
      const updated = updater(cached);
      if (updated && updated !== cached) {
        conversationCacheRef.current[targetConversationId] = updated;
      }
    }
    // Update currentConversation only if it's the active one
    setCurrentConversation((prev) => {
      if (!prev || prev.id !== targetConversationId) return prev;
      const updated = updater(prev);
      // Also update cache with the latest
      if (updated) {
        conversationCacheRef.current[targetConversationId] = updated;
      }
      return updated;
    });
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
      setCurrentConversation(newConv);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    // Save current conversation to cache before switching (if there's an active stream)
    if (currentConversation && activeStreamsRef.current.has(currentConversation.id)) {
      conversationCacheRef.current[currentConversation.id] = currentConversation;
    }
    setCurrentConversationId(id);
    if (!id) {
      setCurrentConversation(null);
    }
  };

  const handleSendMessage = async (content, provider, skipStages = false, replyTo = null) => {
    if (!currentConversationId) return;

    const targetConversationId = currentConversationId;
    setLoadingConversationId(targetConversationId);
    setActiveStreams((prev) => new Set(prev).add(targetConversationId));
    setCurrentSkipStages(skipStages);

    try {
      // Remove any old pending user messages before sending a new message
      try {
        await api.removePendingMessages(currentConversationId, false);
      } catch (e) {
        // non-fatal
        console.warn('removePendingMessages failed', e);
      }
      // Optimistically add user message to UI (include reply_to if replying)
      const userMessage = { role: 'user', content };
      if (replyTo) {
        userMessage.reply_to = replyTo.response;
      }
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      // Create a partial assistant message that will be updated progressively
      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        skipStages: skipStages, // Track if stages were skipped
        loading: {
          stage1: false,
          stage2: false,
          stage3: false,
        },
      };

      // Add the partial assistant message and cache initial state
      setCurrentConversation((prev) => {
        const updated = { ...prev, messages: [...prev.messages, assistantMessage] };
        conversationCacheRef.current[targetConversationId] = updated;
        return updated;
      });

      // Mark this conversation as having an active stream
      activeStreamsRef.current.add(targetConversationId);

      // Prepare replyToResponse if replying to a specific message
      const replyToResponse = replyTo ? replyTo.response : null;

      // Send message with streaming, include selected provider and skipStages flag
      await api.sendMessageStream(currentConversationId, content, (eventType, event) => {
        switch (eventType) {
          case 'stage1_model_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              if (!lastMsg.stage1) lastMsg.stage1 = [];
              // ensure an entry for this model exists
              if (!lastMsg.stage1.find(r => r.model === event.model)) {
                lastMsg.stage1.push({ model: event.model, response: '' });
              }
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_model_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              if (!lastMsg.stage2) lastMsg.stage2 = [];
              if (!lastMsg.stage2.find(r => r.model === event.model)) {
                lastMsg.stage2.push({ model: event.model, ranking: '' });
              }
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;
          case 'stage1_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsg = { ...messages[messages.length - 1] };
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage1 = true;
              messages[messages.length - 1] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage1_chunk':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              // Initialize stage1 array if needed
              if (!lastMsg.stage1) {
                lastMsg.stage1 = [];
              } else {
                lastMsg.stage1 = [...lastMsg.stage1];
              }

              // Find or create the entry for this model
              const modelIndex = lastMsg.stage1.findIndex(r => r.model === event.model);
              if (modelIndex === -1) {
                lastMsg.stage1.push({ model: event.model, response: event.content || '' });
              } else {
                const updatedEntry = { ...lastMsg.stage1[modelIndex] };
                updatedEntry.response += event.content || '';
                lastMsg.stage1[modelIndex] = updatedEntry;
              }
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage1_complete':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              lastMsg.stage1 = event.data;
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage1 = false;
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage2 = true;
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_metadata':
            // Received metadata for stage2 (e.g., label_to_model mapping)
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              // attach metadata (label_to_model and any other info)
              lastMsg.metadata = event.data || lastMsg.metadata;
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_chunk':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              // Initialize stage2 array if needed
              if (!lastMsg.stage2) {
                lastMsg.stage2 = [];
              } else {
                lastMsg.stage2 = [...lastMsg.stage2];
              }

              // Find or create the entry for this model
              const modelIndex = lastMsg.stage2.findIndex(r => r.model === event.model);
              if (modelIndex === -1) {
                lastMsg.stage2.push({ model: event.model, ranking: event.content || '' });
              } else {
                const updatedEntry = { ...lastMsg.stage2[modelIndex] };
                updatedEntry.ranking += event.content || '';
                lastMsg.stage2[modelIndex] = updatedEntry;
              }
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_complete':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              lastMsg.stage2 = event.data;
              lastMsg.metadata = event.metadata;
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage2 = false;
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage3_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage3 = true;
              // Initialize streaming response
              if (!lastMsg.stage3) {
                lastMsg.stage3 = { model: '', response: '', streaming: true };
              }
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage3_chunk':
            // Handle streaming chunks - append to the response
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              // Create a copy of the last message to avoid mutating state directly
              const lastMsg = { ...messages[lastMsgIndex] };

              // Create a copy of stage3 object or initialize it
              if (!lastMsg.stage3) {
                lastMsg.stage3 = { model: event.model || '', response: '', streaming: true };
              } else {
                lastMsg.stage3 = { ...lastMsg.stage3 };
              }

              lastMsg.stage3.response += event.content || '';
              lastMsg.stage3.model = event.model || lastMsg.stage3.model;
              lastMsg.stage3.streaming = true;
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage3_complete':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsg = { ...messages[messages.length - 1] };
              lastMsg.stage3 = event.data;
              lastMsg.stage3.streaming = false;
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage3 = false;
              messages[messages.length - 1] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'title_complete':
            // Reload conversations to get updated title
            loadConversations();
            break;

          case 'complete':
            // Stream complete, remove from active streams and clear cache
            activeStreamsRef.current.delete(targetConversationId);
            delete conversationCacheRef.current[targetConversationId];
            loadConversations();
            setActiveStreams((prev) => {
              const next = new Set(prev);
              next.delete(targetConversationId);
              return next;
            });
            if (targetConversationId === currentConversationId) {
              setLoadingConversationId(null);
            }
            setCurrentSkipStages(false);
            break;

          case 'error':
            console.error('Stream error:', event.message);
            activeStreamsRef.current.delete(targetConversationId);
            delete conversationCacheRef.current[targetConversationId];
            setActiveStreams((prev) => {
              const next = new Set(prev);
              next.delete(targetConversationId);
              return next;
            });
            if (targetConversationId === currentConversationId) {
              setLoadingConversationId(null);
            }
            setCurrentSkipStages(false);
            break;

          default:
            console.log('Unknown event type:', eventType);
        }
      }, provider, skipStages, replyToResponse);
    } catch (error) {
      console.error('Failed to send message:', error);
      // Remove optimistic messages on error
      activeStreamsRef.current.delete(targetConversationId);
      delete conversationCacheRef.current[targetConversationId];
      setCurrentConversation((prev) => {
        if (!prev || !Array.isArray(prev.messages)) return prev;
        return { ...prev, messages: prev.messages.slice(0, -2) };
      });
      setActiveStreams((prev) => {
        const next = new Set(prev);
        next.delete(targetConversationId);
        return next;
      });
      setLoadingConversationId(null);
      setCurrentSkipStages(false);
    }
  };

  const handleRetryPending = async (skipStages = false) => {
    if (!currentConversationId) return;

    const targetConversationId = currentConversationId;
    setLoadingConversationId(targetConversationId);
    setActiveStreams((prev) => new Set(prev).add(targetConversationId));
    setCurrentSkipStages(skipStages);

    try {
      // Remove older pending messages but keep the last one (which we are retrying)
      try {
        await api.removePendingMessages(currentConversationId, true);
      } catch (e) {
        console.warn('removePendingMessages failed', e);
      }

      // Add a placeholder assistant message so stages can be displayed during retry
      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        skipStages: skipStages,
        loading: {
          stage1: false,
          stage2: false,
          stage3: false,
        },
      };
      setCurrentConversation((prev) => {
        if (!prev || prev.id !== targetConversationId) return prev;
        const updated = { ...prev, messages: [...prev.messages, assistantMessage] };
        conversationCacheRef.current[targetConversationId] = updated;
        return updated;
      });

      // Mark this conversation as having an active stream
      activeStreamsRef.current.add(targetConversationId);

      // Use the same event handling as sendMessageStream
      await api.retryPendingStream(currentConversationId, (eventType, event) => {
        switch (eventType) {
          case 'stage1_model_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              if (!lastMsg.stage1) lastMsg.stage1 = [];
              if (!lastMsg.stage1.find(r => r.model === event.model)) {
                lastMsg.stage1.push({ model: event.model, response: '' });
              }
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_model_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              if (!lastMsg.stage2) lastMsg.stage2 = [];
              if (!lastMsg.stage2.find(r => r.model === event.model)) {
                lastMsg.stage2.push({ model: event.model, ranking: '' });
              }
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;
          case 'stage1_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsg = { ...messages[messages.length - 1] };
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage1 = true;
              messages[messages.length - 1] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage1_chunk':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              if (!lastMsg.stage1) lastMsg.stage1 = [];
              else lastMsg.stage1 = [...lastMsg.stage1];

              const modelIndex = lastMsg.stage1.findIndex(r => r.model === event.model);
              if (modelIndex === -1) {
                lastMsg.stage1.push({ model: event.model, response: event.content || '' });
              } else {
                const updatedEntry = { ...lastMsg.stage1[modelIndex] };
                updatedEntry.response += event.content || '';
                lastMsg.stage1[modelIndex] = updatedEntry;
              }
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage1_complete':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              lastMsg.stage1 = event.data;
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage1 = false;
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsg = { ...messages[messages.length - 1] };
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage2 = true;
              messages[messages.length - 1] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_metadata':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsg = { ...messages[messages.length - 1] };
              lastMsg.metadata = event.data || lastMsg.metadata;
              messages[messages.length - 1] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_chunk':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              if (!lastMsg.stage2) lastMsg.stage2 = [];
              else lastMsg.stage2 = [...lastMsg.stage2];

              const modelIndex = lastMsg.stage2.findIndex(r => r.model === event.model);
              if (modelIndex === -1) {
                lastMsg.stage2.push({ model: event.model, ranking: event.content || '' });
              } else {
                const updatedEntry = { ...lastMsg.stage2[modelIndex] };
                updatedEntry.ranking += event.content || '';
                lastMsg.stage2[modelIndex] = updatedEntry;
              }
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage2_complete':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsg = { ...messages[messages.length - 1] };
              lastMsg.stage2 = event.data;
              lastMsg.metadata = event.metadata;
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage2 = false;
              messages[messages.length - 1] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage3_start':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsg = { ...messages[messages.length - 1] };
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage3 = true;
              if (!lastMsg.stage3) lastMsg.stage3 = { model: '', response: '', streaming: true };
              messages[messages.length - 1] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage3_chunk':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              if (!lastMsg.stage3) lastMsg.stage3 = { model: '', response: '', streaming: true };
              else lastMsg.stage3 = { ...lastMsg.stage3 };

              lastMsg.stage3.response += event.content || '';
              lastMsg.stage3.model = event.model || lastMsg.stage3.model;
              lastMsg.stage3.streaming = true;
              messages[lastMsgIndex] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'stage3_complete':
            updateConversationState(targetConversationId, (prev) => {
              const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
              if (messages.length === 0) return prev;
              const lastMsg = { ...messages[messages.length - 1] };
              lastMsg.stage3 = event.data;
              lastMsg.stage3.streaming = false;
              lastMsg.loading = lastMsg.loading || {};
              lastMsg.loading.stage3 = false;
              messages[messages.length - 1] = lastMsg;
              return { ...prev, messages };
            });
            break;

          case 'title_complete':
            loadConversations();
            break;

          case 'complete':
            // Stream complete, remove from active streams and clear cache
            activeStreamsRef.current.delete(targetConversationId);
            delete conversationCacheRef.current[targetConversationId];
            loadConversations();
            setActiveStreams((prev) => {
              const next = new Set(prev);
              next.delete(targetConversationId);
              return next;
            });
            if (targetConversationId === currentConversationId) {
              setLoadingConversationId(null);
            }
            setCurrentSkipStages(false);
            break;

          case 'error':
            console.error('Retry stream error:', event.message);
            activeStreamsRef.current.delete(targetConversationId);
            delete conversationCacheRef.current[targetConversationId];
            setActiveStreams((prev) => {
              const next = new Set(prev);
              next.delete(targetConversationId);
              return next;
            });
            if (targetConversationId === currentConversationId) {
              setLoadingConversationId(null);
            }
            setCurrentSkipStages(false);
            break;

          default:
            console.log('Unknown event type (retry):', eventType);
        }
      }, provider, skipStages);
    } catch (error) {
      console.error('Failed to retry pending message:', error);
      activeStreamsRef.current.delete(targetConversationId);
      delete conversationCacheRef.current[targetConversationId];
      setActiveStreams((prev) => {
        const next = new Set(prev);
        next.delete(targetConversationId);
        return next;
      });
      setLoadingConversationId(null);
      setCurrentSkipStages(false);
    }
  };

  const handleSubmitEdited = async (editedIndex, content) => {
    if (!currentConversation) return;
    // Remove the edited user message and the assistant message that follows it (if any)
    setCurrentConversation((prev) => {
      const messages = Array.isArray(prev.messages) ? [...prev.messages] : [];
      // Remove the message at editedIndex
      if (editedIndex >= 0 && editedIndex < messages.length && messages[editedIndex].role === 'user') {
        messages.splice(editedIndex, 1);
        // If next message exists and is assistant, remove it too
        if (editedIndex < messages.length && messages[editedIndex] && messages[editedIndex].role === 'assistant') {
          messages.splice(editedIndex, 1);
        }
      }
      return { ...prev, messages };
    });

    // Now send as a new message (will add optimistic entries)
    await handleSendMessage(content, provider, currentSkipStages);
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        onConversationsChange={setConversations}
        activeStreams={activeStreams}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        onRetryPending={handleRetryPending}
        onSubmitEdited={handleSubmitEdited}
        isLoading={loadingConversationId === currentConversationId}
        skipStages={currentSkipStages}
        provider="hybrid"
      />
    </div>
  );
}

export default App;
