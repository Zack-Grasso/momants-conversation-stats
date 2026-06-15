import { createRouter, createWebHistory } from "vue-router";
import { useAuth } from "./composables/useAuth";
import RunPage from "./pages/RunPage.vue";
import ResultsPage from "./pages/ResultsPage.vue";
import LoginPage from "./pages/LoginPage.vue";
import OAuthCallbackPage from "./pages/OAuthCallbackPage.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", name: "login", component: LoginPage, meta: { public: true } },
    { path: "/oauth/callback", name: "oauth-callback", component: OAuthCallbackPage, meta: { public: true } },
    { path: "/", name: "run", component: RunPage },
    { path: "/results", name: "results", component: ResultsPage },
  ],
});

router.beforeEach(async (to) => {
  const { ensureAuthLoaded, isAuthenticated, isAuthRequired } = useAuth();
  await ensureAuthLoaded();

  const needsAuth = isAuthRequired.value && !isAuthenticated.value;

  if (to.meta.public) {
    if (!needsAuth && to.name === "login") {
      return { name: "run" };
    }
    return true;
  }

  if (needsAuth) {
    return { name: "login", query: { redirect: to.fullPath } };
  }

  return true;
});
