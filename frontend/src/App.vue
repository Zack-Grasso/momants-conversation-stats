<script setup>
import { onMounted, ref } from "vue";
import { api } from "./api/client";

const conversations = ref([]);
const selectedId = ref(null);
const selectedConversation = ref(null);
const stats = ref(null);
const loading = ref(false);
const error = ref("");

const form = ref({
  title: "",
  role: "user",
  content: "",
});

async function loadConversations() {
  loading.value = true;
  error.value = "";
  try {
    conversations.value = await api.listConversations();
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

async function selectConversation(id) {
  selectedId.value = id;
  selectedConversation.value = await api.getConversation(id);
  stats.value = await api.getStats(id);
}

async function createConversation() {
  if (!form.value.title.trim() || !form.value.content.trim()) {
    return;
  }

  loading.value = true;
  error.value = "";
  try {
    const created = await api.createConversation({
      title: form.value.title.trim(),
      messages: [{ role: form.value.role, content: form.value.content.trim() }],
    });
    form.value.title = "";
    form.value.content = "";
    await loadConversations();
    await selectConversation(created.id);
  } catch (err) {
    error.value = err.message;
  } finally {
    loading.value = false;
  }
}

onMounted(loadConversations);
</script>

<template>
  <main class="page">
    <header class="hero">
      <h1>Conversation Stats</h1>
      <p>FastAPI + Hugging Face sentiment analysis with Vue frontend.</p>
    </header>

    <section class="panel">
      <h2>New conversation</h2>
      <form class="form" @submit.prevent="createConversation">
        <input v-model="form.title" placeholder="Conversation title" required />
        <select v-model="form.role">
          <option value="user">User</option>
          <option value="agent">Agent</option>
        </select>
        <textarea v-model="form.content" rows="3" placeholder="First message" required />
        <button type="submit" :disabled="loading">Create & analyze</button>
      </form>
    </section>

    <p v-if="error" class="error">{{ error }}</p>

    <section class="grid">
      <div class="panel">
        <h2>Conversations</h2>
        <ul class="list">
          <li v-for="item in conversations" :key="item.id">
            <button class="link" @click="selectConversation(item.id)">
              {{ item.title }}
            </button>
          </li>
        </ul>
      </div>

      <div class="panel" v-if="selectedConversation">
        <h2>{{ selectedConversation.title }}</h2>
        <div v-if="stats" class="stats">
          <span>Messages: {{ stats.message_count }}</span>
          <span>Positive: {{ stats.positive_count }}</span>
          <span>Negative: {{ stats.negative_count }}</span>
          <span v-if="stats.average_sentiment_score !== null">
            Avg score: {{ stats.average_sentiment_score.toFixed(2) }}
          </span>
        </div>
        <article v-for="message in selectedConversation.messages" :key="message.id" class="message">
          <header>
            <strong>{{ message.role }}</strong>
            <span v-if="message.sentiment" class="badge" :class="message.sentiment.label.toLowerCase()">
              {{ message.sentiment.label }} ({{ message.sentiment.score.toFixed(2) }})
            </span>
          </header>
          <p>{{ message.content }}</p>
        </article>
      </div>
    </section>
  </main>
</template>

<style scoped>
.page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 2rem 1.25rem 3rem;
}

.hero h1 {
  margin: 0 0 0.25rem;
}

.hero p {
  margin: 0;
  color: #475569;
}

.panel {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 1rem 1.25rem;
  margin-top: 1.25rem;
}

.form {
  display: grid;
  gap: 0.75rem;
}

input,
select,
textarea,
button {
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  padding: 0.6rem 0.75rem;
}

button {
  background: #0f766e;
  border-color: #0f766e;
  color: #fff;
  cursor: pointer;
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: 1fr 2fr;
}

.list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.link {
  width: 100%;
  text-align: left;
  background: #f1f5f9;
  color: #0f172a;
  border-color: #e2e8f0;
}

.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-bottom: 1rem;
  color: #334155;
}

.message {
  border-top: 1px solid #e2e8f0;
  padding-top: 0.75rem;
  margin-top: 0.75rem;
}

.message header {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.badge {
  font-size: 0.85rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
}

.badge.positive {
  background: #dcfce7;
  color: #166534;
}

.badge.negative {
  background: #fee2e2;
  color: #991b1b;
}

.error {
  color: #b91c1c;
}

@media (max-width: 900px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
