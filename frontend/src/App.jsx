import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [currentSkipStages, setCurrentSkipStages] = useState(false);
  const [provider, setProvider] = useState('ollama');

  // On mount, load saved council config and prefer its provider if present
  useEffect(() => {
    const loadProvider = async () => {
      try {
        const conf = await api.getCouncilConfig();
        if (conf && conf.provider) setProvider(conf.provider);
      } catch (e) {
        // ignore - keep default
      }
    };
    loadProvider();
  }, []);

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
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
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
    setCurrentConversationId(id);
    if (!id) {
      setCurrentConversation(null);
    }
  };

  const handleSendMessage = async (content, provider, skipStages = false) => {
    if (!currentConversationId) return;

    setIsLoading(true);
    setCurrentSkipStages(skipStages);
    try {
      // Optimistically add user message to UI
      const userMessage = { role: 'user', content };
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

      // Add the partial assistant message
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
      }));

      // Send message with streaming, include selected provider and skipStages flag
      await api.sendMessageStream(currentConversationId, content, (eventType, event) => {
        switch (eventType) {
          case 'stage1_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage1 = true;
              return { ...prev, messages };
            });
            break;

          case 'stage1_chunk':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              messages[lastMsgIndex] = lastMsg;

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

              return { ...prev, messages };
            });
            break;

          case 'stage1_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              messages[lastMsgIndex] = lastMsg;

              lastMsg.stage1 = event.data;
              lastMsg.loading.stage1 = false;
              return { ...prev, messages };
            });
            break;

          case 'stage2_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              messages[lastMsgIndex] = lastMsg;

              lastMsg.loading.stage2 = true;
              return { ...prev, messages };
            });
            break;

          case 'stage2_metadata':
            // Received metadata for stage2 (e.g., label_to_model mapping)
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              messages[lastMsgIndex] = lastMsg;

              // attach metadata (label_to_model and any other info)
              lastMsg.metadata = event.data || lastMsg.metadata;
              return { ...prev, messages };
            });
            break;

          case 'stage2_chunk':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              messages[lastMsgIndex] = lastMsg;

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

              return { ...prev, messages };
            });
            break;

          case 'stage2_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              messages[lastMsgIndex] = lastMsg;

              lastMsg.stage2 = event.data;
              lastMsg.metadata = event.metadata;
              lastMsg.loading.stage2 = false;
              return { ...prev, messages };
            });
            break;

          case 'stage3_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsgIndex = messages.length - 1;
              const lastMsg = { ...messages[lastMsgIndex] };
              messages[lastMsgIndex] = lastMsg;

              lastMsg.loading.stage3 = true;
              // Initialize streaming response
              if (!lastMsg.stage3) {
                lastMsg.stage3 = { model: '', response: '', streaming: true };
              }
              return { ...prev, messages };
            });
            break;

          case 'stage3_chunk':
            // Handle streaming chunks - append to the response
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsgIndex = messages.length - 1;
              // Create a copy of the last message to avoid mutating state directly
              const lastMsg = { ...messages[lastMsgIndex] };
              messages[lastMsgIndex] = lastMsg;

              // Create a copy of stage3 object or initialize it
              if (!lastMsg.stage3) {
                lastMsg.stage3 = { model: event.model || '', response: '', streaming: true };
              } else {
                lastMsg.stage3 = { ...lastMsg.stage3 };
              }

              lastMsg.stage3.response += event.content || '';
              lastMsg.stage3.model = event.model || lastMsg.stage3.model;
              lastMsg.stage3.streaming = true;
              return { ...prev, messages };
            });
            break;

          case 'stage3_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage3 = event.data;
              lastMsg.stage3.streaming = false;
              lastMsg.loading.stage3 = false;
              return { ...prev, messages };
            });
            break;

          case 'title_complete':
            // Reload conversations to get updated title
            loadConversations();
            break;

          case 'complete':
            // Stream complete, reload conversations list
            loadConversations();
            setIsLoading(false);
            setCurrentSkipStages(false);
            break;

          case 'error':
            console.error('Stream error:', event.message);
            setIsLoading(false);
            setCurrentSkipStages(false);
            break;

          default:
            console.log('Unknown event type:', eventType);
        }
      }, provider, skipStages);
    } catch (error) {
      console.error('Failed to send message:', error);
      // Remove optimistic messages on error
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -2),
      }));
      setIsLoading(false);
      setCurrentSkipStages(false);
    }
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        provider={provider}
        onProviderChange={setProvider}
        onConversationsChange={setConversations}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
        skipStages={currentSkipStages}
        provider={provider}
      />
    </div>
  );
}

export default App;
