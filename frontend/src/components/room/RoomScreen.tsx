"use client"

import { useState, useCallback, useEffect } from "react"
import {
  useVoiceAssistant,
  useConnectionState,
  useLocalParticipant,
  useParticipants,
  useRoomContext,
} from "@livekit/components-react"
import { ConnectionState } from "livekit-client"
import { motion, AnimatePresence } from "framer-motion"
import {
  Mic,
  MicOff,
  PhoneOff,
  MessageSquare,
  Radio,
  Users,
  ChevronUp,
} from "lucide-react"
import { NeonButton } from "@/components/ui/NeonButton"
import { TranscriptPanel } from "@/components/room/TranscriptPanel"
import { WaveformOrbAvatar } from "@/components/avatar/WaveformOrbAvatar"
import { AudioLevelBar } from "@/components/room/AudioLevelBar"

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

  const [isMuted, setIsMuted] = useState(false)
  const [isPushToTalk, setIsPushToTalk] = useState(false)
  const [isPushing, setIsPushing] = useState(false)
  const [showTranscript, setShowTranscript] = useState(false)

  // Auto-collapse transcript during active audio
  const isAudioActive = agentState === "listening" || agentState === "speaking"

  useEffect(() => {
    if (isAudioActive && showTranscript) {
      // Keep transcript open if user explicitly toggled it
    } else if (isAudioActive) {
      // Auto-collapse during audio
      setShowTranscript(false)
    }
  }, [isAudioActive])

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

  const agentParticipant = participants.find((p) => p.isAgent)
  const isAgentSpeaking = agentState === "speaking"

  // Calculate agent orb size based on state
  const orbSize = isAudioActive ? 200 : 80

  return (
    <div className="w-full h-screen flex flex-col relative" style={{ background: '#161616' }}>
      {/* Audio level bar - fixed at top */}
      <AudioLevelBar
        participant={localParticipant}
        isMuted={isMuted || (isPushToTalk && !isPushing)}
      />

      {/* Minimal header - only visible during idle/non-audio states */}
      <AnimatePresence>
        {!isAudioActive && (
          <motion.div
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            className="flex items-center justify-between px-8 py-4"
          >
            <div className="flex items-center gap-4">
              <code className="px-3 py-1.5 bg-[#222222] border border-[#2d2d2d] rounded-lg text-xs text-[#94a3b8] font-mono">
                #{roomName}
              </code>
              {agentName && (
                <span className="text-sm text-[#94a3b8] font-medium">
                  {agentName}
                </span>
              )}
            </div>

            <div className="flex items-center gap-3">
              <span className="text-xs text-[#94a3b8] flex items-center gap-2">
                <Users className="w-3.5 h-3.5" />
                {participants.length}
              </span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main broadcast studio layout */}
      <div className="flex-1 flex items-center justify-center relative">
        {/* Centered agent orb - scales up during audio */}
        <AnimatePresence mode="wait">
          <motion.div
            key={isAudioActive ? "active" : "idle"}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.9 }}
            transition={{ duration: 0.4, ease: "easeInOut" }}
            className="flex flex-col items-center"
          >
            <WaveformOrbAvatar
              audioTrack={agentAudioTrack}
              agentState={agentState}
              isSpeaking={isAgentSpeaking}
              size={orbSize}
            />

            {/* Agent name - only show when not active */}
            {!isAudioActive && (
              <motion.p
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="mt-4 text-sm text-[#94a3b8] font-medium"
              >
                {agentParticipant?.identity || "Agent"}
              </motion.p>
            )}
          </motion.div>
        </AnimatePresence>

        {/* Controls - bottom center, fade during audio */}
        <motion.div
          animate={{ opacity: isAudioActive ? 0.4 : 1 }}
          transition={{ duration: 0.25 }}
          className="fixed bottom-8 left-1/2 -translate-x-1/2 flex items-center gap-4"
        >
          {/* Mute/Unmute button */}
          {isPushToTalk ? (
            <motion.button
              className={`w-16 h-16 rounded-full flex items-center justify-center transition-fast ${
                isPushing
                  ? "bg-[#ff6b35] live-glow-strong"
                  : "bg-[#222222] border border-[#2d2d2d] hover:border-[#ff6b35]"
              }`}
              onMouseDown={handlePushStart}
              onMouseUp={handlePushEnd}
              onMouseLeave={handlePushEnd}
              onTouchStart={handlePushStart}
              onTouchEnd={handlePushEnd}
              whileTap={{ scale: 0.95 }}
            >
              <Radio className={`w-7 h-7 ${isPushing ? "text-[#161616]" : "text-[#94a3b8]"}`} />
            </motion.button>
          ) : (
            <motion.button
              className={`w-16 h-16 rounded-full flex items-center justify-center transition-fast ${
                isMuted
                  ? "bg-[#c53030]/20 border-2 border-[#c53030]"
                  : "bg-[#ff6b35] live-glow"
              }`}
              onClick={toggleMute}
              whileTap={{ scale: 0.95 }}
            >
              {isMuted ? (
                <MicOff className="w-7 h-7 text-[#c53030]" />
              ) : (
                <Mic className="w-7 h-7 text-[#161616]" />
              )}
            </motion.button>
          )}

          {/* Disconnect button */}
          <NeonButton
            variant="ghost"
            size="sm"
            onClick={onDisconnect}
          >
            <PhoneOff className="w-4 h-4" />
            Leave
          </NeonButton>

          {/* Show transcript button - only during audio */}
          {isAudioActive && (
            <NeonButton
              variant="ghost"
              size="sm"
              onClick={() => setShowTranscript(!showTranscript)}
            >
              <MessageSquare className="w-4 h-4" />
              {showTranscript ? "Hide" : "Show"} Transcript
            </NeonButton>
          )}
        </motion.div>
      </div>

      {/* Collapsing transcript panel - slides from bottom */}
      <AnimatePresence>
        {showTranscript && (
          <motion.div
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", damping: 25, stiffness: 200 }}
            className="fixed bottom-0 left-0 right-0 h-[60vh] bg-[#222222] border-t border-[#2d2d2d] z-40"
          >
            <div className="h-full flex flex-col">
              <div className="flex items-center justify-between px-6 py-4 border-b border-[#2d2d2d]">
                <span className="text-sm font-semibold text-[#f3f4f6]">Conversation History</span>
                <button
                  onClick={() => setShowTranscript(false)}
                  className="text-[#94a3b8] hover:text-[#f3f4f6] transition-fast"
                >
                  <ChevronUp className="w-5 h-5" />
                </button>
              </div>
              <div className="flex-1 overflow-hidden">
                <TranscriptPanel userIdentity={identity} />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
