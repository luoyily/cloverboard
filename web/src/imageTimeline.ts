import type { ImageRecord } from "./api";

export function imagesAtStep(images: ImageRecord[], name: string, requested: number | null): ImageRecord[] {
  const matching = images.filter((image) => image.name === name && (requested === null || image.step <= requested));
  if (!matching.length) return [];
  const step = Math.max(...matching.map((image) => image.step));
  return matching.filter((image) => image.step === step);
}

export function sampleSteps(steps: number[], limit: number): number[] {
  if (steps.length <= limit) return steps;
  return Array.from({ length: limit }, (_, index) => steps[Math.round(index * (steps.length - 1) / (limit - 1))]);
}
