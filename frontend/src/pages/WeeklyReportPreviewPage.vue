<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api/client";

const route = useRoute();
const weekId = computed(() => String(route.query.week_id || "").trim());
const agentId = computed(() => String(route.query.agent_id || "").trim());

const pdfViewUrl = computed(() => {
  if (!weekId.value || !agentId.value) return "";
  return api.getWeeklyReportPdfUrl(weekId.value, agentId.value, { inline: true });
});

const loadError = computed(() => {
  if (!weekId.value || !agentId.value) return "Missing week_id or agent_id in the URL.";
  return "";
});

function downloadPdf() {
  if (!weekId.value || !agentId.value) return;
  const link = document.createElement("a");
  link.href = api.getWeeklyReportPdfUrl(weekId.value, agentId.value);
  document.body.appendChild(link);
  link.click();
  link.remove();
}
</script>

<template>
  <div v-if="loadError" class="preview-error"><p>{{ loadError }}</p></div>
  <div v-else class="preview-root">
    <iframe :src="pdfViewUrl" title="Weekly report PDF" class="pdf-frame" />
    <button type="button" class="download-btn" @click="downloadPdf">Download PDF</button>
  </div>
</template>

<style scoped>
.preview-root { position:fixed; inset:0; background:#525659; }
.pdf-frame { display:block; width:100%; height:100%; border:0; }
.download-btn {
  position:fixed; top:1rem; right:1rem; z-index:10; padding:0.55rem 0.9rem; border-radius:8px;
  border:1px solid rgba(255,255,255,0.2); background:rgba(15,23,42,0.82); color:#fff; cursor:pointer;
}
.preview-error { display:flex; align-items:center; justify-content:center; min-height:100vh; color:#64748b; }
</style>
