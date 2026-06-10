const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }

  return response.json();
}

export const api = {
  listConversations: () => request("/api/conversations"),
  createConversation: (payload) =>
    request("/api/conversations", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getConversation: (id) => request(`/api/conversations/${id}`),
  getStats: (id) => request(`/api/conversations/${id}/stats`),
  addMessage: (id, payload) =>
    request(`/api/conversations/${id}/messages`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
