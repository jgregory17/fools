"use client"

import { useEffect, useRef, useState, useMemo } from "react"
import { TrackReference } from "@livekit/components-react"
import { motion } from "framer-motion"

interface WaveformOrbAvatarProps {
  audioTrack: TrackReference | undefined
  agentState: string | undefined
  isSpeaking: boolean
  size?: number
}

/**
 * WaveformOrbAvatar - A futuristic animated orb that visualizes agent audio.
 *
 * Features:
 * - Pulsing neon glow when speaking
 * - Audio-reactive wave animation
 * - State-based color transitions
 */
export function WaveformOrbAvatar({
  audioTrack,
  agentState,
  isSpeaking,
  size = 160,
}: WaveformOrbAvatarProps) {
  const [audioLevel, setAudioLevel] = useState(0)
  const [wavePoints, setWavePoints] = useState<number[]>(Array(12).fill(0))
  const analyserRef = useRef<AnalyserNode | null>(null)
  const animationRef = useRef<number>(0)

  // State-based colors
  const stateColors = useMemo(() => {
    switch (agentState) {
      case "speaking":
        return {
          primary: "#00f5ff",
          secondary: "#bf00ff",
          glow: "rgba(0, 245, 255, 0.5)",
        }
      case "thinking":
        return {
          primary: "#bf00ff",
          secondary: "#ff00ff",
          glow: "rgba(191, 0, 255, 0.5)",
        }
      case "listening":
        return {
          primary: "#00ff88",
          secondary: "#00f5ff",
          glow: "rgba(0, 255, 136, 0.3)",
        }
      default:
        return {
          primary: "#666666",
          secondary: "#888888",
          glow: "rgba(100, 100, 100, 0.2)",
        }
    }
  }, [agentState])

  // Audio analysis for wave animation
  useEffect(() => {
    if (!audioTrack?.publication?.track) {
      setAudioLevel(0)
      setWavePoints(Array(12).fill(0))
      return
    }

    const track = audioTrack.publication.track
    const mediaStream = track.mediaStream

    if (!mediaStream) return

    const audioContext = new AudioContext()
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 64
    analyser.smoothingTimeConstant = 0.7
    analyserRef.current = analyser

    const source = audioContext.createMediaStreamSource(mediaStream)
    source.connect(analyser)

    const dataArray = new Uint8Array(analyser.frequencyBinCount)

    const updateVisualization = () => {
      if (!analyserRef.current) return

      analyserRef.current.getByteFrequencyData(dataArray)

      // Calculate overall level
      const sum = dataArray.reduce((a, b) => a + b, 0)
      const avg = sum / dataArray.length / 255
      setAudioLevel(Math.pow(avg, 0.7))

      // Calculate wave points (12 points around the circle)
      const points = []
      const segmentSize = Math.floor(dataArray.length / 12)
      for (let i = 0; i < 12; i++) {
        let segSum = 0
        for (let j = 0; j < segmentSize; j++) {
          segSum += dataArray[i * segmentSize + j]
        }
        points.push(Math.pow(segSum / segmentSize / 255, 0.6))
      }
      setWavePoints(points)

      animationRef.current = requestAnimationFrame(updateVisualization)
    }

    updateVisualization()

    return () => {
      cancelAnimationFrame(animationRef.current)
      source.disconnect()
      audioContext.close()
    }
  }, [audioTrack])

  // Generate wave path for SVG
  const wavePath = useMemo(() => {
    const center = size / 2
    const baseRadius = size * 0.35
    const maxWave = size * 0.08

    const points = wavePoints.map((level, i) => {
      const angle = (i / wavePoints.length) * Math.PI * 2 - Math.PI / 2
      const radius = baseRadius + level * maxWave * (isSpeaking ? 2 : 0.5)
      const x = center + Math.cos(angle) * radius
      const y = center + Math.sin(angle) * radius
      return { x, y }
    })

    // Create smooth curve through points
    let path = `M ${points[0].x} ${points[0].y}`
    for (let i = 0; i < points.length; i++) {
      const next = points[(i + 1) % points.length]
      const control1x = points[i].x + (next.x - points[i].x) / 2
      const control1y = points[i].y + (next.y - points[i].y) / 2
      path += ` Q ${control1x} ${control1y} ${next.x} ${next.y}`
    }
    path += " Z"

    return path
  }, [wavePoints, size, isSpeaking])

  return (
    <div className="relative" style={{ width: size, height: size }}>
      {/* Outer glow */}
      <motion.div
        className="absolute inset-0 rounded-full"
        animate={{
          boxShadow: isSpeaking
            ? [
                `0 0 20px ${stateColors.glow}, 0 0 40px ${stateColors.glow}, 0 0 60px ${stateColors.glow}`,
                `0 0 30px ${stateColors.glow}, 0 0 60px ${stateColors.glow}, 0 0 90px ${stateColors.glow}`,
                `0 0 20px ${stateColors.glow}, 0 0 40px ${stateColors.glow}, 0 0 60px ${stateColors.glow}`,
              ]
            : `0 0 10px ${stateColors.glow}`,
          scale: isSpeaking ? [1, 1.02, 1] : 1,
        }}
        transition={{
          duration: isSpeaking ? 0.5 : 0.3,
          repeat: isSpeaking ? Infinity : 0,
        }}
      />

      {/* Background gradient */}
      <motion.div
        className="absolute inset-4 rounded-full"
        style={{
          background: `radial-gradient(circle at 30% 30%, ${stateColors.primary}22, ${stateColors.secondary}11, transparent)`,
        }}
        animate={{
          rotate: 360,
        }}
        transition={{
          duration: 20,
          repeat: Infinity,
          ease: "linear",
        }}
      />

      {/* Main SVG */}
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="relative z-10"
      >
        <defs>
          <linearGradient id="orbGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor={stateColors.primary} />
            <stop offset="100%" stopColor={stateColors.secondary} />
          </linearGradient>
          <filter id="glow">
            <feGaussianBlur stdDeviation="3" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Waveform path */}
        <motion.path
          d={wavePath}
          fill="url(#orbGradient)"
          filter="url(#glow)"
          opacity={0.9}
          animate={{
            opacity: isSpeaking ? [0.8, 1, 0.8] : 0.7,
          }}
          transition={{
            duration: 0.5,
            repeat: isSpeaking ? Infinity : 0,
          }}
        />

        {/* Inner core */}
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={size * 0.15}
          fill={stateColors.primary}
          opacity={0.3}
          animate={{
            r: isSpeaking ? [size * 0.15, size * 0.18, size * 0.15] : size * 0.15,
            opacity: isSpeaking ? [0.3, 0.5, 0.3] : 0.3,
          }}
          transition={{
            duration: 0.3,
            repeat: isSpeaking ? Infinity : 0,
          }}
        />
      </svg>

      {/* State indicator */}
      <motion.div
        className="absolute -bottom-2 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full text-xs font-medium"
        style={{
          backgroundColor: `${stateColors.primary}20`,
          color: stateColors.primary,
          border: `1px solid ${stateColors.primary}40`,
        }}
        animate={{
          y: [0, -2, 0],
        }}
        transition={{
          duration: 2,
          repeat: Infinity,
        }}
      >
        {agentState || "offline"}
      </motion.div>
    </div>
  )
}

// Export AvatarProvider interface for future extensions
export interface AvatarProvider {
  name: string
  render: (props: WaveformOrbAvatarProps) => React.ReactNode
}

// Default avatar provider
export const defaultAvatarProvider: AvatarProvider = {
  name: "waveform-orb",
  render: (props) => <WaveformOrbAvatar {...props} />,
}
