import { computed, ref } from "vue";
import { api } from "../api/client";

const user = ref(null);
const authEnabled = ref(false);
const loading = ref(true);
let initPromise = null;

export function useAuth() {
  const isAuthenticated = computed(() => Boolean(user.value));
  const isAuthRequired = computed(() => authEnabled.value);

  async function loadAuthStatus() {
    loading.value = true;
    try {
      const status = await api.getAuthStatus();
      authEnabled.value = status.auth_enabled;
      user.value = status.authenticated ? status.user : null;
      return status;
    } catch {
      authEnabled.value = true;
      user.value = null;
      return { auth_enabled: true, authenticated: false, user: null };
    } finally {
      loading.value = false;
    }
  }

  function ensureAuthLoaded() {
    if (!initPromise) {
      initPromise = loadAuthStatus().finally(() => {
        if (loading.value) {
          loading.value = false;
        }
      });
    }
    return initPromise;
  }

  async function startGoogleLogin() {
    const redirect = new URLSearchParams(window.location.search).get("redirect") || "/";
    sessionStorage.setItem("auth_redirect", redirect);
    const { url } = await api.getGoogleAuthUrl();
    window.location.href = url;
  }

  async function completeGoogleLogin(code, state) {
    const profile = await api.completeGoogleAuth(code, state);
    user.value = profile;
    return profile;
  }

  async function logout() {
    await api.logout();
    user.value = null;
  }

  return {
    user,
    authEnabled,
    loading,
    isAuthenticated,
    isAuthRequired,
    loadAuthStatus,
    ensureAuthLoaded,
    startGoogleLogin,
    completeGoogleLogin,
    logout,
  };
}
