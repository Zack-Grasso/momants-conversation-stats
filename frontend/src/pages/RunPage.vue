<script setup>
import { onMounted } from "vue";
import { useAgent } from "../composables/useAgent";
import { useAgents } from "../composables/useAgents";
import { usePipelineRunner } from "../composables/usePipelineRunner";

const { agentId, hasAgentId } = useAgent();
const { agents, loading: agentsLoading, error: agentsError, loadAgents } = useAgents();
const {
  healthStatus,
  runningIngestJobs,
  runningSentimentJobs,
  runningInsightsJobs,
  runningIntentJobs,
  jobLimits,
  adminBusy,
  logs,
  cacheReady,
  pipelineRunning,
  pipelineStage,
  hasLiveProgress,
  pipelineStarting,
  reanalyzing,
  labelingReferredIntents,
  error,
  success,
  lastUpdated,
  pipelineElapsedSeconds,
  cacheWarm,
  jobElapsed,
  ingestEta,
  sentimentEta,
  insightsEta,
  ingestProgress,
  sentimentProgress,
  insightsProgress,
  intentProgress,
  intentEta,
  formatDuration,
  formatLogTime,
  phaseLabel,
  jobStatusClass,
  loadStatus,
  startPipeline,
  reanalyze,
  labelReferredIntents,
  purgeSystem,
} = usePipelineRunner(agentId);

function healthClass(value) {
  if (value === "ok") return "ok";
  if (value === "error" || value === "unavailable" || value === "degraded") return "bad";
  return "";
}

