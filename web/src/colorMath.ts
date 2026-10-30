export type Hsv = { h: number; s: number; v: number };

export function rgb(color: string): number[] {
  return [1, 3, 5].map((index) => parseInt(color.slice(index, index + 2), 16) / 255);
}

function linear(value: number): number {
  return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
}

export function hex(values: number[]): string {
  return `#${values.map((value) => Math.round(Math.max(0, Math.min(1, value)) * 255).toString(16).padStart(2, "0")).join("")}`;
}

export function hexToHsv(color: string): Hsv {
  const [r, g, b] = rgb(color), high = Math.max(r, g, b), low = Math.min(r, g, b), delta = high - low;
  let h = 0;
  if (delta) h = high === r ? (g - b) / delta : high === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  return { h: (h * 60 + 360) % 360, s: high ? delta / high : 0, v: high };
}

export function hsvToHex({ h, s, v }: Hsv): string {
  const hue = ((h % 360) + 360) % 360 / 60;
  const chroma = v * s, x = chroma * (1 - Math.abs(hue % 2 - 1)), m = v - chroma;
  const channels = [[chroma, x, 0], [x, chroma, 0], [0, chroma, x], [0, x, chroma], [x, 0, chroma], [chroma, 0, x]][Math.floor(hue)];
  return hex(channels.map((channel) => channel + m));
}

export function hslToHex(h: number, s: number, l: number): string {
  const a = s * Math.min(l, 1 - l);
  const channel = (n: number) => { const k = (n + h / 30) % 12; return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)); };
  return hex([channel(0), channel(8), channel(4)]);
}

function luminance(color: string): number {
  const [r, g, b] = rgb(color).map(linear);
  return r * .2126 + g * .7152 + b * .0722;
}

export function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
}

// Adapt a source color toward black or white until it meets the theme's contrast target.
export function ensureContrast(color: string, background: string, minimum: number): string {
  const source = rgb(color), destination = luminance(background) > .5 ? [0, 0, 0] : [1, 1, 1];
  for (let step = 0; step <= 100; step++) {
    const next = hex(source.map((value, index) => value + (destination[index] - value) * step / 100));
    if (contrast(next, background) >= minimum) return next;
  }
  return hex(destination);
}

// OKLab keeps hue and lightness differences more perceptually even than raw RGB.
export function lab(color: string): number[] {
  const [r, g, b] = rgb(color).map(linear);
  const l = Math.cbrt(.4122214708 * r + .5363325363 * g + .0514459929 * b);
  const m = Math.cbrt(.2119034982 * r + .6806995451 * g + .1073969566 * b);
  const s = Math.cbrt(.0883024619 * r + .2817188376 * g + .6299787005 * b);
  return [.2104542553 * l + .793617785 * m - .0040720468 * s, 1.9779984951 * l - 2.428592205 * m + .4505937099 * s, .0259040371 * l + .7827717662 * m - .808675766 * s];
}

export function labDistance(a: number[], b: number[]): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

export function colorDistance(a: string, b: string): number {
  return labDistance(lab(a), lab(b));
}
