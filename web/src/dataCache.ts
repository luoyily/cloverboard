export class BoundedCache<T> {
  private readonly items = new Map<string, T>();
  constructor(private readonly capacity: number) {}

  get(key: string): T | undefined {
    const value = this.items.get(key);
    if (value !== undefined) {
      this.items.delete(key);
      this.items.set(key, value);
    }
    return value;
  }

  set(key: string, value: T): void {
    this.items.delete(key);
    this.items.set(key, value);
    while (this.items.size > this.capacity) this.items.delete(this.items.keys().next().value!);
  }
}

// Share in-flight work across progressive catalogs and selection changes. The
// pool also bounds actual fetches when several effects subscribe to one load.
export class RequestCache<T> {
  private readonly values: BoundedCache<T>;
  private readonly pending = new Map<string, Promise<T>>();
  private readonly queue: (() => void)[] = [];
  private active = 0;

  constructor(capacity: number, private readonly concurrency = 4) {
    this.values = new BoundedCache<T>(capacity);
  }

  load(key: string, action: () => Promise<T>): Promise<T> {
    return this.loadMany([key], async () => [await action()])[0];
  }

  loadMany(keys: string[], action: (missing: string[]) => Promise<T[]>): Promise<T>[] {
    const missing: { key: string; resolve: (value: T) => void; reject: (error: unknown) => void }[] = [];
    const promises = keys.map((key) => {
      const cached = this.values.get(key);
      if (cached !== undefined) return Promise.resolve(cached);
      const existing = this.pending.get(key);
      if (existing) return existing;
      const promise = new Promise<T>((resolve, reject) => missing.push({ key, resolve, reject }));
      this.pending.set(key, promise);
      return promise;
    });
    if (missing.length) this.queue.push(() => {
      this.active++;
      void Promise.resolve().then(() => action(missing.map((item) => item.key))).then((values) => {
        if (values.length !== missing.length) throw Error("Incomplete batch response.");
        missing.forEach((item, index) => { this.values.set(item.key, values[index]); this.pending.delete(item.key); item.resolve(values[index]); });
      }).catch((error) => {
        missing.forEach((item) => { this.pending.delete(item.key); item.reject(error); });
      }).finally(() => { this.active--; this.drain(); });
    });
    this.drain();
    return promises;
  }

  private drain(): void {
    while (this.active < this.concurrency && this.queue.length) this.queue.shift()!();
  }
}
