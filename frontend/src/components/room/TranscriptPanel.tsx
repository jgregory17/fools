"use client"

import { useEffect, useRef, useState } from "react"
import { useRoomContext } from "@livekit/components-react"
import { motion, AnimatePresence } from "framer-motion"
import { User, Bot } from "lucide-react"

interface TranscriptMessage {
  id: string
  sender: "user" | "agent"
  identity: string
  text: string
  timestamp: Date
  isFinal: boolean
}

interface TranscriptPanelProps {
  userIdentity: string
}

export function TranscriptPanel({ userIdentity }: TranscriptPanelProps) {
  const room = useRoomContext()
  const [messages, setMessages] = useState<TranscriptMessage[]>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const pendingMessages = useRef<Map<string, TranscriptMessage>>(new Map())

  // Register text stream handler for transcriptions
  useEffect(() => {
    if (!room) return

    const handleTextStream = async (
      reader: any,
      participantInfo: { identity: string }
    ) => {
      try {
        const participantIdentity = participantInfo.identity
        const text = await reader.readAll()
        const info = reader.info

        // Get attributes
        const isFinal = info?.attributes?.["lk.transcription_final"] === "true"
        const segmentId = info?.attributes?.["lk.segment_id"] || `msg-${Date.now()}`
        const isTranscription = info?.attributes?.["lk.transcribed_track_id"] !== undefined

        if (!isTranscription || !text.trim()) return

        // Determine if user or agent
        const isAgent = participantIdentity !== userIdentity
        const sender = isAgent ? "agent" : "user"

        const message: TranscriptMessage = {
          id: segmentId,
          sender,
          identity: participantIdentity,
          text: text.trim(),
          timestamp: new Date(),
          isFinal,
        }

        setMessages((prev) => {
          // Find existing message with same segment ID
          const existingIndex = prev.findIndex((m) => m.id === segmentId)

          if (existingIndex >= 0) {
            // Update existing message
            const updated = [...prev]
            updated[existingIndex] = message
            return updated
          } else {
            // Add new message
            return [...prev, message]
          }
        })
      } catch (error) {
        console.error("Error reading text stream:", error)
      }
    }

    // Register handler for transcription topic
    room.registerTextStreamHandler("lk.transcription", handleTextStream)

    return () => {
      // Cleanup - unregister handler
      try {
        room.unregisterTextStreamHandler?.("lk.transcription")
      } catch (e) {
        // Ignore cleanup errors
      }
    }
  }, [room, userIdentity])

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 md:p-4 space-y-3 md:space-y-4 min-h-0">
      <AnimatePresence initial={false}>
        {messages.length === 0 ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex flex-col items-center justify-center h-full text-gray-300"
          >
            <div className="bg-gray-800/50 p-6 rounded-full mb-4 border border-gray-700/50">
              <Bot className="w-10 h-10 text-purple-400" />
            </div>
            <p className="text-sm font-medium text-white">Waiting for conversation to start...</p>
            <p className="text-xs mt-2 text-gray-400">Speak into your microphone to begin</p>
          </motion.div>
        ) : (
          messages.map((message, index) => (
            <motion.div
              key={`${message.id}-${index}`}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.2 }}
              className={`flex gap-2 md:gap-3 ${
                message.sender === "user" ? "flex-row-reverse" : "flex-row"
              }`}
            >
              {/* Avatar */}
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 border ${
                  message.sender === "user"
                    ? "bg-cyan-900/40 border-cyan-500/50"
                    : "bg-purple-900/40 border-purple-500/50"
                }`}
              >
                {message.sender === "user" ? (
                  <User className="w-4 h-4 text-cyan-400" />
                ) : (
                  <Bot className="w-4 h-4 text-purple-400" />
                )}
              </div>

              {/* Message Bubble */}
              <div
                className={`max-w-[85%] md:max-w-[80%] px-3 md:px-4 py-2.5 md:py-3 rounded-2xl ${
                  message.sender === "user"
                    ? "bg-cyan-900/40 border border-cyan-500/40 rounded-tr-sm"
                    : "bg-purple-900/40 border border-purple-500/40 rounded-tl-sm"
                } ${!message.isFinal ? "opacity-70" : ""}`}
              >
                <p
                  className={`text-sm leading-relaxed ${
                    message.sender === "user" ? "text-white" : "text-white"
                  }`}
                >
                  {message.text}
                  {!message.isFinal && (
                    <motion.span
                      animate={{ opacity: [1, 0.3, 1] }}
                      transition={{ duration: 1, repeat: Infinity }}
                      className="ml-1 text-gray-300"
                    >
                      ...
                    </motion.span>
                  )}
                </p>
                <p className="text-xs text-gray-400 mt-1.5">
                  {message.timestamp.toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </p>
              </div>
            </motion.div>
          ))
        )}
      </AnimatePresence>

      {/* Typing Indicator (shown when agent is thinking) */}
      <AnimatePresence>
        {/* This would be triggered by agent state = "thinking" */}
      </AnimatePresence>
    </div>
  )
}
