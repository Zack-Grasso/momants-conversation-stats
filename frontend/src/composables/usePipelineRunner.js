import { computed, onUnmounted, ref, watch } from "vue";
import { api } from "../api/client";
import {
  formatDuration,
  ingestEtaSeconds,
  insightsEtaSeconds,
  jobElapsedSeconds,
  jobProgressPercent,
  phaseLabel,
} from "./useJobWebSocket";

const MAX_RUNNING_JOBS = 10;

function formatLogTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString();
}

export function jobStatusClass(status) {
  if (status === "running") return "running";
  if (status === "complete") return "complete";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  return "";
}

export function usePipelineRunner(agentId) {
  const healthStatus = ref(null);
  const ingestJob = ref(null);
  const sentimentJob = ref(null);
  const insightsJob = ref(null);
  const intentJob = ref(null);
  const runningIngestJobs = ref([]);
  const runningSentimentJobs = ref([]);
  const runningInsightsJobs = ref([]);
  const runningIntentJobs = ref([]);
  const jobLimits = ref(null);
  const adminBusy = ref(false);
  const logs = ref([]);
  const pipelineStarting = ref(false);
  const reanalyzing = ref(false);
  const labelingReferredIntents = ref(false);
  const error = ref("");
  const success = ref("");

  const pipelineElapsedSeconds = ref(0);
  const elapsedTick = ref(0);

  const seenEvents = new Set();
  let pollTimer = null;
  let elapsedTimer = null;
  let trackedIngestId = null;

  const cacheReady = computed(() => healthStatus.value?.scheduler?.cache_ready === true);

  // Pipeline run-state published by the scheduler to Redis. Provides a stable start time so the
  // elapsed timer keeps advancing through every stage (incl. cache warming, which has no job to
  // anchor on) and survives page reloads, plus determinate cache-warm progress.
  const pipelineRun = computed(() => healthStatus.value?.scheduler?.run || null);
  const cacheWarm = computed(() => {
    const run = pipelineRun.value;
    const total = run?.cache_total || 0;
    if (!total) return null;
    const done = run.cache_done ?? 0;
    return { done, total, pct: Math.min(100, Math.round((done / total) * 100)) };
  });

  const ingestRunning = computed(() => runningIngestJobs.value.length > 0);
  const sentimentRunning = computed(() => runningSentimentJobs.value.length > 0);
  const insightsRunning = computed(() => runningInsightsJobs.value.length > 0);
  const intentRunning = computed(() => runningIntentJobs.value.length > 0);

  const pipelineStage = computed(() => {
    if (ingestRunning.value) return "ingest";
    if (sentimentRunning.value) return "sentiment";
    if (insightsRunning.value) return "insights";
    if (intentRunning.value) return "intents";
    if (pipelineRun.value?.stage === "intents") return "intents";
    if (pipelineRun.value?.stage === "warming") return "warming";
    if (cacheReady.value) return "ready";
    return "idle";
  });

  const pipelineRunning = computed(
    () =>
      ingestRunning.value ||
      sentimentRunning.value ||
      insightsRunning.value ||
      intentRunning.value ||
      pipelineStage.value === "warming",
  );

  const hasLiveProgress = computed(
    () =>
      runningIngestJobs.value.length > 0 ||
      runningSentimentJobs.value.length > 0 ||
      runningInsightsJobs.value.length > 0 ||
      runningIntentJobs.value.length > 0 ||
      pipelineStage.value === "warming",
  );

  const lastUpdated = computed(() => {
    const candidates = [
      ingestJob.value?.completed_at,
      sentimentJob.value?.completed_at,
      insightsJob.value?.completed_at,
    ].filter(Boolean);
    if (!candidates.length) return null;
    return candidates.reduce((latest, at) => (at > latest ? at : latest));
  });

  function jobElapsed(job) {
    void elapsedTick.value;
    return jobElapsedSeconds(job);
  }

  function ingestEta(job) {
    void elapsedTick.value;
    return ingestEtaSeconds(job);
  }

  function insightsEta(job) {
    void elapsedTick.value;
    return insightsEtaSeconds(job);
  }

  function ingestProgress(job) {
    return jobProgressPercent(job, "ingest");
  }

  function sentimentProgress(job) {
    return jobProgressPercent(job, "ingest");
  }

  function sentimentEta(job) {
    void elapsedTick.value;
    return ingestEtaSeconds(job);
  }

  function insightsProgress(job) {
    return jobProgressPercent(job, "insights");
  }

  function pushProgress(category, message) {
    const entries = logs.value;
    const last = entries[entries.length - 1];
    if (last?.category === category) {
      last.message = message;
      last.at = new Date().toISOString();
      return;
    }
    entries.push({ category, message, at: new Date().toISOString() });
  }

  function pushEvent(key, message) {
    if (seenEvents.has(key)) return;
    seenEvents.add(key);
    logs.value.push({ category: "event", message, at: new Date().toISOString() });
  }

  function resetLogsForNewRun(jobId) {
    if (trackedIngestId !== jobId) {
      trackedIngestId = jobId;
      logs.value = [];
      seenEvents.clear();
      pushEvent(`pipeline-start-${jobId}`, `Pipeline started — ingest #${jobId}`);
    }
  }

  function intentProgress(job) {
    return jobProgressPercent(job, "ingest");
  }

  function intentEta(job) {
    return ingestEtaSeconds(job);
  }

  function updateLogs() {
    const ingestJobs = runningIngestJobs.value;
    const sentimentJobs = runningSentimentJobs.value;
    const insightsJobs = runningInsightsJobs.value;
    const intentJobs = runningIntentJobs.value;
    const latestIngest = ingestJob.value;
    const latestSentiment = sentimentJob.value;
    const latestInsights = insightsJob.value;
    const latestIntent = intentJob.value;

    for (const ingest of ingestJobs) {
      pushProgress(
        `ingest-progress-${ingest.id}`,
        `Ingest #${ingest.id}: ${ingest.processed} / ${ingest.limit ?? "?"} conversations`,
      );
    }

    if (latestIngest?.status === "complete") {
      pushEvent(
        `ingest-complete-${latestIngest.id}`,
        `Ingest #${latestIngest.id} complete — ${latestIngest.processed} conversations, ${latestIngest.messages_analyzed ?? 0} messages analyzed`,
      );
    }

    if (latestIngest?.status === "failed") {
      pushEvent(
        `ingest-failed-${latestIngest.id}`,
        `Ingest #${latestIngest.id} failed: ${latestIngest.error || "unknown error"}`,
      );
    }

    for (const sentiment of sentimentJobs) {
      pushProgress(
        `sentiment-progress-${sentiment.id}`,
        `Sentiment #${sentiment.id}: ${sentiment.processed} / ${sentiment.limit ?? "?"} messages`,
      );
    }

    if (latestSentiment?.status === "complete") {
      pushEvent(
        `sentiment-complete-${latestSentiment.id}`,
        `Sentiment #${latestSentiment.id} complete — ${latestSentiment.messages_analyzed ?? 0} messages analyzed`,
      );
    }

    if (latestSentiment?.status === "failed") {
      pushEvent(
        `sentiment-failed-${latestSentiment.id}`,
        `Sentiment #${latestSentiment.id} failed: ${latestSentiment.error || "unknown error"}`,
      );
    }

    for (const insights of insightsJobs) {
      const progress =
        insights.phase_total > 0
          ? `${insights.phase_progress} / ${insights.phase_total}`
          : insights.limit > 0 && insights.phase === "metrics"
            ? `${insights.processed} / ${insights.limit}`
            : "—";
      const detail = insights.phase_detail ? ` — ${insights.phase_detail}` : "";
      pushProgress(
        `insights-${insights.id}-${insights.phase}`,
        `Insights #${insights.id}: ${phaseLabel(insights.phase)} (${progress})${detail}`,
      );
    }

    if (latestInsights?.status === "complete") {
      pushEvent(`insights-complete-${latestInsights.id}`, `Insights #${latestInsights.id} complete`);
    }

    if (latestInsights?.status === "failed") {
      pushEvent(
        `insights-failed-${latestInsights.id}`,
        `Insights #${latestInsights.id} failed: ${latestInsights.error || "unknown error"}`,
      );
    }

    for (const intent of intentJobs) {
      pushProgress(
        `intent-progress-${intent.id}`,
        `Doorverwezen intents #${intent.id}: ${intent.processed} / ${intent.limit ?? "?"} gesprekken`,
      );
    }

    if (latestIntent?.status === "complete") {
      pushEvent(
        `intent-complete-${latestIntent.id}`,
        `Doorverwezen intents #${latestIntent.id} complete — ${latestIntent.messages_analyzed ?? 0} labeled`,
      );
    }

    if (latestIntent?.status === "failed") {
      pushEvent(
        `intent-failed-${latestIntent.id}`,
        `Doorverwezen intents #${latestIntent.id} failed: ${latestIntent.error || "unknown error"}`,
      );
    }

    if (pipelineStage.value === "warming") {
      const warm = cacheWarm.value;
      pushProgress(
        "cache-warm",
        warm
          ? `Warming dashboard cache… ${warm.done} / ${warm.total} conversations (${warm.pct}%)`
          : "Warming dashboard cache…",
      );
    }

    if (cacheReady.value) {
      pushEvent("cache-ready", "Cache warm — dashboard data ready on Results page");
    }

    if (ingestJobs.length > 0) {
      resetLogsForNewRun(ingestJobs[0].id);
    }
  }

  function tickElapsed() {
    elapsedTick.value += 1;

    const starts = [
      ...runningIngestJobs.value.map((job) => job.created_at),
      ...runningSentimentJobs.value.map((job) => job.created_at),
      ...runningInsightsJobs.value.map((job) => job.created_at),
    ]
      .filter(Boolean)
      .map((iso) => new Date(iso).getTime());

    // Anchor on the scheduler's run start (epoch seconds) so the timer keeps ticking during
    // cache warming and after a reload, even when no ingest/insights job is "running".
    const runStartedAt = pipelineRun.value?.started_at;
    if (runStartedAt) {
      starts.push(runStartedAt * 1000);
    }

    if (starts.length && pipelineRunning.value) {
      const earliest = Math.min(...starts);
      pipelineElapsedSeconds.value = Math.max(0, Math.floor((Date.now() - earliest) / 1000));
    } else if (!pipelineRunning.value) {
      pipelineElapsedSeconds.value = 0;
    }
  }

  function startElapsedTimer() {
    stopElapsedTimer();
    tickElapsed();
    elapsedTimer = setInterval(tickElapsed, 1000);
  }

  function stopElapsedTimer() {
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  function pollIntervalMs() {
    return pipelineRunning.value ? 2000 : 10000;
  }

  function schedulePoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
    }
    pollTimer = setInterval(() => loadStatus(), pollIntervalMs());
  }

  async function loadStatus() {
    const id = agentId.value?.trim() || null;

    if (!id) {
      healthStatus.value = null;
      ingestJob.value = null;
      sentimentJob.value = null;
      insightsJob.value = null;
      intentJob.value = null;
      runningIngestJobs.value = [];
      runningSentimentJobs.value = [];
      runningInsightsJobs.value = [];
      runningIntentJobs.value = [];
      jobLimits.value = null;
      return;
    }

    try {
      const [health, latestIngest, latestSentiment, latestInsights, latestIntent, running] = await Promise.all([
        api.getHealthStatus(id),
        api.getLatestIngestJob(id),
        api.getLatestSentimentJob(id),
        api.getLatestInsightsJob(id),
        api.getLatestIntentJob(id),
        api.getRunningJobs(id, MAX_RUNNING_JOBS),
      ]);

      healthStatus.value = health;
      ingestJob.value = latestIngest;
      sentimentJob.value = latestSentiment;
      insightsJob.value = latestInsights;
      intentJob.value = latestIntent;
      runningIngestJobs.value = running.jobs.filter((job) => job.job_type === "ingest");
      runningSentimentJobs.value = running.jobs.filter((job) => job.job_type === "sentiment");
      runningInsightsJobs.value = running.jobs.filter((job) => job.job_type === "insights");
      runningIntentJobs.value = running.jobs.filter((job) => job.job_type === "intent");
      jobLimits.value = {
        globalRunning: running.global_running,
        globalLimit: running.global_limit,
        ingestRunning: running.ingest_running,
        ingestLimit: running.ingest_limit,
        ingestSlotsLeft: running.ingest_slots_left,
        sentimentRunning: running.sentiment_running,
        sentimentLimit: running.sentiment_limit,
        sentimentSlotsLeft: running.sentiment_slots_left,
        insightsRunning: running.insights_running,
        insightsLimit: running.insights_limit,
        insightsSlotsLeft: running.insights_slots_left,
        intentRunning: running.intent_running,
        intentLimit: running.intent_limit,
        intentSlotsLeft: running.intent_slots_left,
        agentRunning: running.agent_running,
      };

      updateLogs();
      tickElapsed();

      if (pipelineRunning.value) {
        startElapsedTimer();
      } else {
        stopElapsedTimer();
      }

      schedulePoll();
      error.value = "";
    } catch (err) {
      error.value = err.message;
    }
  }

  async function purgeSystem() {
    if (!window.confirm("Delete ALL data, jobs, cache, and queue state? This cannot be undone.")) {
      return;
    }
    adminBusy.value = true;
    error.value = "";
    success.value = "";
    try {
      const result = await api.purgeSystem();
      success.value = `System purged. Cancelled ${result.cancelled_jobs.length} job(s).`;
      trackedIngestId = null;
      logs.value = [];
      seenEvents.clear();
      await loadStatus();
    } catch (err) {
      error.value = err.message;
    } finally {
      adminBusy.value = false;
    }
  }

  async function startPipeline() {
    const id = agentId.value?.trim();
    if (!id) return;

    pipelineStarting.value = true;
    error.value = "";
    success.value = "";
    try {
      const result = await api.startPipeline(id);
      success.value = result.message;
      trackedIngestId = null;
      await loadStatus();
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(() => loadStatus(), 2000);
    } catch (err) {
      error.value = err.message;
    } finally {
      pipelineStarting.value = false;
    }
  }

  async function reanalyze() {
    const id = agentId.value?.trim();
    if (!id) return;

    reanalyzing.value = true;
    error.value = "";
    success.value = "";
    try {
      const result = await api.reanalyze(id);
      success.value = result.message;
      trackedIngestId = null;
      await loadStatus();
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(() => loadStatus(), 2000);
    } catch (err) {
      error.value = err.message;
    } finally {
      reanalyzing.value = false;
    }
  }

  async function labelReferredIntents(reanalyze = false) {
    const id = agentId.value?.trim();
    if (!id) return;

    labelingReferredIntents.value = true;
    error.value = "";
    success.value = "";
    try {
      const result = await api.labelReferredIntents(id, reanalyze);
      success.value = result.message;
      await loadStatus();
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(() => loadStatus(), 2000);
    } catch (err) {
      error.value = err.message;
    } finally {
      labelingReferredIntents.value = false;
    }
  }

  watch(agentId, () => {
    trackedIngestId = null;
    seenEvents.clear();
    logs.value = [];
    loadStatus();
  });

  onUnmounted(() => {
    if (pollTimer) clearInterval(pollTimer);
    stopElapsedTimer();
  });

  return {
    healthStatus,
    ingestJob,
    sentimentJob,
    insightsJob,
    intentJob,
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
    ingestRunning,
    sentimentRunning,
    insightsRunning,
    intentRunning,
    jobElapsed,
    ingestEta,
    sentimentEta,
    insightsEta,
    intentEta,
    ingestProgress,
    sentimentProgress,
    insightsProgress,
    intentProgress,
    formatDuration,
    formatLogTime,
    phaseLabel,
    jobStatusClass,
    loadStatus,
    startPipeline,
    reanalyze,
    labelReferredIntents,
    purgeSystem,
  };
}
