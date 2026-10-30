import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Rail } from "@/components/shell/Rail";
import { TopBar } from "@/components/shell/TopBar";
import { Providers } from "./providers";
import "./globals.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist", display: "swap" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", display: "swap" });

export const metadata: Metadata = {
  title: { default: "Opsis", template: "%s | Opsis" },
  description: "An open-source AI video studio",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geist.variable} ${geistMono.variable}`} suppressHydrationWarning>
      <head>
        {/* animations off (Settings > Appearance) applies before the first paint */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{if(localStorage.getItem("motion")==="off")document.documentElement.dataset.motion="off"}catch(e){}`,
          }}
        />
      </head>
      <body className="h-dvh overflow-hidden">
        <div aria-hidden className="aura">
          <div className="aura-light aura-beige" />
          <div className="aura-light aura-ember" />
          <div className="aura-light aura-warm" />
          <div className="aura-light aura-cool" />
        </div>
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
