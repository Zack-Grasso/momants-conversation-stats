<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api, CacheUnavailableError } from "../api/client";
import SentimentSparkline from "../components/SentimentSparkline.vue";
import { useAgent } from "../composables/useAgent";
import { useAgents } from "../composables/useAgents";
import { formatDuration } from "../composables/useJobWebSocket";

const router = useRouter();
const { agentId, hasAgentId } = useAgent();
const { agents, loading: agentsLoading, error: agentsError, loadAgents, agentName } = useAgents();
const selectedAgentName = computed(() => agentName(agentId.value) || "this agent");

const conversations = ref([]);
const selectedId = ref(null);
const selectedConversation = ref(null);
const selectedTimeline = ref(null);
const stats = ref(null);
const overview = ref(null);
const questionClusters = ref([]);
const unansweredQuestions = ref([]);
const listLoading = ref(false);
const actionLoading = ref(false);
const error = ref("");
const cacheWarning = ref("");
const success = ref("");
const reviewSample = ref([]);
const reviewCount = 5;
const eventName = ref("");
const reportMissing = ref([]);
const chartsGenerated = ref(true);
const chartSource = ref("local");

function clearResultsState() {
  overview.value = null;
  questionClusters.value = [];
  unansweredQuestions.value = [];
  conversations.value = [];
  selectedId.value = null;
  selectedConversation.value = null;
  selectedTimeline.value = null;
  stats.value = null;
  reviewSample.value = [];
}

function formatDeletedCounts(deleted) {
  if (!deleted) return "";
  return Object.entries(deleted)
    .filter(([, count]) => count > 0)
    .map(([key, count]) => `${key.replace(/_/g, " ")}: ${count}`)
    .join(", ");
}

const depthBars = computed(() => {
  const dist = overview.value?.depth_distribution;
  if (!dist) return [];
  const total = dist.shallow + dist.medium + dist.deep || 1;
  return [
    { label: "Shallow", value: dist.shallow, pct: Math.round((100 * dist.shallow) / total) },
    { label: "Medium", value: dist.medium, pct: Math.round((100 * dist.medium) / total) },
    { label: "Deep", value: dist.deep, pct: Math.round((100 * dist.deep) / total) },
  ];
});

function starDisplay(count) {
  const filled = Math.round(count);
  return "★".repeat(filled) + "☆".repeat(5 - filled);
}

function trajectoryLabel(value) {
  const labels = {
    improving: "Improving ↑",
    worsening: "Worsening ↓",
    stable_positive: "Stable positive",
    stable_negative: "Stable negative",
    stable_neutral: "Stable neutral",
    mixed: "Mixed",
  };
  return labels[value] || value || "—";
}

function unansweredBadge(status) {
  const labels = {
    no_reply: "Unanswered (no reply)",
    weak_answer: "Weak answer",
    not_answered: "Not answered",
  };
  return labels[status] || status;
}

function memberMessages(conversation) {
  return conversation.messages.filter((message) => message.role === "member");
}

function handleCacheError(err) {
  if (err instanceof CacheUnavailableError) {
    cacheWarning.value =
      "Dashboard data unavailable — waiting for pipeline refresh. Check the Run page for pipeline status.";
    return true;
  }
  error.value = err.message;
  return false;
}

async function loadConversations() {
  listLoading.value = true;
  try {
    const id = agentId.value.trim();
    if (!id) {
      conversations.value = [];
      return;
    }
    conversations.value = await api.listConversations(id);
    if (selectedId.value && !conversations.value.some((item) => item.id === selectedId.value)) {
      selectedId.value = null;
      selectedConversation.value = null;
      selectedTimeline.value = null;
      stats.value = null;
    }
  } catch (err) {
    if (!handleCacheError(err)) {
      conversations.value = [];
    }
  } finally {
    listLoading.value = false;
  }
}

async function loadInsightsData() {
  const id = agentId.value.trim();
  if (!id) {
    overview.value = null;
    questionClusters.value = [];
    unansweredQuestions.value = [];
    return;
  }
  try {
    [overview.value, questionClusters.value, unansweredQuestions.value] = await Promise.all([
      api.getInsightsOverview(id),
      api.getInsightsQuestions(id),
      api.getInsightsUnanswered(id),
    ]);
  } catch (err) {
    if (handleCacheError(err)) {
      overview.value = null;
      questionClusters.value = [];
      unansweredQuestions.value = [];
    }
  }
}

