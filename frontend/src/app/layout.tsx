import type { Metadata } from "next";
import { Inter } from "next/font/google";
import localFont from "next/font/local";
import "./globals.css";

// Brand typeface (Cera Pro): Medium/Bold for titles, Regular/Medium/Bold for text.
const cera = localFont({
  src: [
    { path: "../../assets/fonts/Cera Pro Regular.ttf", weight: "400", style: "normal" },
    { path: "../../assets/fonts/Cera Pro Regular Italic.ttf", weight: "400", style: "italic" },
    { path: "../../assets/fonts/Cera Pro Medium.ttf", weight: "500", style: "normal" },
    { path: "../../assets/fonts/Cera Pro Bold.ttf", weight: "700", style: "normal" },
  ],
  variable: "--font-cera",
  display: "swap",
});

// Default reading face: lining figures and plain shapes (Cera Pro uses old-style figures,
// which make addresses and amounts hard to read). Cera Pro is kept for titles.
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: "Anychain | Assistente de transações",
  description: "Entenda transações EVM a partir de evidências verificáveis.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR" className={`${cera.variable} ${inter.variable}`}>
      <body>{children}</body>
    </html>
  );
}
