"use client";

/** A tiny cross-component signal so the Task Panel can refresh the instant
 * something happens elsewhere in the app, instead of waiting on its own
 * fixed polling interval. Any component that creates/approves/dismisses an
 * operation calls notifyOperationsChanged() right after; TaskPanel listens
 * and refreshes immediately, with the interval kept only as a fallback
 * (e.g. an operation progressing on its own between user actions). */

const EVENT_NAME = "pyxie:operations-changed";

export function notifyOperationsChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(EVENT_NAME));
}

export function onOperationsChanged(callback: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(EVENT_NAME, callback);
  return () => window.removeEventListener(EVENT_NAME, callback);
}
