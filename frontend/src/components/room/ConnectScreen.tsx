"use client"

import { useState, useEffect } from "react"
import { motion } from "framer-motion"
import { Mic, Volume2, Loader2, AlertCircle, Wifi, Settings, User, Hash, Bot, ChevronDown, Sparkles } from "lucide-react"
import { generateRandomId } from "@/lib/utils"
import { GlassCard } from "@/components/ui/GlassCard"
import { NeonButton } from "@/components/ui/NeonButton"
import { DeviceSelector } from "@/components/room/DeviceSelector"
import { getAgents, AgentInfo } from "@/lib/api"

interface ConnectScreenProps {
  onConnect: (room: string, identity: string, agent?: string) => Promise<void>
  isConnecting: boolean
  error: string | null
}

export function ConnectScreen({ onConnect, isConnecting, error }: ConnectScreenProps) {
  const [roomName, setRoomName] = useState("playground")
  const [identity, setIdentity] = useState("")
  const [showDevices, setShowDevices] = useState(false)
  const [selectedMic, setSelectedMic] = useState<string>("")

  // Agent selection state
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [selectedAgent, setSelectedAgent] = useState<string>("")
  const [loadingAgents, setLoadingAgents] = useState(true)
  const [agentsError, setAgentsError] = useState<string | null>(null)

  // Generate random identity on mount
  useEffect(() => {
    setIdentity(`user-${generateRandomId(6)}`)
  }, [])

  // Fetch available agents on mount
  useEffect(() => {
    async function fetchAgents() {
      setLoadingAgents(true)
      setAgentsError(null)

      const result = await getAgents()

      if (result.error) {
        setAgentsError(result.error)
      } else {
        setAgents(result.agents)
        // Set default agent if available
        if (result.default) {
          setSelectedAgent(result.default)
        } else if (result.agents.length > 0) {
          setSelectedAgent(result.agents[0].name)
        }
      }

      setLoadingAgents(false)
    }

    fetchAgents()
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (roomName && identity) {
      await onConnect(roomName, identity, selectedAgent || undefined)
    }
  }

  // Get selected agent info for display
  const selectedAgentInfo = agents.find(a => a.name === selectedAgent)

  const livekitUrl = process.env.NEXT_PUBLIC_LIVEKIT_URL || "ws://localhost:7880"

  return (
    <GlassCard className="w-full max-w-md p-8">
      {/* Logo / Title */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="text-center mb-8"
      >
        <motion.div
          className="w-20 h-20 mx-auto mb-4 rounded-full bg-gradient-to-br from-cyan-500 to-purple-600 flex items-center justify-center"
          animate={{
            boxShadow: [
              "0 0 20px rgba(0, 245, 255, 0.5)",
              "0 0 40px rgba(0, 245, 255, 0.8)",
              "0 0 20px rgba(0, 245, 255, 0.5)",
            ],
          }}
          transition={{ duration: 2, repeat: Infinity }}
        >
          <Mic className="w-10 h-10 text-white" />
        </motion.div>
        <h1 className="text-3xl font-bold bg-gradient-to-r from-cyan-400 to-purple-500 bg-clip-text text-transparent">
          Agent Playground
        </h1>
        <p className="text-muted-foreground mt-2">
          Talk to AI voice agents via WebRTC
        </p>
      </motion.div>

      {/* Connection Form */}
      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Agent Selection */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-muted-foreground flex items-center gap-2">
            <Bot className="w-4 h-4" />
            Select Agent
          </label>
          {loadingAgents ? (
            <div className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-xl text-muted-foreground flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading agents...
            </div>
          ) : agentsError ? (
            <div className="w-full px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 flex items-center gap-2">
              <AlertCircle className="w-4 h-4" />
              {agentsError}
            </div>
          ) : agents.length === 0 ? (
            <div className="w-full px-4 py-3 bg-yellow-500/10 border border-yellow-500/20 rounded-xl text-yellow-400 flex items-center gap-2">
              <AlertCircle className="w-4 h-4" />
              No agents available
            </div>
          ) : (
            <div className="relative">
              <select
                value={selectedAgent}
                onChange={(e) => setSelectedAgent(e.target.value)}
                disabled={isConnecting}
                className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-xl text-white appearance-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-cyan-500/50 transition-all disabled:opacity-50"
              >
                {agents.map((agent) => (
                  <option key={agent.name} value={agent.name} className="bg-gray-900 text-white">
                    {agent.name}
                  </option>
                ))}
              </select>
              <ChevronDown className="absolute right-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
            </div>
          )}

          {/* Agent Info Card */}
          {selectedAgentInfo && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              className="p-3 bg-gradient-to-r from-cyan-500/10 to-purple-500/10 border border-white/10 rounded-xl"
            >
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-cyan-500 to-purple-600 flex items-center justify-center flex-shrink-0">
                  <Sparkles className="w-5 h-5 text-white" />
                </div>
                <div className="flex-1 min-w-0">
                  <h4 className="font-medium text-white truncate">{selectedAgentInfo.name}</h4>
                  <p className="text-xs text-muted-foreground line-clamp-2 mt-0.5">
                    {selectedAgentInfo.description || "Voice AI agent"}
                  </p>
                  {selectedAgentInfo.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {selectedAgentInfo.tags.slice(0, 3).map((tag) => (
                        <span
                          key={tag}
                          className="px-2 py-0.5 text-xs bg-white/10 rounded-full text-muted-foreground"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </motion.div>
          )}
        </div>

        {/* Room Name */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-muted-foreground flex items-center gap-2">
            <Hash className="w-4 h-4" />
            Room Name
          </label>
          <input
            type="text"
            value={roomName}
            onChange={(e) => setRoomName(e.target.value)}
            placeholder="Enter room name"
            className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-xl text-white placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-cyan-500/50 transition-all"
            disabled={isConnecting}
          />
        </div>

        {/* Identity */}
        <div className="space-y-2">
          <label className="text-sm font-medium text-muted-foreground flex items-center gap-2">
            <User className="w-4 h-4" />
            Your Name
          </label>
          <input
            type="text"
            value={identity}
            onChange={(e) => setIdentity(e.target.value)}
            placeholder="Enter your name"
            className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-xl text-white placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-cyan-500/50 transition-all"
            disabled={isConnecting}
          />
        </div>

        {/* Device Settings Toggle */}
        <button
          type="button"
          onClick={() => setShowDevices(!showDevices)}
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-white transition-colors"
        >
          <Settings className="w-4 h-4" />
          {showDevices ? "Hide" : "Show"} device settings
        </button>

        {/* Device Selector */}
        {showDevices && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
          >
            <DeviceSelector
              selectedMic={selectedMic}
              onMicChange={setSelectedMic}
            />
          </motion.div>
        )}

        {/* Error Message */}
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex items-center gap-2 p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400"
          >
            <AlertCircle className="w-5 h-5 flex-shrink-0" />
            <span className="text-sm">{error}</span>
          </motion.div>
        )}

        {/* Connect Button */}
        <NeonButton
          type="submit"
          disabled={isConnecting || !roomName || !identity}
          className="w-full"
        >
          {isConnecting ? (
            <>
              <Loader2 className="w-5 h-5 animate-spin" />
              Connecting...
            </>
          ) : (
            <>
              <Wifi className="w-5 h-5" />
              Join Room
            </>
          )}
        </NeonButton>
      </form>

      {/* Server Info */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.5 }}
        className="mt-6 pt-6 border-t border-white/10"
      >
        <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
          <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
          <span>LiveKit Server:</span>
          <code className="px-2 py-0.5 bg-white/5 rounded text-cyan-400">
            {livekitUrl}
          </code>
        </div>
      </motion.div>
    </GlassCard>
  )
}