async function loadAll() {
  error.value = "";
  cacheWarning.value = "";
  await Promise.all([loadConversations(), loadInsightsData()]);
}

async function selectConversation(id) {
  selectedId.value = id;
  try {
    selectedConversation.value = await api.getConversation(id);
    stats.value = await api.getStats(id);
    selectedTimeline.value = await api.getTimeline(id);
  } catch (err) {
    if (handleCacheError(err)) {
      selectedConversation.value = null;
      stats.value = null;
      selectedTimeline.value = null;
    }
  }
}

async function deleteSelectedConversation() {
  if (!selectedId.value || !confirm("Delete this conversation and all its messages?")) return;
  actionLoading.value = true;
  try {
    await api.deleteConversation(selectedId.value);
    selectedId.value = null;
    selectedConversation.value = null;
    selectedTimeline.value = null;
    stats.value = null;
    await loadAll();
  } catch (err) {
    error.value = err.message;
  } finally {
    actionLoading.value = false;
  }
}

async function deleteAllAgentData() {
  const id = agentId.value.trim();
  if (
    !id ||
    !confirm(
      `Delete ALL data for ${selectedAgentName.value}?\n\nThis removes conversations, top questions, insights, metrics, unanswered questions, and job history. This cannot be undone.`,
    )
  ) {
    return;
  }
  actionLoading.value = true;
  error.value = "";
  success.value = "";
  try {
    const result = await api.deleteAllAgentData(id);
    clearResultsState();
    success.value = `Deleted all agent data. ${formatDeletedCounts(result.deleted)}`;
  } catch (err) {
    error.value = err.message;
  } finally {
    actionLoading.value = false;
  }
}

async function deleteInsightsOnly() {
  const id = agentId.value.trim();
  if (
    !id ||
    !confirm(
      `Remove insights for ${selectedAgentName.value}?\n\nDeletes top questions, metrics, unanswered analysis, and job history. Conversations are kept.`,
    )
  ) {
    return;
  }
  actionLoading.value = true;
  error.value = "";
  success.value = "";
  overview.value = null;
  questionClusters.value = [];
  unansweredQuestions.value = [];
  try {
    const result = await api.deleteInsightsForAgent(id);
    await loadInsightsData();
    success.value = `Insights removed. ${formatDeletedCounts(result.deleted)}`;
  } catch (err) {
    error.value = err.message;
  } finally {
    actionLoading.value = false;
  }
}

async function loadReviewSample() {
  actionLoading.value = true;
  error.value = "";
  reviewSample.value = [];
  try {
    const id = agentId.value.trim() || null;
    reviewSample.value = await api.getReviewSample(reviewCount, id);
    if (!reviewSample.value.length) {
      error.value = id ? "No conversations to review for this agent." : "No conversations to review.";
    }
  } catch (err) {
    error.value = err.message;
  } finally {
    actionLoading.value = false;
  }
}

async function previewReport() {
  const id = agentId.value.trim();
  if (!id) return;
  actionLoading.value = true;
  error.value = "";
  reportMissing.value = [];
  chartsGenerated.value = true;
  chartSource.value = "local";
  try {
    const label = eventName.value.trim() || null;
    const context = await api.getReportContext(id, label);
    reportMissing.value = context.missing || [];
    chartsGenerated.value = context.charts_generated ?? false;
    chartSource.value = context.chart_source || "local";
    window.open(api.getReportPreviewUrl(id, label), "_blank", "noopener,noreferrer");
  } catch (err) {
    error.value = err.message;
  } finally {
    actionLoading.value = false;
  }
}

