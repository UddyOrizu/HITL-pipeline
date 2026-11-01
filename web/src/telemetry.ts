// Review-session telemetry client. Records how the document was read inside this page only:
// sections in view (dwell), scroll depth, focus, flags opened, comments and edits. Batched every 5 seconds.

import { api } from "./api";

type Event = { type: string; client_ts: string; section_id?: string; span_id?: string; value?: number };

const FLUSH_MS = 5000;
const ACTIVE_WINDOW_MS = 60000;

export class TelemetryClient {
  private taskId: string;
  private queue: Event[] = [];
  private dwell = new Map<string, number>();
  private visible = new Set<string>();
  private lastTick = performance.now();
  private lastActivity = Date.now();
  private maxScroll = 0;
  private sentScroll = 0;
  private focused = document.hasFocus() && document.visibilityState === "visible";
  private timer: number | undefined;
  private observer: IntersectionObserver | undefined;
  private cleanup: (() => void)[] = [];

  constructor(taskId: string) {
    this.taskId = taskId;
  }

  start(scrollRoot: HTMLElement | null) {
    this.push({ type: "document_opened" });
    this.observer = new IntersectionObserver(
      (entries) => {
        this.accumulate();
        for (const e of entries) {
          const id = (e.target as HTMLElement).dataset.sectionId;
          if (!id) continue;
          if (e.isIntersecting && e.intersectionRatio >= 0.35) this.visible.add(id);
          else this.visible.delete(id);
        }
      },
      { threshold: [0, 0.35, 0.7] },
    );
    document.querySelectorAll<HTMLElement>("[data-section-id]").forEach((el) => this.observer!.observe(el));

    const onActivity = () => (this.lastActivity = Date.now());
    const onScroll = () => {
      onActivity();
      const el = scrollRoot || document.documentElement;
      const depth = (el.scrollTop + el.clientHeight) / Math.max(1, el.scrollHeight);
      this.maxScroll = Math.max(this.maxScroll, Math.min(1, depth));
    };
    const onFocus = () => this.setFocus(true);
    const onBlur = () => this.setFocus(false);
    const onVis = () => this.setFocus(document.visibilityState === "visible" && document.hasFocus());
    const target: any = scrollRoot || window;
    target.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("mousemove", onActivity, { passive: true });
    window.addEventListener("keydown", onActivity);
    window.addEventListener("focus", onFocus);
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVis);
    const onUnload = () => this.flush(true);
    window.addEventListener("pagehide", onUnload);
    this.cleanup.push(() => {
      target.removeEventListener("scroll", onScroll);
      window.removeEventListener("mousemove", onActivity);
      window.removeEventListener("keydown", onActivity);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("pagehide", onUnload);
    });
    onScroll();
    this.timer = window.setInterval(() => this.flush(), FLUSH_MS);
  }

  /** Re-scan for section elements (after the document renders). */
  observeSections() {
    document.querySelectorAll<HTMLElement>("[data-section-id]").forEach((el) => this.observer?.observe(el));
  }

  private setFocus(focused: boolean) {
    if (focused === this.focused) return;
    this.accumulate();
    this.focused = focused;
    this.push({ type: focused ? "focus_gained" : "focus_lost" });
  }

  private active() {
    return this.focused && Date.now() - this.lastActivity < ACTIVE_WINDOW_MS;
  }

  private accumulate() {
    const now = performance.now();
    const elapsed = (now - this.lastTick) / 1000;
    this.lastTick = now;
    if (!this.active()) return;
    for (const id of this.visible) this.dwell.set(id, (this.dwell.get(id) || 0) + elapsed);
  }

  push(e: Omit<Event, "client_ts">) {
    this.queue.push({ ...e, client_ts: new Date().toISOString() });
  }

  async flush(final = false) {
    this.accumulate();
    if (this.active()) this.push({ type: "heartbeat" });
    for (const [id, secs] of this.dwell) {
      if (secs >= 0.5) this.push({ type: "section_in_view", section_id: id, value: Math.round(secs * 10) / 10 });
    }
    this.dwell.clear();
    if (this.maxScroll > this.sentScroll + 0.01) {
      this.push({ type: "scroll_depth", value: Math.round(this.maxScroll * 1000) / 1000 });
      this.sentScroll = this.maxScroll;
    }
    if (!this.queue.length) return;
    const events = this.queue.splice(0);
    try {
      await api("/v1/telemetry", { body: { task_id: this.taskId, events } });
    } catch {
      if (!final) this.queue.unshift(...events.slice(-500));
    }
  }

  async stop() {
    window.clearInterval(this.timer);
    this.observer?.disconnect();
    this.cleanup.forEach((fn) => fn());
    await this.flush(true);
  }
}
