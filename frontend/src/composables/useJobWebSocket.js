const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const PHASE_LABELS = {
  metrics: "Computing metrics",
  questions: "Clustering top questions",
  intents: "Labeling intents (ML)",
  unanswered: "Analyzing unanswered questions (ML)",
  done: "Complete",
};

function wsBaseUrl() {
  return API_BASE.replace(/^http/, "ws");
}

export function phaseLabel(phase) {
  return PHASE_LABELS[phase] || phase || "—";
}

export function useJobWebSocket({
  jobType,
  jobId,
  onUpdate,
  onDone,
  onError,
  pollFallback,
}) {
  let socket = null;
  let pollTimer = null;
  let closed = false;

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function cleanup() {
    closed = true;
    stopPoll();
    if (socket) {
      socket.close();
      socket = null;
    }
  }

  function startPoll() {
    if (!pollFallback || pollTimer) {
      return;
    }
    pollFallback();
    pollTimer = setInterval(pollFallback, 2000);
  }

  function connect() {
    if (!jobId) {
      return cleanup;
    }

    const url = `${wsBaseUrl()}/api/ws/jobs/${jobType}/${jobId}`;
    socket = new WebSocket(url);

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const job =
          data.event === "snapshot"
            ? data.job
            : { id: data.job_id ?? jobId, ...data };
        if (data.event === "snapshot" || data.event === "progress") {
          onUpdate?.(job);
        }
        if (data.event === "done") {
          onUpdate?.(job);
          onDone?.(job);
          cleanup();
        }
        if (data.event === "error") {
          onError?.(data.detail || "WebSocket error");
          cleanup();
          startPoll();
        }
      } catch (err) {
        onError?.(err.message);
      }
    };

    socket.onerror = () => {
      onError?.("WebSocket connection failed");
      cleanup();
      startPoll();
    };

    socket.onclose = () => {
      if (!closed) {
        startPoll();
      }
    };

    return cleanup;
  }

  const disconnect = connect();
  return disconnect;
}

export function formatDuration(totalSeconds) {
  if (totalSeconds == null || Number.isNaN(totalSeconds)) {
    return "—";
  }
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) {
    return `${minutes}m ${remainder}s`;
  }
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return `${hours}h ${mins}m`;
}

export function jobElapsedSeconds(job) {
  if (!job?.created_at) {
    return 0;
  }
  const start = new Date(job.created_at).getTime();
  const end = job.completed_at ? new Date(job.completed_at).getTime() : Date.now();
  return Math.max(0, Math.floor((end - start) / 1000));
}

export function ingestEtaSeconds(job) {
  if (!job || job.status !== "running") {
    return null;
  }
  const processed = job.processed ?? 0;
  const total = job.limit ?? 0;
  if (processed <= 0 || total <= 0) {
    return null;
  }
  const remaining = total - processed;
  if (remaining <= 0) {
    return null;
  }
  const elapsed = jobElapsedSeconds(job);
  return Math.max(1, Math.floor((elapsed / processed) * remaining));
}

export function insightsEtaSeconds(job) {
  if (!job || job.status !== "running") {
    return null;
  }
  const elapsed = jobElapsedSeconds(job);
  const phaseTotal = job.phase_total ?? 0;
  const phaseProgress = job.phase_progress ?? 0;

  if (phaseTotal > 0 && phaseProgress > 0) {
    const remaining = phaseTotal - phaseProgress;
    if (remaining <= 0) {
      return null;
    }
    return Math.max(1, Math.floor((elapsed / phaseProgress) * remaining));
  }

  const processed = job.processed ?? 0;
  const total = job.limit ?? 0;
  if (job.phase === "metrics" && processed > 0 && total > processed) {
    return Math.max(1, Math.floor((elapsed / processed) * (total - processed)));
  }

  return null;
}

export function jobProgressPercent(job, type = "ingest") {
  if (!job) {
    return 0;
  }
  if (type === "insights") {
    if (job.phase_total > 0) {
      return Math.min(100, Math.round((100 * (job.phase_progress ?? 0)) / job.phase_total));
    }
    if (job.limit > 0 && job.phase === "metrics") {
      return Math.min(100, Math.round((100 * (job.processed ?? 0)) / job.limit));
    }
    return job.status === "running" ? 5 : 100;
  }
  if (!job.limit) {
    return 0;
  }
  return Math.min(100, Math.round((100 * (job.processed ?? 0)) / job.limit));
}
