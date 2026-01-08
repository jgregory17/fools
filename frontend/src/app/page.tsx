"use client"

import { useState, useCallback } from "react"
import { LiveKitRoom, RoomAudioRenderer } from "@livekit/components-react"
import { Room, RoomEvent, ConnectionState } from "livekit-client"
import { motion, AnimatePresence } from "framer-motion"
import { ConnectScreen } from "@/components/room/ConnectScreen"
import { RoomScreen } from "@/components/room/RoomScreen"
import { getConnectionConfig, ConnectionConfig } from "@/lib/api"

export default function Home() {
  const [connectionConfig, setConnectionConfig] = useState<ConnectionConfig | null>(null)
  const [isConnecting, setIsConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleConnect = useCallback(async (room: string, identity: string, agent?: string) => {
    setIsConnecting(true)
    setError(null)

    try {
      const config = await getConnectionConfig(room, identity, agent)
      setConnectionConfig(config)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect")
      setIsConnecting(false)
    }
  }, [])

  const handleDisconnect = useCallback(() => {
    setConnectionConfig(null)
    setIsConnecting(false)
  }, [])

  const handleRoomConnected = useCallback(() => {
    setIsConnecting(false)
  }, [])

  const handleRoomDisconnected = useCallback(() => {
    handleDisconnect()
  }, [handleDisconnect])

  return (
    <main className="min-h-screen h-screen flex items-center justify-center p-2 md:p-4 overflow-hidden">
      <AnimatePresence mode="wait">
        {!connectionConfig ? (
          <motion.div
            key="connect"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.3 }}
            className="w-full flex items-center justify-center"
          >
            <ConnectScreen
              onConnect={handleConnect}
              isConnecting={isConnecting}
              error={error}
            />
          </motion.div>
        ) : (
          <motion.div
            key="room"
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.3 }}
            className="w-full h-full max-w-7xl"
          >
            <LiveKitRoom
              serverUrl={connectionConfig.livekitUrl}
              token={connectionConfig.token}
              connect={true}
              audio={true}
              video={false}
              onConnected={handleRoomConnected}
              onDisconnected={handleRoomDisconnected}
              options={{
                adaptiveStream: true,
                dynacast: true,
              }}
              className="h-full"
            >
              <RoomScreen
                roomName={connectionConfig.room}
                identity={connectionConfig.identity}
                agentName={connectionConfig.agent}
                onDisconnect={handleDisconnect}
              />
              <RoomAudioRenderer />
            </LiveKitRoom>
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  )
}
