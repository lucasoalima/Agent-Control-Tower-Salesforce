import type { ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { TONE_BG, TONE_TEXT, type Tone } from "@/lib/labels";

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="mb-6">
      <h1 className="text-[1.6rem] leading-tight font-bold tracking-tight">{title}</h1>
      {children ? <p className="mt-1 max-w-[70ch] text-muted">{children}</p> : null}
    </header>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-2 flex items-baseline justify-between gap-4">
      <h2 className="text-base font-bold">{children}</h2>
      {aside ? <div className="text-[13px] text-muted">{aside}</div> : null}
    </div>
  );
}

/** A coloured dot followed by a word. The word carries the meaning; the dot is redundancy. */
export function ToneLabel({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 font-bold ${TONE_TEXT[tone]}`}>
      <span aria-hidden className={`size-2 shrink-0 rounded-full ${TONE_BG[tone]}`} />
      {children}
    </span>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-block rounded-sm border border-line px-1.5 py-px text-xs text-muted">
      {children}
    </span>
  );
}

export function Code({ children }: { children: ReactNode }) {
  return <code className="font-mono text-[12px] text-muted">{children}</code>;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <p role="status" className="py-8 text-muted">
      {label}…
    </p>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-sm border border-dashed border-line px-5 py-6">
      <p className="font-bold">{title}</p>
      {children ? <div className="mt-1 max-w-[70ch] text-muted">{children}</div> : null}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 401) return null; // redirecting to sign-in
  const message =
    error instanceof ApiError ? error.message : "Something went wrong while loading this page.";
  return (
    <div role="alert" className="rounded-sm border border-deny px-5 py-4">
      <p className="font-bold text-deny">This data could not be loaded</p>
      <p className="mt-1 text-muted">{message} Reload the page to try again.</p>
    </div>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <p className="mb-5 max-w-[80ch] border-l-2 border-accent bg-surface px-4 py-2.5 text-[13px] text-muted">
      {children}
    </p>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-[28rem] overflow-auto rounded-sm border border-line bg-surface p-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap break-words">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
