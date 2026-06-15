<script setup>
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuth } from "../composables/useAuth";

const route = useRoute();
const router = useRouter();
const { completeGoogleLogin } = useAuth();
const error = ref("");

onMounted(async () => {
  const code = route.query.code;
  const state = route.query.state;
  const oauthError = route.query.error;

  if (oauthError) {
    error.value = "Google sign-in was cancelled or denied.";
    return;
  }
  if (!code || !state) {
    error.value = "Missing OAuth callback parameters.";
    return;
  }

  try {
    await completeGoogleLogin(String(code), String(state));
    const redirect = sessionStorage.getItem("auth_redirect") || "/";
    sessionStorage.removeItem("auth_redirect");
    await router.replace(redirect);
  } catch (err) {
    error.value = err.message || "Sign-in failed";
  }
});
</script>

<template>
  <section class="panel callback-panel">
    <h2>Signing you in…</h2>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-else class="hint">Completing Google authentication.</p>
    <router-link v-if="error" to="/login" class="link">Back to sign in</router-link>
  </section>
</template>

<style scoped>
.callback-panel {
  max-width: 420px;
  margin: 3rem auto;
  text-align: center;
}
</style>
