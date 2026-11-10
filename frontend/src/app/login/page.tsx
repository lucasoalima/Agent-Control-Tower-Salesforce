"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useSWRConfig } from "swr";
import { ApiError, api, everyKey } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
      });
      // A cached "signed out" answer would send the dashboard straight back here.
      await mutate(everyKey, undefined, { revalidate: false });
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed. Try again.");
      setBusy(false);
    }
  }

  const field =
    "mt-1 block w-full rounded-sm border border-line bg-raised px-3 py-2 text-ink";

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-6 py-10">
      <h1 className="text-[1.6rem] leading-tight font-bold tracking-tight">Agent Control Tower</h1>
      <p className="mt-1 mb-6 text-muted">Sign in as an operator to review what your agents are doing.</p>

      <form onSubmit={signIn} className="flex flex-col gap-4">
        <label className="block">
          Username
          <input name="username" autoComplete="username" required autoFocus className={field} />
        </label>
        <label className="block">
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            className={field}
          />
        </label>
        {error ? (
          <p role="alert" className="text-deny">
            {error}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={busy}
          className="cursor-pointer rounded-sm bg-accent px-4 py-2 font-bold text-raised disabled:cursor-default disabled:opacity-60"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>

      <p className="mt-6 text-[13px] text-muted">
        The first operator account is created by <code className="font-mono">act seed</code>, which
        prints its password once.
      </p>
    </main>
  );
}
