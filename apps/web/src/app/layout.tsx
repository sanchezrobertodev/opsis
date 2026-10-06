import type { Metadata } from "next";
import { Archivo } from "next/font/google";
import { Rail } from "@/components/shell/Rail";
import { TopBar } from "@/components/shell/TopBar";
import { Providers } from "./providers";
import "./globals.css";

// One family; the width axis gives titles their credits-roll character.
const archivo = Archivo({
  subsets: ["latin"],
  axes: ["wdth"],
  variable: "--font-archivo",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "AI Media Studio", template: "%s | AI Media Studio" },
  description: "Personal AI video studio",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={archivo.variable}>
      <body className="h-dvh overflow-hidden">
        <Providers>
          <div className="flex h-full">
            <Rail />
            <div className="flex min-w-0 flex-1 flex-col">
              <TopBar />
              <main className="min-h-0 flex-1">{children}</main>
            </div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
