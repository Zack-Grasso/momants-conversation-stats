const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

export class CacheUnavailableError extends Error {
  constructor(message, status = 503) {
    super(message);
    this.name = "CacheUnavailableError";
    this.status = status;
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    const detail = await response.text();
    if (response.status === 503) {
      throw new CacheUnavailableError(detail || "Dashboard data not ready", 503);
    }
    throw new Error(detail || `Request failed: ${response.status}`);
  }

  if (response.status === 204) {
    return null;
  }

  const text = await response.text();
  if (!text) {
    return null;
  }

  return JSON.parse(text);
}

export const api = {
  listConversations: (agentId) => {
    const query = `?agent_id=${encodeURIComponent(agentId)}`;
    return request(`/api/conversations${query}`);
  },
  createConversation: (payload) =>
    request("/api/conversations", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getConversation: (id) => request(`/api/conversations/${id}`),
  getTimeline: (id) => request(`/api/conversations/${id}/timeline`),
  deleteConversation: (id) =>
    request(`/api/conversations/${id}`, { method: "DELETE" }),
  deleteConversationsByAgent: (agentId) =>
    request(`/api/conversations?agent_id=${encodeURIComponent(agentId)}`, {
      method: "DELETE",
    }),
  deleteAllConversations: () =>
    request("/api/conversations/all", { method: "DELETE" }),
  getReviewSample: (count = 5, agentId = null) => {
    const params = new URLSearchParams({ count: String(count) });
    if (agentId) {
      params.set("agent_id", agentId);
    }
    return request(`/api/conversations/review/sample?${params}`);
  },
  getStats: (id) => request(`/api/conversations/${id}/stats`),
  addMessage: (id, payload) =>
    request(`/api/conversations/${id}/messages`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getLatestIngestJob: (agentId = null) => {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
    return request(`/api/ingest/latest${q}`);
  },
  getRunningJobs: (agentId = null, limit = 10) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (agentId) {
      params.set("agent_id", agentId);
    }
    return request(`/api/jobs/running?${params}`);
  },
  getLatestInsightsJob: (agentId = null) => {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
    return request(`/api/insights/jobs/latest${q}`);
  },
  getLatestSentimentJob: (agentId = null) => {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
    return request(`/api/sentiment/latest${q}`);
  },
  startPipeline: (agentId) =>
    request("/api/pipeline/run", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId }),
    }),
  reanalyze: (agentId) =>
    request("/api/pipeline/reanalyze", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId }),
    }),
  listAgents: () => request("/api/agents"),
  deleteInsightsForAgent: (agentId) =>
    request(`/api/insights?agent_id=${encodeURIComponent(agentId)}`, { method: "DELETE" }),
  deleteAllAgentData: (agentId) =>
    request(`/api/agents/${encodeURIComponent(agentId)}`, { method: "DELETE" }),
  deleteAgentConversationsOnly: (agentId) =>
    request(`/api/agents/${encodeURIComponent(agentId)}/conversations`, { method: "DELETE" }),
  getHealthStatus: (agentId = null) => {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
    return request(`/api/health/status${q}`);
  },
  getSchedulerHealth: (agentId = null) => {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
    return request(`/api/health/scheduler${q}`);
  },
  getInsightsOverview: (agentId) =>
    request(`/api/insights/overview?agent_id=${encodeURIComponent(agentId)}`),
  getInsightsQuestions: (agentId) =>
    request(`/api/insights/questions?agent_id=${encodeURIComponent(agentId)}`),
  getInsightsUnanswered: (agentId, limit = 50) =>
    request(`/api/insights/unanswered?agent_id=${encodeURIComponent(agentId)}&limit=${limit}`),
  getAuthStatus: () => request("/api/auth/status"),
  getGoogleAuthUrl: () => request("/api/auth/google/url"),
  completeGoogleAuth: (code, state) =>
    request("/api/auth/google/callback", {
      method: "POST",
      body: JSON.stringify({ code, state }),
    }),
  getAuthMe: () => request("/api/auth/me"),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  getSchedulerStatus: () => request("/api/scheduler/status"),
  stopScheduler: () => request("/api/scheduler/stop", { method: "POST" }),
  resumeScheduler: () => request("/api/scheduler/resume", { method: "POST" }),
  purgeSystem: () => request("/api/system/purge", { method: "POST" }),
  getReportContext: (agentId, eventName = null) => {
    const params = new URLSearchParams({ agent_id: agentId });
    if (eventName) {
      params.set("event_name", eventName);
    }
    return request(`/api/reports/context?${params}`);
  },
  getReportPreviewUrl: (agentId, eventName = null) => {
    const params = new URLSearchParams({ agent_id: agentId });
    if (eventName) {
      params.set("event_name", eventName);
    }
    return `${API_BASE}/api/reports/preview?${params}`;
  },
  getReportPdfUrl: (agentId, eventName = null) => {
    const params = new URLSearchParams({ agent_id: agentId });
    if (eventName) {
      params.set("event_name", eventName);
    }
    return `${API_BASE}/api/reports/pdf?${params}`;
  },
};
