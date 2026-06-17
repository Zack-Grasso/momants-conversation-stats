<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/client";

const route = useRoute();

const agentId = computed(() => String(route.query.agent_id || "").trim());
const eventName = computed(() => String(route.query.event_name || "").trim() || null);
const pdfViewUrl = computed(() => {
  if (!agentId.value) return "";
  return api.getReportPdfUrl(agentId.value, eventName.value, { inline: true });
});
const pdfDownloadUrl = computed(() => {
  if (!agentId.value) return "";
  return api.getReportPdfUrl(agentId.value, eventName.value);
});

const loadError = computed(() => {
  if (!agentId.value) {
    return "Missing agent_id in the URL.";
  }
  return "";
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
  <div v-if="loadError" class="preview-error">
    <p>{{ loadError }}</p>
  </div>
  <div v-else class="preview-root">
    <iframe :src="pdfViewUrl" title="Rapport PDF preview" class="pdf-frame" />
    <button type="button" class="download-btn" aria-label="Download PDF" @click="downloadPdf">
      Download PDF
    </button>
  </div>
</template>

<style scoped>
.preview-root {
  position: fixed;
  inset: 0;
  background: #525659;
}

.pdf-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

.download-btn {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 10;
  padding: 0.55rem 0.9rem;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.2);
  background: rgba(15, 23, 42, 0.82);
  color: #fff;
  cursor: pointer;
  font-size: 0.9rem;
  backdrop-filter: blur(8px);
}

.download-btn:hover {
  background: rgba(15, 23, 42, 0.95);
}

.preview-error {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 2rem;
  color: #64748b;
}
</style>
