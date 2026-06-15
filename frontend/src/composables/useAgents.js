import { ref } from "vue";
import { api } from "../api/client";

// Shared across pages so the agent list is fetched once and reused.
const agents = ref([]);
const loading = ref(false);
const error = ref("");
let loaded = false;
let inflight = null;

export function useAgents() {
  async function loadAgents(force = false) {
    if (loaded && !force) return agents.value;
    if (inflight) return inflight;
    loading.value = true;
    error.value = "";
    inflight = (async () => {
      try {
        agents.value = await api.listAgents();
        loaded = true;
      } catch (err) {
        error.value = err.message;
      } finally {
        loading.value = false;
        inflight = null;
      }
      return agents.value;
    })();
    return inflight;
  }

  function agentName(id) {
    if (!id) return "";
    return agents.value.find((agent) => agent.id === id)?.name || "";
  }

  return { agents, loading, error, loadAgents, agentName };
}
