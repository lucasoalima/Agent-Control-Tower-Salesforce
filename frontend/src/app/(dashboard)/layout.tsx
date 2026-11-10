"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import useSWR, { SWRConfig, useSWRConfig } from "swr";
import { Rail } from "@/components/rail";
import { Loading } from "@/components/ui";
import { ApiError, api, everyKey, fetcher } from "@/lib/api";
import type { Operator, Overview } from "@/lib/types";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const me = useSWR<Operator, ApiError>("/auth/me", fetcher, { shouldRetryOnError: false });
  const overview = useSWR<Overview>(me.data ? "/overview" : null, fetcher, { refreshInterval: 5000 });

  const signedOut = me.error?.status === 401;
  useEffect(() => {
    if (signedOut) router.replace("/login");
  }, [signedOut, router]);

  async function signOut() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
    await mutate(everyKey, undefined, { revalidate: false });
  }

  if (!me.data) {
    return (
      <main className="p-8">
        {me.error && !signedOut ? (
          <p role="alert" className="text-deny">
            {me.error.message}
          </p>
        ) : (
          <Loading />
        )}
      </main>
    );
  }

  return (
    <SWRConfig
      value={{
        fetcher,
        onError: (error: unknown) => {
          if (error instanceof ApiError && error.status === 401) router.replace("/login");
        },
      }}
    >
      <div className="relative md:flex">
        <Rail
          username={me.data.username}
          pendingApprovals={overview.data?.pending_approvals}
          onSignOut={signOut}
        />
        <main className="min-w-0 flex-1 px-5 py-6 md:px-10 md:py-8">
          <div className="max-w-[1180px]">{children}</div>
        </main>
      </div>
    </SWRConfig>
  );
}
