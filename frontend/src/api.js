/**
 * API client for the LLM Council backend.
 */

const API_BASE = 'http://localhost:8001';

export const api = {
  /**
   * List all conversations.
   */
  async listConversations() {
    const response = await fetch(`${API_BASE}/api/conversations`);
    if (!response.ok) {
      throw new Error('Failed to list conversations');
    }
    return response.json();
  },

  /**
   * Create a new conversation.
   */
  async createConversation() {
    const response = await fetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      throw new Error('Failed to create conversation');
    }
    return response.json();
  },

  /**
   * Get a specific conversation.
   */
  async getConversation(conversationId) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`);
    if (!response.ok) throw new Error('Failed to get conversation');
    return response.json();
  },

  /**
   * Delete a conversation.
   */
  async deleteConversation(conversationId) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete conversation');
    return response.json();
  },

  /**
   * Send a message in a conversation.
   */
  async sendMessage(conversationId, content, provider = null) {
    const body = { content };
    if (provider) body.provider = provider;

    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      }
    );
    if (!response.ok) {
      throw new Error('Failed to send message');
    }
    return response.json();
  },

  /**
   * Send a message and receive streaming updates.
   * @param {string} conversationId - The conversation ID
   * @param {string} content - The message content
   * @param {function} onEvent - Callback function for each event: (eventType, data) => void
   * @param {string} provider - Optional provider ('ollama' or 'openrouter')
   * @param {boolean} skipStages - Optional flag to skip stages 1 and 2
   * @param {string} replyToResponse - Optional response text we are replying to (gets priority in context)
   * @returns {Promise<void>}
   */
  async sendMessageStream(conversationId, content, onEvent, provider = null, skipStages = false, replyToResponse = null) {
    const body = { content };
    if (provider) body.provider = provider;
    if (skipStages) body.skip_stages = skipStages;
    if (replyToResponse) body.reply_to_response = replyToResponse;

    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message/stream`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to send message');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();

      for (const part of parts) {
        const lines = part.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            try {
              const event = JSON.parse(data);
              onEvent(event.type, event);
            } catch (e) {
              console.error('Failed to parse SSE event:', e);
            }
          }
        }
      }
    }
  },

  /**
   * Retry the last pending/failed user message and stream SSE events.
   * @param {string} conversationId
   * @param {function} onEvent - (eventType, event) => void
   * @param {string} provider
   * @param {boolean} skipStages
   */
  async retryPendingStream(conversationId, onEvent, provider = null, skipStages = false) {
    const body = {};
    if (provider) body.provider = provider;
    if (skipStages) body.skip_stages = skipStages;

    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/pending/retry/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!response.ok) throw new Error('Failed to start retry stream');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        const lines = part.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (onEvent) onEvent(data.type, data);
            } catch (e) {
              console.error('Failed to parse retry SSE', e);
            }
          }
        }
      }
    }
  },

  /**
   * Remove pending user messages for a conversation. Body: {keep_last: true}
   */
  async removePendingMessages(conversationId, keepLast = true) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/pending/remove`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep_last: keepLast }),
    });
    if (!response.ok) throw new Error('Failed to remove pending messages');
    return response.json();
  },

  /**
   * Mark the last user message status (e.g., 'failed', 'complete')
   */
  async markUserMessageStatus(conversationId, status) {
    const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/user-message/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    if (!response.ok) throw new Error('Failed to mark user message status');
    return response.json();
  },

  async listAvailableModels(provider = 'ollama') {
    const response = await fetch(`${API_BASE}/api/available-models?provider=${provider}`);
    if (!response.ok) throw new Error('Failed to list available models');
    return response.json();
  },

  async getCouncilConfig() {
    const response = await fetch(`${API_BASE}/api/council-config`);
    if (!response.ok) throw new Error('Failed to get council config');
    return response.json();
  },

  async setCouncilConfig(config) {
    const response = await fetch(`${API_BASE}/api/council-config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    if (!response.ok) throw new Error('Failed to update council config');
    return response.json();
  },

  async installOllamaModel(model) {
    const response = await fetch(`${API_BASE}/api/ollama/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    if (!response.ok) throw new Error('Failed to install model');
    return response.json();
  },

  async installOllamaModelStream(model, onEvent) {
    const response = await fetch(`${API_BASE}/api/ollama/install/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });

    if (!response.ok) {
      throw new Error('Failed to start install');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        const lines = part.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (onEvent) onEvent(data.type, data);
            } catch (e) {
              console.error('Failed to parse install SSE', e);
            }
          }
        }
      }
    }
  },
  async uninstallOllamaModel(model) {
    const response = await fetch(`${API_BASE}/api/ollama/uninstall`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    if (!response.ok) throw new Error('Failed to uninstall model');
    return response.json();
  },
  async uninstallOllamaModelStream(model, onEvent) {
    const response = await fetch(`${API_BASE}/api/ollama/uninstall/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    if (!response.ok) throw new Error('Failed to start uninstall');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        const lines = part.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (onEvent) onEvent(data.type, data);
            } catch (e) {
              console.error('Failed to parse uninstall SSE', e);
            }
          }
        }
      }
    }
  },
  async registrySearch(query) {
    const response = await fetch(`${API_BASE}/api/ollama/registry?query=${encodeURIComponent(query)}`);
    if (!response.ok) throw new Error('Failed to search registry');
    return response.json();
  },

  // OpenRouter configuration APIs
  async getOpenRouterConfig() {
    const response = await fetch(`${API_BASE}/api/openrouter/config`);
    if (!response.ok) throw new Error('Failed to get OpenRouter config');
    return response.json();
  },

  async setOpenRouterConfig(config) {
    const response = await fetch(`${API_BASE}/api/openrouter/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    if (!response.ok) throw new Error('Failed to update OpenRouter config');
    return response.json();
  },

  async validateOpenRouterKey(apiKey = null) {
    const params = new URLSearchParams();
    if (apiKey) params.append('api_key', apiKey);
    const response = await fetch(`${API_BASE}/api/openrouter/validate?${params.toString()}`);
    if (!response.ok) throw new Error('Failed to validate OpenRouter key');
    return response.json();
  },

  // Custom API configuration APIs
  async getCustomApiConfig() {
    const response = await fetch(`${API_BASE}/api/custom-api/config`);
    if (!response.ok) throw new Error('Failed to get Custom API config');
    return response.json();
  },

  async setCustomApiConfig(config) {
    const response = await fetch(`${API_BASE}/api/custom-api/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    if (!response.ok) throw new Error('Failed to update Custom API config');
    return response.json();
  },

  async validateCustomApi(apiUrl = null, apiKey = null) {
    const params = new URLSearchParams();
    if (apiUrl) params.append('api_url', apiUrl);
    if (apiKey) params.append('api_key', apiKey);
    const response = await fetch(`${API_BASE}/api/custom-api/validate?${params.toString()}`);
    if (!response.ok) throw new Error('Failed to validate Custom API');
    return response.json();
  },
};
