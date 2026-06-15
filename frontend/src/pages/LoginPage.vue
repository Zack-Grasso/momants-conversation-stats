<script setup>
import { ref } from "vue";
import { useAuth } from "../composables/useAuth";

const { startGoogleLogin } = useAuth();
const error = ref("");
const loading = ref(false);

async function signIn() {
  loading.value = true;
  error.value = "";
  try {
    await startGoogleLogin();
  } catch (err) {
    error.value = err.message || "Could not start Google sign-in";
    loading.value = false;
  }
}
</script>

<template>
  <section class="login-card">
    <h1 class="login-title">Conversation Stats</h1>
    <p class="login-subtitle">Internal access only</p>
    <p class="hint">Sign in with your Momants Google account to continue.</p>
    <button type="button" class="google-btn" :disabled="loading" @click="signIn">
      <span class="google-mark" aria-hidden="true">G</span>
      <span>{{ loading ? "Redirecting…" : "Continue with Google" }}</span>
    </button>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.login-card {
  width: min(420px, 100%);
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  padding: 2rem 1.75rem;
  text-align: center;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.08);
}

.login-title {
  margin: 0 0 0.25rem;
  font-size: 1.5rem;
  color: #0f172a;
}

.login-subtitle {
  margin: 0 0 1rem;
  color: #475569;
  font-weight: 600;
}

.google-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.65rem;
  width: 100%;
  margin-top: 1.25rem;
  padding: 0.8rem 1rem;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  color: #0f172a;
  cursor: pointer;
  font-weight: 600;
  font-size: 0.95rem;
}

.google-btn:hover:not(:disabled) {
  border-color: #0f766e;
  background: #f0fdfa;
}

.google-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.google-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 999px;
  background: #4285f4;
  color: #fff;
  font-size: 0.8rem;
  font-weight: 700;
}
</style>
