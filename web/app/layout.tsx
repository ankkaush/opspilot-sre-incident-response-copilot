import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "OpsPilot",
  description: "SRE Incident Response Copilot",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">{children}</div>
      </body>
    </html>
  );
}
