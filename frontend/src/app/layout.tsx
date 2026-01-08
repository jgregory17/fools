import type { Metadata } from "next"
import { Inter } from "next/font/google"
import "./globals.css"
import "@livekit/components-styles"

const inter = Inter({ subsets: ["latin"] })

export const metadata: Metadata = {
  title: "Agent Playground",
  description: "Talk to AI voice agents via WebRTC",
}

export default function RootLayout({
  children,}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.className} min-h-screen`} style={{ background: '#161616' }}>
        <div className="bg-grid min-h-screen">
          {children}
        </div>
      </body>
    </html>
  )
}
