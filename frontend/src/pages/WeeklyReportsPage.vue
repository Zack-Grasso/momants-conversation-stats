<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/client";
import WeeklyRunStatusDialog from "../components/WeeklyRunStatusDialog.vue";

const router = useRouter();
const runs = ref([]);
const settings = ref(null);
const selectedWeekId = ref("");
const loading = ref(false);
const settingsSaving = ref(false);
const runLoading = ref(false);
const showStatusDialog = ref(false);
const error = ref("");
const settingsError = ref("");
const runMessage = ref("");
let pollTimer = null;

const selectedRun = computed(() => runs.value.find((r) => r.week_id === selectedWeekId.value) || runs.value[0] || null);
const isRunning = computed(() => settings.value?.running === true);
const runState = computed(() => settings.value?.run_state || null);

const settingsForm = ref({
  cron: "",
  days: 7,
  enabled: true,
  agent_id: "",
});

function applySettingsToForm(data) {
  settings.value = data;
  settingsForm.value = {
    cron: data.cron,
    days: data.days,
    enabled: data.enabled,
    agent_id: data.agent_id || "",
  };
}

async function loadSettings() {
  try {
    applySettingsToForm(await api.getWeeklySettings());
  } catch (err) {
    settingsError.value = err.message || String(err);
  }
}

async function loadRuns() {
  loading.value = true;
  error.value = "";
  try {
    runs.value = await api.getWeeklyRuns();
    if (!selectedWeekId.value && runs.value.length) {
      selectedWeekId.value = runs.value[0].week_id;
    }
  } catch (err) {
    error.value = err.message || String(err);
  } finally {
    loading.value = false;
  }
}

async function refreshAll() {
  await Promise.all([loadSettings(), loadRuns()]);
}

async function saveSettings() {
  settingsSaving.value = true;
  settingsError.value = "";
  try {
    applySettingsToForm(await api.updateWeeklySettings(settingsForm.value));
    runMessage.value = "Settings saved.";
  } catch (err) {
    settingsError.value = err.message || String(err);
  } finally {
    settingsSaving.value = false;
  }
}

