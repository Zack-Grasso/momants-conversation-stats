<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAgent } from "./composables/useAgent";
import { useAuth } from "./composables/useAuth";

const route = useRoute();
const router = useRouter();
const { agentId } = useAgent();
const { user, isAuthenticated, isAuthRequired, logout, loading: authLoading } = useAuth();

const showAppShell = computed(() => {
  if (authLoading.value) {
    return false;
  }
  if (!isAuthRequired.value) {
    return true;
  }
  return isAuthenticated.value;
});

const agentLabel = computed(() => {
  const id = agentId.value.trim();
  return id ? `${id.slice(0, 8)}…` : "none selected";
});

async function signOut() {
  await logout();
  if (isAuthRequired.value) {
    await router.push("/login");
  }
}
</script>

<template>
  <main class="page" :class="{ 'page-auth': !showAppShell && !authLoading }">
    <template v-if="authLoading">
      <p class="hint loading-hint">Loading…</p>
    </template>
    <template v-else-if="showAppShell">
      <header class="hero">
        <div class="hero-row">
          <div>
            <h1>Conversation Stats</h1>
            <p>Ingest, analyze, and explore agent conversations.</p>
          </div>
          <div class="hero-meta">
            <span class="agent-badge">Agent: {{ agentLabel }}</span>
            <span v-if="user" class="user-badge">
              {{ user.name }}
              <button type="button" class="link small" @click="signOut">Sign out</button>
            </span>
          </div>
        </div>
        <nav class="nav">
          <router-link to="/" class="nav-link" :class="{ active: route.name === 'run' }">Run</router-link>
          <router-link to="/results" class="nav-link" :class="{ active: route.name === 'results' }">
            Results
          </router-link>
        </nav>
      </header>
      <router-view />
    </template>
    <template v-else>
      <router-view />
    </template>
  </main>
</template>

<style scoped>
.page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 2rem 1.25rem 3rem;
}

.hero h1 { margin: 0 0 0.25rem; }
.hero p { margin: 0; color: #475569; }

.hero-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.agent-badge {
  font-size: 0.85rem;
  padding: 0.35rem 0.65rem;
  border-radius: 999px;
  background: #ecfeff;
  color: #155e75;
  white-space: nowrap;
}

.hero-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.5rem;
}

.user-badge {
  font-size: 0.85rem;
  color: #475569;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.nav {
  display: flex;
  gap: 0.5rem;
  margin-top: 1rem;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 0;
}

.nav-link {
  display: inline-block;
  padding: 0.6rem 1rem;
  text-decoration: none;
  color: #64748b;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  font-weight: 500;
}

.nav-link:hover { color: #0f766e; }
.nav-link.active {
  color: #0f766e;
  border-bottom-color: #0f766e;
}

.loading-hint {
  margin-top: 2rem;
  text-align: center;
}

.page-auth {
  display: flex;
  min-height: 100vh;
  align-items: center;
  justify-content: center;
  padding-top: 0;
}
</style>
