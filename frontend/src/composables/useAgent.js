import { computed, ref, watch } from "vue";

const STORAGE_KEY = "momants_agent_id";
const agentId = ref(localStorage.getItem(STORAGE_KEY) || "");

watch(agentId, (value) => {
  const trimmed = value?.trim() || "";
  if (trimmed) {
    localStorage.setItem(STORAGE_KEY, trimmed);
  }
});

export function useAgent() {
  const hasAgentId = computed(() => Boolean(agentId.value.trim()));

  function setAgentId(value) {
    agentId.value = value ?? "";
  }

  return { agentId, hasAgentId, setAgentId };
}
