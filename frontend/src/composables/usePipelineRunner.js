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
  const insightsJob = ref(null);
  const runningIngestJobs = ref([]);
  const runningInsightsJobs = ref([]);
  const jobLimits = ref(null);
  const adminBusy = ref(false);
  const logs = ref([]);
  const pipelineStarting = ref(false);
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
  const insightsRunning = computed(() => runningInsightsJobs.value.length > 0);

  const pipelineStage = computed(() => {
    if (ingestRunning.value) return "ingest";
    if (insightsRunning.value) return "insights";
    if (ingestJob.value?.status === "complete" && insightsJob.value?.status === "complete" && !cacheReady.value) {
      return "warming";
    }
    if (cacheReady.value) return "ready";
    return "idle";
  });

  const pipelineRunning = computed(
    () => ingestRunning.value || insightsRunning.value || pipelineStage.value === "warming",
  );

  const hasLiveProgress = computed(
    () => runningIngestJobs.value.length > 0 || runningInsightsJobs.value.length > 0 || pipelineStage.value === "warming",
  );

  const lastUpdated = computed(() => {
    const ingestAt = ingestJob.value?.completed_at;
    const insightsAt = insightsJob.value?.completed_at;
    if (!ingestAt && !insightsAt) return null;
    if (!ingestAt) return insightsAt;
    if (!insightsAt) return ingestAt;
    return ingestAt > insightsAt ? ingestAt : insightsAt;
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

  function updateLogs() {
    const ingestJobs = runningIngestJobs.value;
    const insightsJobs = runningInsightsJobs.value;
    const latestIngest = ingestJob.value;
    const latestInsights = insightsJob.value;

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
      insightsJob.value = null;
      runningIngestJobs.value = [];
      runningInsightsJobs.value = [];
      jobLimits.value = null;
      return;
    }

    try {
      const [health, latestIngest, latestInsights, running] = await Promise.all([
        api.getHealthStatus(id),
        api.getLatestIngestJob(id),
        api.getLatestInsightsJob(id),
        api.getRunningJobs(id, MAX_RUNNING_JOBS),
      ]);

      healthStatus.value = health;
      ingestJob.value = latestIngest;
      insightsJob.value = latestInsights;
      runningIngestJobs.value = running.jobs.filter((job) => job.job_type === "ingest");
      runningInsightsJobs.value = running.jobs.filter((job) => job.job_type === "insights");
      jobLimits.value = {
        globalRunning: running.global_running,
        globalLimit: running.global_limit,
        ingestRunning: running.ingest_running,
        ingestLimit: running.ingest_limit,
        ingestSlotsLeft: running.ingest_slots_left,
        insightsRunning: running.insights_running,
        insightsLimit: running.insights_limit,
        insightsSlotsLeft: running.insights_slots_left,
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
    insightsJob,
    runningIngestJobs,
    runningInsightsJobs,
    jobLimits,
    adminBusy,
    logs,
    cacheReady,
    pipelineRunning,
    pipelineStage,
    hasLiveProgress,
    pipelineStarting,
    error,
    success,
    lastUpdated,
    pipelineElapsedSeconds,
    cacheWarm,
    ingestRunning,
    insightsRunning,
    jobElapsed,
    ingestEta,
    insightsEta,
    ingestProgress,
    insightsProgress,
    formatDuration,
    formatLogTime,
    phaseLabel,
    jobStatusClass,
    loadStatus,
    startPipeline,
    purgeSystem,
  };
}