async function downloadReportPdf() {
  const id = agentId.value.trim();
  if (!id) return;
  actionLoading.value = true;
  error.value = "";
  reportMissing.value = [];
  chartsGenerated.value = true;
  chartSource.value = "local";
  try {
    const label = eventName.value.trim() || null;
    const context = await api.getReportContext(id, label);
    reportMissing.value = context.missing || [];
    chartsGenerated.value = context.charts_generated ?? false;
    chartSource.value = context.chart_source || "local";
    // Content-Disposition: attachment makes the browser download the PDF; navigating a
    // hidden anchor keeps the current page and sends the auth cookie.
    const link = document.createElement("a");
    link.href = api.getReportPdfUrl(id, label);
    document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (err) {
    error.value = err.message;
  } finally {
    actionLoading.value = false;
  }
}

watch(agentId, () => loadAll());

onMounted(() => {
  loadAgents();
  loadAll();
});
</script>

<template>
  <div>
    <section class="panel agent-panel">
      <div class="panel-header">
        <div>
          <h2>Agent results</h2>
          <p class="hint">Select an agent to view conversations and insights.</p>
        </div>
        <button type="button" class="link small" :disabled="agentsLoading" @click="loadAgents(true)">
          Refresh list
        </button>
      </div>
      <select v-model="agentId" class="agent-input" :disabled="agentsLoading">
        <option value="" disabled>{{ agentsLoading ? "Loading agents…" : "Select an agent" }}</option>
        <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option>
      </select>
      <p v-if="agentsError" class="hint warn">Could not load agents: {{ agentsError }}</p>
      <div v-if="hasAgentId" class="button-row">
        <input
          v-model="eventName"
          placeholder="Override event name (defaults to agent name)"
          class="event-name-input"
          title="Leave empty to use the agent name from Momants Studio"
        />
        <button type="button" :disabled="actionLoading || listLoading" @click="loadAll">Refresh</button>
        <button type="button" :disabled="actionLoading" @click="previewReport">
          Preview report
        </button>
        <button type="button" :disabled="actionLoading" @click="downloadReportPdf">
          Download PDF
        </button>
        <button type="button" class="danger" :disabled="actionLoading" @click="deleteAllAgentData">
          Delete all agent data
        </button>
        <button type="button" class="danger-outline" :disabled="actionLoading" @click="deleteInsightsOnly">
          Remove insights only
        </button>
      </div>
      <p v-if="reportMissing.length || !chartsGenerated" class="report-gaps hint">
        <span v-if="reportMissing.length">
          Report missing data: {{ reportMissing.join(", ") }}.
        </span>
        <span v-if="!chartsGenerated">
          Charts could not be generated from available data.
        </span>
        <span v-if="chartsGenerated && chartSource !== 'local'">
          Charts use Momants stats fallback ({{ chartSource }}).
        </span>
      </p>
      <p v-if="success" class="success">{{ success }}</p>
    </section>

    <p v-if="!hasAgentId" class="panel hint warn">Select an agent to see results.</p>

    <p v-if="cacheWarning" class="panel warn-banner">{{ cacheWarning }}</p>

    <template v-else-if="hasAgentId && !cacheWarning">
      <section v-if="overview && overview.conversation_count" class="panel">
        <h2>Overview</h2>
        <div class="stats overview-grid">
          <span>Conversations: {{ overview.conversation_count }}</span>
          <span v-if="overview.average_stars !== null">
            Avg stars: {{ starDisplay(overview.average_stars) }} ({{ overview.average_stars.toFixed(1) }})
          </span>
          <span>Improving: {{ overview.improving_pct }}%</span>
          <span>Worsening: {{ overview.worsening_pct }}%</span>
          <span v-if="overview.median_response_seconds !== null">
            Median response: {{ formatDuration(overview.median_response_seconds) }}
          </span>
          <span v-if="overview.sla_met_pct !== null">SLA met: {{ overview.sla_met_pct.toFixed(0) }}%</span>
          <span>Unanswered: {{ overview.unanswered_pct.toFixed(1) }}%</span>
        </div>
        <div class="bar-chart">
          <div v-for="bar in depthBars" :key="bar.label" class="bar-row">
            <span class="bar-label">{{ bar.label }} ({{ bar.value }})</span>
            <div class="bar-track"><div class="bar-fill" :style="{ width: `${bar.pct}%` }" /></div>
          </div>
        </div>
        <div v-if="Object.keys(overview.intent_breakdown).length" class="intent-list">
          <strong>Intents:</strong>
          <span v-for="(count, intent) in overview.intent_breakdown" :key="intent" class="intent-chip">
            {{ intent }} ({{ count }})
          </span>
        </div>
      </section>

      <section v-else-if="!listLoading && overview" class="panel">
        <p class="hint">No insights data in cache for this agent yet.</p>
        <button type="button" class="link small" @click="router.push('/')">Go to Run</button>
      </section>

      <section v-if="questionClusters.length" class="panel">
        <div class="panel-header">
          <h2>Top questions</h2>
          <button type="button" class="danger-outline small" :disabled="actionLoading" @click="deleteInsightsOnly">
            Clear
          </button>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Count</th>
              <th>Intent</th>
              <th>Question</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="cluster in questionClusters" :key="cluster.id">
              <td>{{ cluster.rank }}</td>
              <td>{{ cluster.count }}</td>
              <td>{{ cluster.intent_label || "—" }}</td>
              <td>{{ cluster.representative_text }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="unansweredQuestions.length" class="panel">
        <h2>Unanswered questions</h2>
        <table class="data-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Question</th>
              <th>Agent reply</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in unansweredQuestions" :key="item.id">
              <td>{{ unansweredBadge(item.status) }}</td>
              <td>{{ item.question_text }}</td>
              <td>{{ item.agent_reply_text || "—" }}</td>
              <td>
                <button type="button" class="link small" @click="selectConversation(item.conversation_id)">
                  Open
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="panel actions-panel">
        <div class="panel-header">
          <h2>Review</h2>
          <button type="button" :disabled="actionLoading" @click="loadReviewSample">
            Random review ({{ reviewCount }})
          </button>
        </div>
      </section>

      <section v-if="reviewSample.length" class="panel review-panel">
        <article v-for="conversation in reviewSample" :key="conversation.id" class="review-card">
          <header class="review-card-header">
            <strong>{{ conversation.title }}</strong>
            <button type="button" class="link small" @click="selectConversation(conversation.id)">Open</button>
          </header>
          <div v-for="message in memberMessages(conversation)" :key="message.id" class="message review-message">
            <header>
              <span v-if="message.sentiment" class="badge" :class="message.sentiment.label.toLowerCase()">
                {{ starDisplay(message.sentiment.stars) }} {{ message.sentiment.label }}
              </span>
            </header>
            <p>{{ message.content }}</p>
          </div>
        </article>
      </section>

      <section class="grid">
        <div class="panel">
          <div class="panel-header">
            <h2>
              Conversations
              <span class="filter-hint">({{ conversations.length }})</span>
            </h2>
          </div>
          <p v-if="listLoading" class="hint">Loading…</p>
          <ul v-else class="list">
            <li v-for="item in conversations" :key="item.id">
              <button class="link" @click="selectConversation(item.id)">{{ item.title }}</button>
            </li>
          </ul>
          <p v-if="!listLoading && !conversations.length" class="empty">No conversations for this agent.</p>
        </div>

        <div class="panel" v-if="selectedConversation">
          <div class="panel-header">
            <div>
              <h2>{{ selectedConversation.title }}</h2>
              <p v-if="selectedTimeline" class="meta">
                <span v-if="selectedTimeline.trajectory" class="trajectory-badge">
                  {{ trajectoryLabel(selectedTimeline.trajectory) }}
                </span>
                <span v-if="selectedTimeline.intent_label">Intent: {{ selectedTimeline.intent_label }}</span>
              </p>
            </div>
            <button type="button" class="danger" :disabled="actionLoading" @click="deleteSelectedConversation">
              Delete
            </button>
          </div>
          <SentimentSparkline v-if="selectedTimeline?.timeline?.length" :points="selectedTimeline.timeline" />
          <div v-if="stats" class="stats">
            <span>Messages: {{ stats.message_count }}</span>
            <span v-if="stats.average_stars !== null">
              Avg: {{ starDisplay(stats.average_stars) }}
            </span>
          </div>
          <article
            v-for="message in selectedTimeline?.messages || selectedConversation.messages"
            :key="message.message_id || message.id"
            class="message"
          >
            <header>
              <strong>{{ message.role }}</strong>
              <span v-if="message.sentiment" class="badge" :class="message.sentiment.label.toLowerCase()">
                {{ starDisplay(message.sentiment.stars) }} {{ message.sentiment.label }}
              </span>
              <span v-if="message.unanswered_status" class="badge unanswered">
                {{ unansweredBadge(message.unanswered_status) }}
              </span>
            </header>
            <p>{{ message.content }}</p>
          </article>
        </div>
      </section>
    </template>

    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
.agent-input { width: 100%; max-width: 420px; }
.event-name-input { min-width: 180px; max-width: 260px; }
.agent-panel h2 { margin-top: 0; }
.button-row { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.75rem; }
.success { color: #166534; margin-top: 0.75rem; }
.warn-banner {
  background: #fffbeb;
  border: 1px solid #fcd34d;
  color: #92400e;
  padding: 1rem;
  border-radius: 8px;
}
.danger-outline {
  background: #fff;
  color: #b91c1c;
  border-color: #fca5a5;
}
.danger-outline.small { padding: 0.35rem 0.6rem; font-size: 0.85rem; }
.report-gaps { margin: 0.75rem 0 0; color: #92400e; }
</style>
