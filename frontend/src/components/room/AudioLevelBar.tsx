"use client"

import { useEffect, useState } from "react"
import { LocalParticipant, Track } from "livekit-client"
import { motion } from "framer-motion"

interface AudioLevelBarProps {
  participant: LocalParticipant | undefined
  isMuted: boolean
}

export function AudioLevelBar({ participant, isMuted }: AudioLevelBarProps) {
  const [level, setLevel] = useState(0)

  useEffect(() => {
    if (!participant || isMuted) {
      setLevel(0)
      return
    }

    const audioTrack = participant.getTrackPublication(Track.Source.Microphone)?.track

    if (!audioTrack) {
      return
    }

    const audioContext = new (window as any).AudioContext()
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 64
    analyser.smoothingTimeConstant = 0.7

    const mediaStream = audioTrack.mediaStream
    if (!mediaStream) return

    const source = audioContext.createMediaStreamSource(mediaStream)
    source.connect(analyser)

    const dataArray = new Uint8Array(analyser.frequencyBinCount)

    let animationId: number

    const updateLevel = () => {
      analyser.getByteFrequencyData(dataArray)
      const sum = dataArray.reduce((a, b) => a + b, 0)
      const avg = sum / dataArray.length / 255
      setLevel(Math.pow(avg, 0.7))
      animationId = requestAnimationFrame(updateLevel)
    }

    updateLevel()

    return () => {
      cancelAnimationFrame(animationId)
      source.disconnect()
      audioContext.close()
    }
  }, [participant, isMuted])

  if (level < 0.01) return null

  return (
    <motion.div
      className="fixed top-0 left-0 right-0 h-1 z-50"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25 }}
    >
      <div className="h-full bg-[#222222]">
        <motion.div
          className="h-full bg-[#ff6b35]"
          style={{ width: `${level * 100}%` }}
          transition={{ duration: 0.1 }}
        />
      </div>
    </motion.div>
  )
}