async function runNow() {
  runLoading.value = true;
  runMessage.value = "";
  error.value = "";
  try {
    const result = await api.runWeeklyReports();
    runMessage.value = result.message;
    showStatusDialog.value = true;
    await loadSettings();
    startPolling();
  } catch (err) {
    error.value = err.message || String(err);
  } finally {
    runLoading.value = false;
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    await loadSettings();
    if (!isRunning.value) {
      stopPolling();
      await loadRuns();
    }
  }, 2000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function openStatusDialog() {
  showStatusDialog.value = true;
}

function closeStatusDialog() {
  showStatusDialog.value = false;
}

function formatNextRun(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function preview(agentId) {
  if (!selectedRun.value) return;
  router.push({
    name: "weekly-report-preview",
    query: { week_id: selectedRun.value.week_id, agent_id: agentId },
  });
}

function downloadPdf(agentId) {
  if (!selectedRun.value) return;
  window.open(api.getWeeklyReportPdfUrl(selectedRun.value.week_id, agentId), "_blank");
}

function downloadZip() {
  if (!selectedRun.value) return;
  window.open(api.getWeeklyReportZipUrl(selectedRun.value.week_id), "_blank");
}

watch(isRunning, (running) => {
  if (running) {
    startPolling();
  }
});

watch(
  () => runState.value?.status,
  (status) => {
    if ((status === "complete" || status === "failed") && isRunning.value === false) {
      showStatusDialog.value = true;
    }
  },
);

onMounted(async () => {
  await refreshAll();
  if (isRunning.value) {
    showStatusDialog.value = true;
    startPolling();
  }
});

onUnmounted(stopPolling);
</script>

<template>
  <section class="panel">
    <WeeklyRunStatusDialog
      :open="showStatusDialog"
      :running="isRunning"
      :run-state="runState"
      @close="closeStatusDialog"
    />

    <div class="panel-header">
      <div>
        <h2>Weekly unanswered reports</h2>
        <p class="hint">Isolated weekly PDFs per agent — value metrics, top questions, and flagged answers.</p>
      </div>
      <div class="header-actions">
        <button type="button" class="primary" :disabled="runLoading || isRunning" @click="runNow">
          {{ isRunning ? "Running…" : "Run now" }}
        </button>
        <button v-if="isRunning && !showStatusDialog" type="button" @click="openStatusDialog">
          View status
        </button>
        <button type="button" :disabled="loading || isRunning" @click="refreshAll">Refresh</button>
      </div>
    </div>

    <div v-if="settings" class="settings card">
      <h3>Schedule & scope</h3>
      <div class="settings-grid">
        <label>
          Cron (UTC)
          <input v-model="settingsForm.cron" type="text" placeholder="0 6 * * 1" />
          <span class="field-hint">e.g. <code>0 6 * * 1</code> = Monday 06:00 UTC</span>
        </label>
        <label>
          Lookback days
          <input v-model.number="settingsForm.days" type="number" min="1" max="30" />
        </label>
        <label>
          Test agent ID
          <input v-model="settingsForm.agent_id" type="text" placeholder="Leave empty for all agents" />
          <span class="field-hint">When set, only this agent is processed.</span>
        </label>
        <label class="checkbox-row">
          <input v-model="settingsForm.enabled" type="checkbox" />
          Scheduler enabled
        </label>
      </div>
      <div class="settings-meta">
        <span>Next run: {{ formatNextRun(settings.next_run_at) }}</span>
        <span v-if="settings.scoped">Scoped to test agent</span>
        <button v-if="isRunning" type="button" class="linkish" @click="openStatusDialog">Run in progress — view status</button>
      </div>
      <div class="settings-actions">
        <button type="button" :disabled="settingsSaving" @click="saveSettings">Save settings</button>
      </div>
      <p v-if="settingsError" class="hint warn">{{ settingsError }}</p>
    </div>

    <p v-if="runMessage" class="hint ok">{{ runMessage }}</p>
    <p v-if="error" class="hint warn">{{ error }}</p>
    <p v-else-if="loading && !runs.length" class="hint">Loading…</p>
    <p v-else-if="!runs.length" class="hint">No weekly runs yet. Click <strong>Run now</strong> to generate the first batch.</p>

    <template v-else>
      <label class="week-select">
        Week
        <select v-model="selectedWeekId">
          <option v-for="run in runs" :key="run.week_id" :value="run.week_id">{{ run.week_id }}</option>
        </select>
      </label>

      <div v-if="selectedRun" class="summary card">
        <span>{{ selectedRun.agent_count }} agents</span>
        <span>{{ selectedRun.counts.total || 0 }} flagged</span>
        <span>{{ selectedRun.counts.no_reply || 0 }} no reply</span>
        <span>{{ selectedRun.counts.weak_answer || 0 }} weak</span>
        <span>{{ selectedRun.counts.not_answered || 0 }} not answered</span>
        <button type="button" class="primary" :disabled="!selectedRun.zip_available" @click="downloadZip">
          Download all (ZIP)
        </button>
      </div>

      <table v-if="selectedRun" class="agent-table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Top question</th>
            <th>No reply</th>
            <th>Weak</th>
            <th>Not answered</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="agent in selectedRun.agents"
            :key="agent.agent_id"
            :class="{ skipped: agent.status === 'skipped' }"
            :title="agent.status === 'skipped' ? 'Agent skipped due to no convs' : undefined"
          >
            <td>{{ agent.agent_name || agent.agent_id.slice(0, 8) }}</td>
            <td class="top-q">{{ agent.top_questions[0]?.text || "—" }}</td>
            <td>{{ agent.counts.no_reply || 0 }}</td>
            <td>{{ agent.counts.weak_answer || 0 }}</td>
            <td>{{ agent.counts.not_answered || 0 }}</td>
            <td>{{ agent.status }}</td>
            <td class="actions">
              <button type="button" :disabled="!agent.pdf_available" @click="preview(agent.agent_id)">Preview</button>
              <button type="button" :disabled="!agent.pdf_available" @click="downloadPdf(agent.agent_id)">PDF</button>
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<style scoped>
.panel { margin-top: 1rem; }
.panel-header { display:flex; justify-content:space-between; align-items:flex-start; gap:1rem; margin-bottom:1rem; }
.panel-header h2 { margin:0; }
.header-actions { display:flex; gap:0.5rem; flex-shrink:0; flex-wrap:wrap; }
.settings { padding:1rem; margin-bottom:1rem; background:#f8fafc; border-radius:8px; }
.settings h3 { margin:0 0 0.75rem; font-size:1rem; }
.settings-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:0.75rem 1rem; }
.settings-grid label { display:flex; flex-direction:column; gap:0.35rem; font-size:0.9rem; }
.settings-grid input[type="text"], .settings-grid input[type="number"] { padding:0.45rem 0.6rem; }
.checkbox-row { flex-direction:row !important; align-items:center; gap:0.5rem !important; align-self:end; }
.field-hint { font-size:0.8rem; color:#64748b; }
.settings-meta { display:flex; flex-wrap:wrap; gap:0.75rem 1.25rem; margin-top:0.75rem; font-size:0.85rem; color:#475569; align-items:center; }
.settings-actions { margin-top:0.75rem; }
.linkish { border:none; background:none; padding:0; color:#2563eb; cursor:pointer; font-size:inherit; text-decoration:underline; }
.week-select { display:flex; flex-direction:column; gap:0.35rem; margin-bottom:1rem; font-size:0.9rem; }
.week-select select { max-width:240px; padding:0.45rem 0.6rem; }
.summary { display:flex; flex-wrap:wrap; gap:0.75rem 1.25rem; align-items:center; padding:1rem; margin-bottom:1rem; background:#f8fafc; border-radius:8px; }
.summary .primary { margin-left:auto; }
.agent-table { width:100%; border-collapse:collapse; font-size:0.9rem; }
.agent-table th, .agent-table td { text-align:left; padding:0.55rem 0.5rem; border-bottom:1px solid #e2e8f0; vertical-align:top; }
.agent-table tr.skipped td { text-decoration:line-through; color:#94a3b8; }
.agent-table tr.skipped .actions button { text-decoration:none; }
.top-q { max-width:280px; font-style:italic; color:#475569; }
.actions { white-space:nowrap; display:flex; gap:0.35rem; }
.hint.ok { color:#15803d; }
</style>
