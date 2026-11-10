import type { Metadata } from "next";
import "@fontsource/b612/400.css";
import "@fontsource/b612/700.css";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Agent Control Tower", template: "%s | Agent Control Tower" },
  description: "See, govern and audit what AI agents do in your enterprise systems.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
