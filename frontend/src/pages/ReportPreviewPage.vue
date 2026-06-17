<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/client";
import { useAgents } from "../composables/useAgents";

const route = useRoute();
const { agents, loadAgents, agentName } = useAgents();

const agentId = computed(() => String(route.query.agent_id || "").trim());
const eventName = computed(() => String(route.query.event_name || "").trim() || null);
const label = computed(() => {
  if (!agentId.value) return "";
  return agentName(agentId.value) || `Agent ${agentId.value.slice(0, 8)}`;
});
const pdfViewUrl = computed(() => {
  if (!agentId.value) return "";
  return api.getReportPdfUrl(agentId.value, eventName.value, { inline: true });
});
const pdfDownloadUrl = computed(() => {
  if (!agentId.value) return "";
  return api.getReportPdfUrl(agentId.value, eventName.value);
});

const loadError = ref("");

onMounted(async () => {
  if (!agentId.value) {
    loadError.value = "Missing agent_id in the URL.";
    return;
  }
  try {
    await loadAgents();
  } catch (err) {
    loadError.value = err.message;
  }
});

function downloadPdf() {
  if (!pdfDownloadUrl.value) return;
  const link = document.createElement("a");
  link.href = pdfDownloadUrl.value;
  document.body.appendChild(link);
  link.click();
  link.remove();
}
</script>

<template>
  <section class="preview-page">
    <div class="preview-toolbar panel">
      <div>
        <h2>Rapport preview</h2>
        <p v-if="label" class="hint">{{ label }}</p>
        <p v-if="loadError" class="error">{{ loadError }}</p>
        <p v-else-if="!agentId" class="hint warn">Select an agent via the link in Slack or Results.</p>
      </div>
      <div class="preview-actions">
        <router-link to="/results" class="link-btn">Back to results</router-link>
        <button type="button" :disabled="!agentId" @click="downloadPdf">Download PDF</button>
      </div>
    </div>

    <div v-if="agentId && !loadError" class="preview-frame panel">
      <iframe
        :src="pdfViewUrl"
        title="Rapport PDF preview"
        class="pdf-frame"
      />
    </div>
  </section>
</template>

<style scoped>
.preview-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  min-height: calc(100vh - 12rem);
}

.preview-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.preview-toolbar h2 {
  margin: 0 0 0.25rem;
}

.preview-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-shrink: 0;
}

.link-btn {
  display: inline-flex;
  align-items: center;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #f1f5f9;
  color: #0f172a;
  text-decoration: none;
}

.preview-frame {
  flex: 1;
  min-height: 70vh;
  padding: 0;
  overflow: hidden;
}

.pdf-frame {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 70vh;
  border: 0;
}
</style>
