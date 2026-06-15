<script setup>
import { computed } from "vue";

const props = defineProps({
  points: { type: Array, default: () => [] },
});

const path = computed(() => {
  if (!props.points.length) {
    return "";
  }
  const width = 200;
  const height = 48;
  const padding = 4;
  const xs = props.points.map((_, index) => {
    if (props.points.length === 1) {
      return width / 2;
    }
    return padding + (index / (props.points.length - 1)) * (width - padding * 2);
  });
  const ys = props.points.map((point) => {
    const stars = point.stars ?? 3;
    return height - padding - ((stars - 1) / 4) * (height - padding * 2);
  });
  return xs.map((x, i) => `${i === 0 ? "M" : "L"}${x},${ys[i]}`).join(" ");
});
</script>

<template>
  <svg v-if="points.length" class="sparkline" viewBox="0 0 200 48" aria-hidden="true">
    <path :d="path" fill="none" stroke="#0f766e" stroke-width="2" />
    <circle
      v-for="(point, index) in points"
      :key="index"
      :cx="points.length === 1 ? 100 : 4 + (index / (points.length - 1)) * 192"
      :cy="48 - 4 - (((point.stars ?? 3) - 1) / 4) * 40"
      r="3"
      fill="#0f766e"
    />
  </svg>
</template>

<style scoped>
.sparkline {
  width: 200px;
  height: 48px;
}
</style>
