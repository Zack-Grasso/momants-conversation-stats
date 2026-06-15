import { computed, onUnmounted, ref } from "vue";
import { api } from "../api/client";
import {
  formatDuration,
  ingestEtaSeconds,
  insightsEtaSeconds,
  jobElapsedSeconds,
  jobProgressPercent,
  phaseLabel,
  useJobWebSocket,
} from "./useJobWebSocket";

function jobStorageKey(type, agentId) {
  return `momants_${type}_job_${agentId}`;
}

function normalizeJob(data, fallbackId) {
  return {
    id: data.id ?? data.job_id ?? fallbackId,
    agent_id: data.agent_id,
    status: data.status,
    phase: data.phase,
    phase_detail: data.phase_detail,
    phase_progress: data.phase_progress ?? 0,
    phase_total: data.phase_total ?? 0,
    processed: data.processed ?? 0,
    limit: data.limit,
    failed: data.failed ?? 0,
    messages_analyzed: data.messages_analyzed ?? 0,
    error: data.error,
    created_at: data.created_at,
    completed_at: data.completed_at,
  };
}

export function useJobRunner(agentId) {
  const ingestForm = ref({ limit: 10, reanalyze: true });
  const ingestJob = ref(null);
  const insightsJob = ref(null);
  const jobSubmitting = ref(false);
  const error = ref("");

  let ingestWsCleanup = null;
  let insightsWsCleanup = null;
  let elapsedTimer = null;

  const ingestElapsedSeconds = ref(0);
  const insightsElapsedSeconds = ref(0);

  const ingestRunning = computed(() => ingestJob.value?.status === "running");
  const insightsRunning = computed(() => insightsJob.value?.status === "running");
  const ingestEta = computed(() => ingestEtaSeconds(ingestJob.value));
  const insightsEta = computed(() => insightsEtaSeconds(insightsJob.value));
  const ingestProgress = computed(() => jobProgressPercent(ingestJob.value, "ingest"));
  const insightsProgress = computed(() => jobProgressPercent(insightsJob.value, "insights"));

  function tickElapsed() {
    if (ingestJob.value) {
      ingestElapsedSeconds.value = jobElapsedSeconds(ingestJob.value);
    }
    if (insightsJob.value) {
      insightsElapsedSeconds.value = jobElapsedSeconds(insightsJob.value);
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

  function persistJobId(type, jobId) {
    const id = agentId.value?.trim();
    if (!id) return;
    const key = jobStorageKey(type, id);
    if (jobId) {
      localStorage.setItem(key, String(jobId));
    } else {
      localStorage.removeItem(key);
    }
  }

  function clearFinishedJob(type, job) {
    if (!job || ["complete", "failed", "cancelled"].includes(job.status)) {
      persistJobId(type, null);
    }
  }

  function disconnectJobSockets() {
    ingestWsCleanup?.();
    insightsWsCleanup?.();
    ingestWsCleanup = null;
    insightsWsCleanup = null;
  }

  function watchIngestJob(jobId) {
    persistJobId("ingest", jobId);
    ingestWsCleanup = useJobWebSocket({
      jobType: "ingest",
      jobId,
      onUpdate: (data) => {
        ingestJob.value = normalizeJob(data, jobId);
        tickElapsed();
      },
      onDone: () => {
        clearFinishedJob("ingest", ingestJob.value);
        if (!insightsRunning.value) {
          stopElapsedTimer();
        }
      },
      onError: () => {},
      pollFallback: () => pollIngestJob(jobId),
    });
  }

  function watchInsightsJob(jobId) {
    persistJobId("insights", jobId);
    insightsWsCleanup = useJobWebSocket({
      jobType: "insights",
      jobId,
      onUpdate: (data) => {
        insightsJob.value = normalizeJob(data, jobId);
        tickElapsed();
      },
      onDone: () => {
        clearFinishedJob("insights", insightsJob.value);
        if (!ingestRunning.value) {
          stopElapsedTimer();
        }
      },
      onError: () => {},
      pollFallback: () => pollInsightsJob(jobId),
    });
  }

  async function pollIngestJob(jobId) {
    ingestJob.value = await api.getIngestJob(jobId);
    tickElapsed();
    if (ingestJob.value.status === "running") {
      startElapsedTimer();
    } else {
      clearFinishedJob("ingest", ingestJob.value);
    }
  }

  async function pollInsightsJob(jobId) {
    insightsJob.value = await api.getInsightsJob(jobId);
    tickElapsed();
    if (insightsJob.value.status === "running") {
      startElapsedTimer();
    } else {
      clearFinishedJob("insights", insightsJob.value);
    }
  }

  async function resumeJob(type, jobId) {
    if (!jobId) return;
    try {
      if (type === "ingest") {
        ingestJob.value = await api.getIngestJob(jobId);
        if (ingestJob.value.status === "running") {
          startElapsedTimer();
          watchIngestJob(jobId);
        } else {
          clearFinishedJob("ingest", ingestJob.value);
        }
      } else {
        insightsJob.value = await api.getInsightsJob(jobId);
        if (insightsJob.value.status === "running") {
          startElapsedTimer();
          watchInsightsJob(jobId);
        } else {
          clearFinishedJob("insights", insightsJob.value);
        }
      }
    } catch {
      persistJobId(type, null);
    }
  }

  async function restoreJobs() {
    const id = agentId.value?.trim();
    if (!id) {
      ingestJob.value = null;
      insightsJob.value = null;
      return;
    }

    disconnectJobSockets();
    ingestJob.value = null;
    insightsJob.value = null;

    const storedIngestId = localStorage.getItem(jobStorageKey("ingest", id));
    const storedInsightsId = localStorage.getItem(jobStorageKey("insights", id));

    if (storedIngestId) {
      await resumeJob("ingest", Number(storedIngestId));
    } else {
      const latest = await api.getLatestIngestJob(id);
      if (latest?.status === "running") {
        ingestJob.value = latest;
        persistJobId("ingest", latest.id);
        startElapsedTimer();
        watchIngestJob(latest.id);
      }
    }

    if (storedInsightsId) {
      await resumeJob("insights", Number(storedInsightsId));
    } else {
      const latest = await api.getLatestInsightsJob(id);
      if (latest?.status === "running") {
        insightsJob.value = latest;
        persistJobId("insights", latest.id);
        startElapsedTimer();
        watchInsightsJob(latest.id);
      }
    }
  }

  async function startIngest() {
    const id = agentId.value?.trim();
    if (!id) return;

    jobSubmitting.value = true;
    error.value = "";
    disconnectJobSockets();
    try {
      const started = await api.startIngest(id, Number(ingestForm.value.limit), ingestForm.value.reanalyze);
      ingestJob.value = {
        id: started.job_id,
        agent_id: id,
        status: started.status,
        processed: 0,
        limit: ingestForm.value.limit,
        failed: 0,
        messages_analyzed: 0,
      };
      persistJobId("ingest", started.job_id);
      startElapsedTimer();
      watchIngestJob(started.job_id);
    } catch (err) {
      error.value = err.message;
    } finally {
      jobSubmitting.value = false;
    }
  }

  async function startInsights() {
    const id = agentId.value?.trim();
    if (!id || insightsRunning.value) return;

    jobSubmitting.value = true;
    error.value = "";
    try {
      const started = await api.startInsights(id);
      insightsJob.value = {
        id: started.job_id,
        agent_id: id,
        status: started.status,
        processed: 0,
        phase: "metrics",
        phase_progress: 0,
        phase_total: 0,
        failed: 0,
        messages_analyzed: 0,
      };
      persistJobId("insights", started.job_id);
      startElapsedTimer();
      watchInsightsJob(started.job_id);
    } catch (err) {
      error.value = err.message;
    } finally {
      jobSubmitting.value = false;
    }
  }

  async function cancelIngest() {
    if (!ingestJob.value?.id || !ingestRunning.value) return;
    try {
      ingestJob.value = await api.cancelIngestJob(ingestJob.value.id);
      clearFinishedJob("ingest", ingestJob.value);
      disconnectJobSockets();
      if (!insightsRunning.value) stopElapsedTimer();
    } catch (err) {
      error.value = err.message;
    }
  }

  async function cancelInsights() {
    if (!insightsJob.value?.id || !insightsRunning.value) return;
    try {
      insightsJob.value = await api.cancelInsightsJob(insightsJob.value.id);
      clearFinishedJob("insights", insightsJob.value);
      disconnectJobSockets();
      if (!ingestRunning.value) stopElapsedTimer();
    } catch (err) {
      error.value = err.message;
    }
  }

  onUnmounted(() => {
    disconnectJobSockets();
    stopElapsedTimer();
  });

  return {
    ingestForm,
    ingestJob,
    insightsJob,
    jobSubmitting,
    error,
    ingestRunning,
    insightsRunning,
    ingestElapsedSeconds,
    insightsElapsedSeconds,
    ingestEta,
    insightsEta,
    ingestProgress,
    insightsProgress,
    formatDuration,
    phaseLabel,
    restoreJobs,
    startIngest,
    startInsights,
    cancelIngest,
    cancelInsights,
  };
}

export function jobStatusClass(status) {
  if (status === "running") return "running";
  if (status === "complete") return "complete";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  return "";
}