function formatTimestamp(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

const stageLabels = {
  ingest: "Ingesting conversations",
  sentiment: "Analyzing sentiment",
  insights: "Running insights",
  intents: "Labeling doorverwezen intents",
  warming: "Warming cache",
  ready: "Ready",
  idle: "Idle",
};

onMounted(() => {
  loadAgents();
  loadStatus();
});
</script>

<template>
  <div>
    <section class="panel agent-panel">
      <h2>Start analysis</h2>
      <p class="hint">Select an agent and start the analysis. You'll get Slack updates as each stage completes.</p>
      <select v-model="agentId" class="agent-input" :disabled="agentsLoading">
        <option value="" disabled>{{ agentsLoading ? "Loading agents…" : "Select an agent" }}</option>
        <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option>
      </select>
      <button type="button" class="link small refresh-agents" :disabled="agentsLoading" @click="loadAgents(true)">
        Refresh list
      </button>
      <div class="agent-actions">
        <button type="button" :disabled="pipelineStarting || pipelineRunning || !hasAgentId" @click="startPipeline">
          {{ pipelineStarting ? "Starting…" : pipelineRunning ? "Analysis running…" : "Start analysis" }}
        </button>
        <button
          type="button"
          class="secondary"
          :disabled="reanalyzing || pipelineStarting || pipelineRunning || !hasAgentId"
          @click="reanalyze"
        >
          {{ reanalyzing ? "Starting…" : "Reanalyze (sentiment + insights)" }}
        </button>
        <button
          type="button"
          class="secondary"
          :disabled="labelingReferredIntents || pipelineRunning || !hasAgentId"
          @click="labelReferredIntents(false)"
        >
          {{ labelingReferredIntents ? "Starting…" : "Label doorverwezen intents" }}
        </button>
      </div>
      <p class="hint">
        Reanalyze skips ingest and re-runs sentiment + insights over conversations already stored for this agent.
        Doorverwezen intent labeling classifies only referred conversations (where the agent shared an e-mail).
      </p>
      <p v-if="agentsError" class="hint warn">Could not load agents: {{ agentsError }}</p>
      <p v-else-if="!hasAgentId" class="hint warn">Select an agent to begin.</p>
      <p v-if="success" class="success">{{ success }}</p>
    </section>

    <section class="panel health-panel">
      <div class="panel-header">
        <h2>Pipeline status</h2>
        <button type="button" class="link small" @click="loadStatus">Refresh</button>
      </div>
      <div v-if="healthStatus" class="health-grid">
        <span class="health-item" :class="healthClass(healthStatus.status)">
          Overall: {{ healthStatus.status }}
        </span>
        <span class="health-item" :class="healthClass(healthStatus.database)">
          Database: {{ healthStatus.database }}
        </span>
        <span class="health-item" :class="healthClass(healthStatus.redis)">
          Redis: {{ healthStatus.redis }}
        </span>
        <span class="health-item" :class="healthClass(cacheReady ? 'ok' : 'degraded')">
          Cache ready: {{ cacheReady ? "yes" : "no" }}
        </span>
        <span v-if="pipelineRunning" class="health-item running">
          Stage: {{ stageLabels[pipelineStage] || pipelineStage }}
        </span>
      </div>
      <p v-if="hasAgentId" class="hint last-updated">
        Last successful update: {{ formatTimestamp(lastUpdated) }}
      </p>
    </section>

    <section v-if="hasAgentId && hasLiveProgress" class="panel">
      <div class="panel-header">
        <h2>Live progress</h2>
        <span v-if="pipelineRunning" class="metric-value pipeline-elapsed">
          Elapsed: {{ formatDuration(pipelineElapsedSeconds) }}
        </span>
      </div>

      <p v-if="jobLimits" class="hint job-count-hint">
        Total {{ jobLimits.globalRunning }} / {{ jobLimits.globalLimit }}
        · Ingest {{ jobLimits.ingestRunning }} / {{ jobLimits.ingestLimit }}
        · Sentiment {{ jobLimits.sentimentRunning }} / {{ jobLimits.sentimentLimit }}
        · Insights {{ jobLimits.insightsRunning }} / {{ jobLimits.insightsLimit }}
        · Intents {{ jobLimits.intentRunning }} / {{ jobLimits.intentLimit }}
        <span v-if="hasAgentId"> · This agent: {{ jobLimits.agentRunning }} running</span>
      </p>

      <div
        v-for="ingestJob in runningIngestJobs"
        :key="`ingest-${ingestJob.id}`"
        class="job-card"
        :class="jobStatusClass(ingestJob.status)"
      >
        <div class="job-card-header">
          <strong>Ingest #{{ ingestJob.id }}</strong>
          <span class="status-pill" :class="jobStatusClass(ingestJob.status)">{{ ingestJob.status }}</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: `${ingestProgress(ingestJob)}%` }" />
        </div>
        <p class="job-detail">Fetching conversations from Momants API</p>
        <div class="job-metrics">
          <div class="metric">
            <span class="metric-label">Progress</span>
            <span class="metric-value">{{ ingestJob.processed }} / {{ ingestJob.limit ?? "?" }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Elapsed</span>
            <span class="metric-value">{{ formatDuration(jobElapsed(ingestJob)) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">ETA</span>
            <span class="metric-value">
              {{ ingestEta(ingestJob) !== null ? `~${formatDuration(ingestEta(ingestJob))}` : "Estimating…" }}
            </span>
          </div>
          <div v-if="ingestJob.messages_analyzed" class="metric">
            <span class="metric-label">Messages</span>
            <span class="metric-value">{{ ingestJob.messages_analyzed }}</span>
          </div>
          <div v-if="ingestJob.failed" class="metric">
            <span class="metric-label">Failed</span>
            <span class="metric-value">{{ ingestJob.failed }}</span>
          </div>
        </div>
        <p v-if="ingestJob.error && ingestJob.status !== 'running'" class="error">{{ ingestJob.error }}</p>
      </div>

      <div
        v-for="sentimentJob in runningSentimentJobs"
        :key="`sentiment-${sentimentJob.id}`"
        class="job-card"
        :class="jobStatusClass(sentimentJob.status)"
      >
        <div class="job-card-header">
          <strong>Sentiment #{{ sentimentJob.id }}<span v-if="sentimentJob.reanalyze"> (reanalyze)</span></strong>
          <span class="status-pill" :class="jobStatusClass(sentimentJob.status)">{{ sentimentJob.status }}</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill sentiment" :style="{ width: `${sentimentProgress(sentimentJob)}%` }" />
        </div>
        <p class="job-detail">
          Language detection → translation → polarity + emotion
          <span v-if="sentimentJob.phase_detail"> — {{ sentimentJob.phase_detail }}</span>
        </p>
        <div class="job-metrics">
          <div class="metric">
            <span class="metric-label">Progress</span>
            <span class="metric-value">{{ sentimentJob.processed }} / {{ sentimentJob.limit ?? "?" }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Elapsed</span>
            <span class="metric-value">{{ formatDuration(jobElapsed(sentimentJob)) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">ETA</span>
            <span class="metric-value">
              {{ sentimentEta(sentimentJob) !== null ? `~${formatDuration(sentimentEta(sentimentJob))}` : "Estimating…" }}
            </span>
          </div>
          <div v-if="sentimentJob.messages_analyzed" class="metric">
            <span class="metric-label">Analyzed</span>
            <span class="metric-value">{{ sentimentJob.messages_analyzed }}</span>
          </div>
          <div v-if="sentimentJob.failed" class="metric">
            <span class="metric-label">Failed</span>
            <span class="metric-value">{{ sentimentJob.failed }}</span>
          </div>
        </div>
        <p v-if="sentimentJob.error && sentimentJob.status !== 'running'" class="error">{{ sentimentJob.error }}</p>
      </div>

      <div
        v-for="insightsJob in runningInsightsJobs"
        :key="`insights-${insightsJob.id}`"
        class="job-card"
        :class="jobStatusClass(insightsJob.status)"
      >
        <div class="job-card-header">
          <strong>Insights #{{ insightsJob.id }}</strong>
          <span class="status-pill" :class="jobStatusClass(insightsJob.status)">{{ insightsJob.status }}</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill insights" :style="{ width: `${insightsProgress(insightsJob)}%` }" />
        </div>
        <p class="job-detail">
          <span class="phase-name">{{ phaseLabel(insightsJob.phase) }}</span>
          <span v-if="insightsJob.phase_detail"> — {{ insightsJob.phase_detail }}</span>
        </p>
        <div class="job-metrics">
          <div class="metric">
            <span class="metric-label">Phase progress</span>
            <span class="metric-value">
              <template v-if="insightsJob.phase_total > 0">
                {{ insightsJob.phase_progress }} / {{ insightsJob.phase_total }}
              </template>
              <template v-else-if="insightsJob.phase === 'metrics'">
                {{ insightsJob.processed }} / {{ insightsJob.limit ?? "?" }}
              </template>
              <template v-else>—</template>
            </span>
          </div>
          <div class="metric">
            <span class="metric-label">Elapsed</span>
            <span class="metric-value">{{ formatDuration(jobElapsed(insightsJob)) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">ETA</span>
            <span class="metric-value">
              {{ insightsEta(insightsJob) !== null ? `~${formatDuration(insightsEta(insightsJob))}` : "Estimating…" }}
            </span>
          </div>
        </div>
        <p v-if="insightsJob.error" class="error">{{ insightsJob.error }}</p>
      </div>

      <div
        v-for="intentJob in runningIntentJobs"
        :key="`intent-${intentJob.id}`"
        class="job-card"
        :class="jobStatusClass(intentJob.status)"
      >
        <div class="job-card-header">
          <strong>Doorverwezen intents #{{ intentJob.id }}</strong>
          <span class="status-pill" :class="jobStatusClass(intentJob.status)">{{ intentJob.status }}</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill intent" :style="{ width: `${intentProgress(intentJob)}%` }" />
        </div>
        <p class="job-detail">
          <span class="phase-name">{{ phaseLabel(intentJob.phase) }}</span>
          <span v-if="intentJob.phase_detail"> — {{ intentJob.phase_detail }}</span>
        </p>
        <div class="job-metrics">
          <div class="metric">
            <span class="metric-label">Progress</span>
            <span class="metric-value">{{ intentJob.processed }} / {{ intentJob.limit ?? "?" }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Elapsed</span>
            <span class="metric-value">{{ formatDuration(jobElapsed(intentJob)) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">ETA</span>
            <span class="metric-value">
              {{ intentEta(intentJob) !== null ? `~${formatDuration(intentEta(intentJob))}` : "Estimating…" }}
            </span>
          </div>
        </div>
        <p v-if="intentJob.error" class="error">{{ intentJob.error }}</p>
      </div>

      <div v-if="pipelineStage === 'warming'" class="job-card running">
        <div class="job-card-header">
          <strong>Cache warm</strong>
          <span class="status-pill running">running</span>
        </div>
        <div v-if="cacheWarm" class="progress-track">
          <div class="progress-fill" :style="{ width: `${cacheWarm.pct}%` }" />
        </div>
        <p class="job-detail">Writing precomputed payloads to Redis for fast dashboard reads…</p>
        <div v-if="cacheWarm" class="job-metrics">
          <div class="metric">
            <span class="metric-label">Progress</span>
            <span class="metric-value">{{ cacheWarm.done }} / {{ cacheWarm.total }} ({{ cacheWarm.pct }}%)</span>
          </div>
          <div class="metric">
            <span class="metric-label">Elapsed</span>
            <span class="metric-value">{{ formatDuration(pipelineElapsedSeconds) }}</span>
          </div>
        </div>
      </div>

      <div v-if="cacheReady && !pipelineRunning" class="hint success-inline">
        Dashboard cache is ready — open the Results page to view data.
      </div>
    </section>

    <section v-if="hasAgentId && logs.length" class="panel log-panel">
      <h2>Activity log</h2>
      <p class="hint">Updates every 2s while the pipeline is running. Reload this page anytime to resume tracking.</p>
      <ol class="activity-log">
        <li v-for="(entry, index) in logs" :key="`${entry.category}-${index}`" class="log-entry">
          <time class="log-time">{{ formatLogTime(entry.at) }}</time>
          <span class="log-message">{{ entry.message }}</span>
        </li>
      </ol>
    </section>

    <section class="panel admin-panel">
      <h2>Danger zone</h2>
      <div class="admin-actions">
        <button type="button" class="danger" :disabled="adminBusy" @click="purgeSystem">
          {{ adminBusy ? "Working…" : "Purge everything" }}
        </button>
      </div>
      <p class="hint warn">
        Purge wipes the database, Redis cache, locks, and job queue for a clean slate.
      </p>
    </section>

    <p v-if="pipelineRunning" class="hint logs-hint">
      Stuck? Check <code>docker compose logs -f api</code>
    </p>

    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
.agent-input { width: 100%; max-width: 420px; }
.refresh-agents { margin-left: 0.5rem; }
.agent-actions { margin-top: 0.75rem; }
.agent-panel h2 { margin-top: 0; }
.last-updated { margin-top: 0.75rem; font-weight: 500; }
.success { color: #166534; margin-top: 0.75rem; }
.success-inline { color: #166534; margin-top: 0.75rem; font-weight: 500; }
.pipeline-elapsed { font-size: 0.95rem; color: #0f766e; }
.job-count-hint { margin: 0 0 0.75rem; }
.admin-panel h2 { margin-top: 0; }
.admin-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 0.75rem;
}
.health-item.running { background: #ccfbf1; color: #0f766e; border-color: #99f6e4; }
.progress-fill.sentiment { background: #8b5cf6; }
.agent-actions button.secondary {
  background: #ede9fe;
  color: #5b21b6;
  border: 1px solid #ddd6fe;
}
.agent-actions button.secondary:disabled { opacity: 0.6; }

.log-panel h2 { margin-top: 0; }
.activity-log {
  list-style: none;
  margin: 0.75rem 0 0;
  padding: 0;
  max-height: 280px;
  overflow-y: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
}
.log-entry {
  display: grid;
  grid-template-columns: 5.5rem 1fr;
  gap: 0.75rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #e2e8f0;
  font-size: 0.9rem;
}
.log-entry:last-child { border-bottom: none; }
.log-time {
  color: #64748b;
  font-variant-numeric: tabular-nums;
  font-size: 0.8rem;
}
.log-message { color: #334155; }
</style>
