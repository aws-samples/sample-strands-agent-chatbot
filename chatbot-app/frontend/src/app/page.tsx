"use client"

import { ChatInterface } from "@/components/ChatInterface"
import { SidebarProvider } from "@/components/ui/sidebar"
import { ConnectorProvider } from "@/hooks/useConnector"

export default function Home() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <ConnectorProvider>
        <SidebarProvider defaultOpen={false}>
          <ChatInterface />
        </SidebarProvider>
      </ConnectorProvider>
    </div>
  )
}
