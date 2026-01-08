"use client"

import { useEffect, useState } from "react"
import { LocalParticipant, Track } from "livekit-client"
import { motion } from "framer-motion"

interface AudioLevelMeterProps {
  participant: LocalParticipant | undefined
  isMuted: boolean
  barCount?: number
}

export function AudioLevelMeter({ participant, isMuted, barCount = 5 }: AudioLevelMeterProps) {
  const [levels, setLevels] = useState<number[]>(Array(barCount).fill(0))

  useEffect(() => {
    if (!participant || isMuted) {
      setLevels(Array(barCount).fill(0))
      return
    }

    // Get the audio track
    const audioTrack = participant.getTrackPublication(Track.Source.Microphone)?.track

    if (!audioTrack) {
      return
    }

    // Create audio context and analyser
    const audioContext = new (window as any).AudioContext()
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 64
    analyser.smoothingTimeConstant = 0.8

    // Connect the track to the analyser
    const mediaStream = audioTrack.mediaStream
    if (!mediaStream) return

    const source = audioContext.createMediaStreamSource(mediaStream)
    source.connect(analyser)

    const dataArray = new Uint8Array(analyser.frequencyBinCount)

    // Animate levels
    let animationId: number

    const updateLevels = () => {
      analyser.getByteFrequencyData(dataArray)

      // Calculate levels for each bar
      const segmentSize = Math.floor(dataArray.length / barCount)
      const newLevels = []

      for (let i = 0; i < barCount; i++) {
        let sum = 0
        for (let j = 0; j < segmentSize; j++) {
          sum += dataArray[i * segmentSize + j]
        }
        // Normalize to 0-1
        const avg = sum / segmentSize / 255
        // Apply some curve for better visual
        newLevels.push(Math.pow(avg, 0.5))
      }

      setLevels(newLevels)
      animationId = requestAnimationFrame(updateLevels)
    }

    updateLevels()

    return () => {
      cancelAnimationFrame(animationId)
      source.disconnect()
      audioContext.close()
    }
  }, [participant, isMuted, barCount])

  return (
    <div className="flex items-end gap-0.5 h-6">
      {levels.map((level, i) => (
        <motion.div
          key={i}
          className={`w-1 rounded-full ${
            isMuted ? "bg-red-500/50" : "bg-gradient-to-t from-cyan-500 to-cyan-300"
          }`}
          animate={{
            height: isMuted ? 4 : Math.max(4, level * 24),
          }}
          transition={{
            type: "spring",
            stiffness: 400,
            damping: 30,
          }}
        />
      ))}
    </div>
  )
}
