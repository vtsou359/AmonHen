import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Amon Hen — Fire Intelligence",
  description:
    "Monitoring and projection for wildfires in Greece, from open satellite data.",
};

export const viewport: Viewport = {
  themeColor: "#0E0F11",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      {/*
        Browser extensions (password managers, Grammarly, Dark Reader and
        friends) inject attributes into <body> before React hydrates, which
        React then reports as a hydration mismatch. Nothing we render here is
        dynamic — the server sends exactly `class="h-full overflow-hidden"` —
        so the only possible source of a mismatch on this element is external.

        suppressHydrationWarning applies to THIS element only, not its
        children, so genuine mismatches inside the app still surface.
      */}
      <body suppressHydrationWarning className="h-full overflow-hidden">
        {children}
      </body>
    </html>
  );
}
