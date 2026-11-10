"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/agents", label: "Agents" },
  { href: "/approvals", label: "Approval queue" },
  { href: "/executions", label: "Execution history" },
  { href: "/policies", label: "Policies" },
];

export function Rail({
  username,
  pendingApprovals,
  onSignOut,
}: {
  username: string;
  pendingApprovals: number | undefined;
  onSignOut: () => void;
}) {
  const pathname = usePathname();
  const isCurrent = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <aside className="flex shrink-0 flex-col border-line bg-surface md:sticky md:top-0 md:h-dvh md:w-56 md:border-r max-md:border-b">
      <Link href="/" className="block px-5 pt-5 pb-4 leading-tight font-bold">
        Agent
        <br className="max-md:hidden" /> Control Tower
      </Link>

      <nav aria-label="Main" className="flex gap-0.5 px-2 max-md:overflow-x-auto max-md:pb-2 md:flex-col">
        {NAV.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            aria-current={isCurrent(href) ? "page" : undefined}
            className={`flex items-center justify-between gap-3 rounded-sm px-3 py-1.5 whitespace-nowrap ${
              isCurrent(href) ? "bg-ink text-ground" : "text-ink hover:bg-ground"
            }`}
          >
            {label}
            {href === "/approvals" && pendingApprovals ? (
              <span
                className={`rounded-sm px-1.5 text-xs font-bold ${
                  isCurrent(href) ? "bg-ground text-ink" : "bg-approval text-raised"
                }`}
              >
                {pendingApprovals}
                <span className="sr-only"> waiting</span>
              </span>
            ) : null}
          </Link>
        ))}
      </nav>

      <div className="mt-auto flex items-center justify-between gap-3 border-t border-line px-5 py-3 text-[13px] max-md:hidden">
        <span className="truncate text-muted">{username}</span>
        <button type="button" onClick={onSignOut} className="cursor-pointer text-accent underline underline-offset-2">
          Sign out
        </button>
      </div>
      <button
        type="button"
        onClick={onSignOut}
        className="absolute top-4 right-4 cursor-pointer text-[13px] text-accent underline underline-offset-2 md:hidden"
      >
        Sign out
      </button>
    </aside>
  );
}
