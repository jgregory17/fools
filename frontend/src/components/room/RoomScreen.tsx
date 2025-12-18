"use client"

import { useState, useCallback, useEffect, useRef } from "react"
import {
  useVoiceAssistant,
  useConnectionState,
  useTracks,
  useLocalParticipant,
  useParticipants,
  useRoomContext,
} from "@livekit/components-react"
import { ConnectionState, Track, RoomEvent } from "livekit-client"
import { motion, AnimatePresence } from "framer-motion"
import {
  Mic,
  MicOff,
  PhoneOff,
  Settings,
  MessageSquare,
  Volume2,
  Radio,
  Users,
  Clock,
} from "lucide-react"
import { GlassCard } from "@/components/ui/GlassCard"
import { NeonButton } from "@/components/ui/NeonButton"
import { TranscriptPanel } from "@/components/room/TranscriptPanel"
import { WaveformOrbAvatar } from "@/components/avatar/WaveformOrbAvatar"
import { AudioLevelMeter } from "@/components/room/AudioLevelMeter"

interface RoomScreenProps {
  roomName: string
  identity: string
  agentName?: string
  onDisconnect: () => void
}

export function RoomScreen({ roomName, identity, agentName, onDisconnect }: RoomScreenProps) {
  const connectionState = useConnectionState()
  const { state: agentState, audioTrack: agentAudioTrack } = useVoiceAssistant()
  const { localParticipant } = useLocalParticipant()
  const participants = useParticipants()
  const room = useRoomContext()

  const [isMuted, setIsMuted] = useState(false)
  const [isPushToTalk, setIsPushToTalk] = useState(false)
  const [isPushing, setIsPushing] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [connectionTime, setConnectionTime] = useState(0)

  // Connection timer
  useEffect(() => {
    if (connectionState === ConnectionState.Connected) {
      const interval = setInterval(() => {
        setConnectionTime((t) => t + 1)
      }, 1000)
      return () => clearInterval(interval)
    }
  }, [connectionState])

  // Handle mute toggle
  const toggleMute = useCallback(async () => {
    if (localParticipant) {
      await localParticipant.setMicrophoneEnabled(isMuted)
      setIsMuted(!isMuted)
    }
  }, [localParticipant, isMuted])

  // Push-to-talk handlers
  const handlePushStart = useCallback(async () => {
    if (isPushToTalk && localParticipant) {
      await localParticipant.setMicrophoneEnabled(true)
      setIsPushing(true)
    }
  }, [isPushToTalk, localParticipant])

  const handlePushEnd = useCallback(async () => {
    if (isPushToTalk && localParticipant) {
      await localParticipant.setMicrophoneEnabled(false)
      setIsPushing(false)
    }
  }, [isPushToTalk, localParticipant])

  // Toggle push-to-talk mode
  const togglePushToTalk = useCallback(async () => {
    if (!isPushToTalk && localParticipant) {
      // Enabling PTT - mute by default
      await localParticipant.setMicrophoneEnabled(false)
      setIsMuted(true)
    } else if (localParticipant) {
      // Disabling PTT - unmute
      await localParticipant.setMicrophoneEnabled(true)
      setIsMuted(false)
    }
    setIsPushToTalk(!isPushToTalk)
  }, [isPushToTalk, localParticipant])

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, "0")}`
  }

  const agentParticipant = participants.find((p) => p.isAgent)
  const isAgentSpeaking = agentState === "speaking"

  return (
    <div className="w-full space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center justify-between"
      >
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div
              className={`w-3 h-3 rounded-full ${
                connectionState === ConnectionState.Connected
                  ? "bg-green-500 animate-pulse"
                  : "bg-yellow-500"
              }`}
            />
            <span className="text-sm text-muted-foreground">
              {connectionState === ConnectionState.Connected ? "Connected" : "Connecting..."}
            </span>
          </div>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Clock className="w-4 h-4" />
            {formatTime(connectionTime)}
          </div>
        </div>

        <div className="flex items-center gap-4">
          {agentName && (
            <span className="text-sm text-purple-400 flex items-center gap-1.5 bg-purple-500/10 px-2 py-1 rounded-lg">
              <span className="w-2 h-2 rounded-full bg-purple-500" />
              {agentName}
            </span>
          )}
          <span className="text-sm text-muted-foreground flex items-center gap-2">
            <Users className="w-4 h-4" />
            {participants.length} in room
          </span>
          <code className="px-2 py-1 bg-white/5 rounded text-xs text-cyan-400">
            #{roomName}
          </code>
        </div>
      </motion.div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Avatar & Controls */}
        <div className="lg:col-span-1 space-y-6">
          {/* Agent Avatar */}
          <GlassCard className="p-6" hover={false}>
            <div className="flex flex-col items-center">
              <WaveformOrbAvatar
                audioTrack={agentAudioTrack}
                agentState={agentState}
                isSpeaking={isAgentSpeaking}
              />

              <motion.div
                className="mt-4 text-center"
                animate={{ opacity: agentState ? 1 : 0.5 }}
              >
                <h3 className="text-lg font-semibold text-white">
                  {agentParticipant?.identity || "Agent"}
                </h3>
                <p className="text-sm text-muted-foreground capitalize">
                  {agentState || "initializing"}
                </p>
              </motion.div>
            </div>
          </GlassCard>

          {/* User Mic Controls */}
          <GlassCard className="p-6" hover={false}>
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-muted-foreground">Your Microphone</span>
                <AudioLevelMeter
                  participant={localParticipant}
                  isMuted={isMuted || (isPushToTalk && !isPushing)}
                />
              </div>

              {/* PTT Toggle */}
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Push-to-talk</span>
                <button
                  onClick={togglePushToTalk}
                  className={`relative w-12 h-6 rounded-full transition-colors ${
                    isPushToTalk ? "bg-cyan-500" : "bg-white/20"
                  }`}
                >
                  <motion.div
                    className="absolute top-1 w-4 h-4 rounded-full bg-white"
                    animate={{ left: isPushToTalk ? "calc(100% - 20px)" : "4px" }}
                    transition={{ type: "spring", stiffness: 500, damping: 30 }}
                  />
                </button>
              </div>

              {/* Mic Button */}
              <div className="flex justify-center">
                {isPushToTalk ? (
                  <motion.button
                    className={`w-20 h-20 rounded-full flex items-center justify-center transition-all ${
                      isPushing
                        ? "bg-cyan-500 shadow-lg shadow-cyan-500/50"
                        : "bg-white/10 hover:bg-white/20"
                    }`}
                    onMouseDown={handlePushStart}
                    onMouseUp={handlePushEnd}
                    onMouseLeave={handlePushEnd}
                    onTouchStart={handlePushStart}
                    onTouchEnd={handlePushEnd}
                    whileTap={{ scale: 0.95 }}
                  >
                    <Radio className={`w-8 h-8 ${isPushing ? "text-white" : "text-muted-foreground"}`} />
                  </motion.button>
                ) : (
                  <motion.button
                    className={`w-20 h-20 rounded-full flex items-center justify-center transition-all ${
                      isMuted
                        ? "bg-red-500/20 border-2 border-red-500"
                        : "bg-cyan-500 shadow-lg shadow-cyan-500/50"
                    }`}
                    onClick={toggleMute}
                    whileTap={{ scale: 0.95 }}
                  >
                    {isMuted ? (
                      <MicOff className="w-8 h-8 text-red-400" />
                    ) : (
                      <Mic className="w-8 h-8 text-white" />
                    )}
                  </motion.button>
                )}
              </div>

              <p className="text-xs text-center text-muted-foreground">
                {isPushToTalk
                  ? "Hold to speak"
                  : isMuted
                  ? "Click to unmute"
                  : "Voice activated"}
              </p>
            </div>
          </GlassCard>

          {/* Disconnect Button */}
          <NeonButton
            variant="danger"
            onClick={onDisconnect}
            className="w-full"
          >
            <PhoneOff className="w-5 h-5" />
            Leave Room
          </NeonButton>
        </div>

        {/* Right: Transcript */}
        <div className="lg:col-span-2">
          <GlassCard className="h-[600px] flex flex-col" hover={false}>
            <div className="p-4 border-b border-white/10 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <MessageSquare className="w-5 h-5 text-cyan-400" />
                <span className="font-semibold">Conversation</span>
              </div>
              <div className="flex items-center gap-2">
                {isAgentSpeaking && (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="flex items-center gap-2 px-3 py-1 bg-purple-500/20 rounded-full"
                  >
                    <div className="w-2 h-2 rounded-full bg-purple-500 animate-pulse" />
                    <span className="text-xs text-purple-400">Agent speaking</span>
                  </motion.div>
                )}
              </div>
            </div>

            <TranscriptPanel userIdentity={identity} />
          </GlassCard>
        </div>
      </div>

      {/* Video/Avatar Placeholder */}
      {process.env.NEXT_PUBLIC_ENABLE_VIDEO === "true" && (
        <GlassCard className="p-6" hover={false}>
          <div className="text-center text-muted-foreground">
            <p className="text-sm">Video features coming soon</p>
          </div>
        </GlassCard>
      )}
    </div>
  )
}
