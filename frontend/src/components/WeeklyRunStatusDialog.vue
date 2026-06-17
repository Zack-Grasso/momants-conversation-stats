<script setup>
import { computed, onUnmounted, ref, watch } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  running: { type: Boolean, default: false },
  runState: { type: Object, default: null },
});

defineEmits(["close"]);

const tick = ref(0);
let tickTimer = null;

watch(
  () => props.open,
  (open) => {
    if (open && !tickTimer) {
      tickTimer = setInterval(() => {
        tick.value += 1;
      }, 1000);
    } else if (!open && tickTimer) {
      clearInterval(tickTimer);
      tickTimer = null;
    }
  },
  { immediate: true },
);

onUnmounted(() => {
  if (tickTimer) clearInterval(tickTimer);
});

const STEP_LABELS = {
  models: "Loading ML models",
  starting: "Starting",
  ingest: "Ingesting conversations",
  analyze: "Analyzing unanswered & weak answers",
  pdf: "Generating PDF",
  zip: "Building ZIP bundle",
  complete: "Wrapping up",
};

const PHASE_LABELS = {
  preload: "Preparing",
  starting: "Starting",
  agents: "Processing agents",
  finalize: "Finalizing",
  done: "Complete",
};

const statusLabel = computed(() => {
  const state = props.runState;
  if (!state) return props.running ? "Starting…" : "Idle";
  if (state.status === "failed") return "Failed";
  if (state.status === "complete") return "Complete";
  if (state.phase === "preload") return STEP_LABELS.models;
  if (state.phase === "finalize") return STEP_LABELS.zip;
  if (state.current_step && STEP_LABELS[state.current_step]) return STEP_LABELS[state.current_step];
  if (state.phase && PHASE_LABELS[state.phase]) return PHASE_LABELS[state.phase];
  return "Running…";
});

const agentLine = computed(() => {
  const state = props.runState;
  if (!state?.agent_total) return null;
  const index = state.agent_index || 0;
  const name = state.current_agent_name || state.current_agent_id?.slice(0, 8) || "agent";
  if (state.phase === "finalize") {
    return `Finished ${state.agents_complete || 0} of ${state.agent_total} agents`;
  }
  if (index > 0) {
    return `Agent ${index} of ${state.agent_total} — ${name}`;
  }
  return `${state.agent_total} agent${state.agent_total === 1 ? "" : "s"} queued`;
});

const progressPercent = computed(() => {
  const state = props.runState;
  if (!state) return props.running ? 2 : 0;
  if (state.status === "complete" || state.phase === "done") return 100;
  if (state.status === "failed") return 100;
  if (state.phase === "preload") return 5;

  const total = Math.max(state.agent_total || 1, 1);
  const done = state.agents_complete || 0;
  const stepFrac = { starting: 0.05, ingest: 0.2, analyze: 0.55, pdf: 0.85 }[state.current_step] || 0;

  if (state.phase === "finalize") {
    return Math.min(99, Math.round(((done + 0.95) / total) * 100));
  }

  const agentProgress = (done + stepFrac) / total;
  return Math.min(99, Math.round(agentProgress * 100));
});

const elapsedLabel = computed(() => {
  void tick.value;
  const started = props.runState?.started_at;
  if (!started) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(started).getTime()) / 1000));
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
});

const countsLine = computed(() => {
  const state = props.runState;
  if (!state || state.status !== "complete") return null;
  const ok = state.agents_complete ?? 0;
  const fail = state.agents_failed ?? 0;
  if (fail > 0) return `${ok} succeeded, ${fail} failed`;
  return `${ok} agent${ok === 1 ? "" : "s"} processed`;
});

const canClose = computed(
  () => !props.running || props.runState?.status === "complete" || props.runState?.status === "failed",
);
</script>

<template>
  <div v-if="open" class="dialog-backdrop" @click.self="canClose && $emit('close')">
    <div class="dialog" role="dialog" aria-labelledby="weekly-run-title" aria-modal="true">
      <header class="dialog-header">
        <h3 id="weekly-run-title">Weekly report run</h3>
        <button v-if="canClose" type="button" class="close-btn" aria-label="Close" @click="$emit('close')">×</button>
      </header>

      <div class="dialog-body">
        <div class="status-row">
          <span class="status-pill" :class="runState?.status || (running ? 'running' : 'idle')">
            {{ statusLabel }}
          </span>
          <span v-if="elapsedLabel" class="elapsed">{{ elapsedLabel }}</span>
        </div>

        <div class="progress-track" aria-hidden="true">
          <div class="progress-fill" :style="{ width: `${progressPercent}%` }" />
        </div>
        <p class="progress-label">{{ progressPercent }}%</p>

        <p v-if="agentLine" class="detail">{{ agentLine }}</p>
        <p v-if="runState?.week_id" class="detail muted">Week {{ runState.week_id }}</p>

        <p v-if="runState?.status === 'failed' && runState.error" class="error">{{ runState.error }}</p>
        <p v-else-if="countsLine" class="success">{{ countsLine }}</p>
        <p v-else-if="running" class="hint">This can take several minutes per agent while models analyze conversations.</p>
      </div>

      <footer v-if="canClose" class="dialog-footer">
        <button type="button" class="primary" @click="$emit('close')">Close</button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(4px);
}
.dialog {
  width: min(480px, 100%);
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 20px 50px rgba(15, 23, 42, 0.2);
  overflow: hidden;
}
.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid #e2e8f0;
}
.dialog-header h3 { margin: 0; font-size: 1.05rem; }
.close-btn {
  border: none;
  background: transparent;
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
  color: #64748b;
  padding: 0 0.25rem;
}
.dialog-body { padding: 1.25rem; }
.status-row { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; margin-bottom: 1rem; }
.status-pill {
  display: inline-block;
  padding: 0.25rem 0.65rem;
  border-radius: 999px;
  font-size: 0.85rem;
  font-weight: 600;
  background: #e2e8f0;
  color: #334155;
}
.status-pill.running { background: #fef3c7; color: #92400e; }
.status-pill.complete { background: #dcfce7; color: #166534; }
.status-pill.failed { background: #fee2e2; color: #991b1b; }
.elapsed { font-size: 0.85rem; color: #64748b; }
.progress-track {
  height: 10px;
  background: #e2e8f0;
  border-radius: 999px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #2563eb);
  border-radius: 999px;
  transition: width 0.4s ease;
}
.progress-label { margin: 0.35rem 0 0.75rem; font-size: 0.8rem; color: #64748b; }
.detail { margin: 0 0 0.35rem; font-size: 0.95rem; }
.detail.muted { color: #64748b; font-size: 0.85rem; }
.hint { margin: 0.75rem 0 0; font-size: 0.85rem; color: #64748b; }
.error { margin: 0.75rem 0 0; color: #b91c1c; font-size: 0.9rem; }
.success { margin: 0.75rem 0 0; color: #15803d; font-size: 0.9rem; }
.dialog-footer {
  padding: 0.75rem 1.25rem 1.25rem;
  display: flex;
  justify-content: flex-end;
}
.primary { padding: 0.5rem 1rem; }
</style>
