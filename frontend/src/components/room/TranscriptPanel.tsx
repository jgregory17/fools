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
      participantIdentity: string
    ) => {
      try {
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
    <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
      <AnimatePresence initial={false}>
        {messages.length === 0 ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex flex-col items-center justify-center h-full text-muted-foreground"
          >
            <Bot className="w-12 h-12 mb-4 opacity-50" />
            <p className="text-sm">Waiting for conversation to start...</p>
            <p className="text-xs mt-2">Speak into your microphone to begin</p>
          </motion.div>
        ) : (
          messages.map((message, index) => (
            <motion.div
              key={`${message.id}-${index}`}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.2 }}
              className={`flex gap-3 ${
                message.sender === "user" ? "flex-row-reverse" : "flex-row"
              }`}
            >
              {/* Avatar */}
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                  message.sender === "user"
                    ? "bg-cyan-500/20"
                    : "bg-purple-500/20"
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
                className={`max-w-[80%] px-4 py-3 rounded-2xl ${
                  message.sender === "user"
                    ? "bg-cyan-500/10 border border-cyan-500/20 rounded-tr-sm"
                    : "bg-purple-500/10 border border-purple-500/20 rounded-tl-sm"
                } ${!message.isFinal ? "opacity-70" : ""}`}
              >
                <p
                  className={`text-sm ${
                    message.sender === "user" ? "text-cyan-100" : "text-purple-100"
                  }`}
                >
                  {message.text}
                  {!message.isFinal && (
                    <motion.span
                      animate={{ opacity: [1, 0.3, 1] }}
                      transition={{ duration: 1, repeat: Infinity }}
                      className="ml-1"
                    >
                      ...
                    </motion.span>
                  )}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
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
