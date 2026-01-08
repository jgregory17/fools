"use client"

import { useState, useEffect } from "react"
import { Mic, Volume2, Video, AlertCircle } from "lucide-react"

interface DeviceSelectorProps {
  selectedMic: string
  onMicChange: (deviceId: string) => void
  showVideo?: boolean
}

interface DeviceInfo {
  deviceId: string
  label: string
}

export function DeviceSelector({ selectedMic, onMicChange, showVideo = false }: DeviceSelectorProps) {
  const [audioInputs, setAudioInputs] = useState<DeviceInfo[]>([])
  const [audioOutputs, setAudioOutputs] = useState<DeviceInfo[]>([])
  const [videoInputs, setVideoInputs] = useState<DeviceInfo[]>([])
  const [permissionError, setPermissionError] = useState<string | null>(null)

  useEffect(() => {
    async function loadDevices() {
      try {
        // Request permissions first
        await navigator.mediaDevices.getUserMedia({ audio: true })

        const devices = await navigator.mediaDevices.enumerateDevices()

        const audioIn = devices
          .filter((d) => d.kind === "audioinput")
          .map((d) => ({ deviceId: d.deviceId, label: d.label || `Microphone ${d.deviceId.slice(0, 5)}` }))

        const audioOut = devices
          .filter((d) => d.kind === "audiooutput")
          .map((d) => ({ deviceId: d.deviceId, label: d.label || `Speaker ${d.deviceId.slice(0, 5)}` }))

        const videoIn = devices
          .filter((d) => d.kind === "videoinput")
          .map((d) => ({ deviceId: d.deviceId, label: d.label || `Camera ${d.deviceId.slice(0, 5)}` }))

        setAudioInputs(audioIn)
        setAudioOutputs(audioOut)
        setVideoInputs(videoIn)

        // Auto-select first device if none selected
        if (!selectedMic && audioIn.length > 0) {
          onMicChange(audioIn[0].deviceId)
        }
      } catch (err) {
        console.error("Failed to enumerate devices:", err)
        setPermissionError("Microphone access denied. Please allow microphone access.")
      }
    }

    loadDevices()
  }, [selectedMic, onMicChange])

  if (permissionError) {
    return (
      <div className="flex items-center gap-2 p-4 bg-yellow-900/30 border border-yellow-600/50 rounded-xl text-yellow-300">
        <AlertCircle className="w-5 h-5 flex-shrink-0" />
        <span className="text-sm">{permissionError}</span>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Microphone */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-gray-300 flex items-center gap-2">
          <Mic className="w-4 h-4" />
          Microphone
        </label>
        <select
          value={selectedMic}
          onChange={(e) => onMicChange(e.target.value)}
          className="w-full px-4 py-2.5 bg-gray-800/50 border border-gray-600/50 rounded-xl text-white text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500/50 transition-all appearance-none cursor-pointer"
        >
          {audioInputs.map((device) => (
            <option key={device.deviceId} value={device.deviceId} className="bg-gray-900 text-white">
              {device.label}
            </option>
          ))}
        </select>
      </div>

      {/* Speaker (for info only) */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-gray-300 flex items-center gap-2">
          <Volume2 className="w-4 h-4" />
          Speaker
        </label>
        <select
          disabled
          className="w-full px-4 py-2.5 bg-gray-800/30 border border-gray-600/30 rounded-xl text-gray-400 text-sm appearance-none cursor-not-allowed opacity-50"
        >
          {audioOutputs.map((device) => (
            <option key={device.deviceId} value={device.deviceId} className="bg-gray-900 text-white">
              {device.label}
            </option>
          ))}
          {audioOutputs.length === 0 && (
            <option className="bg-gray-900 text-white">System default</option>
          )}
        </select>
        <p className="text-xs text-gray-400">Speaker selection is handled by your browser</p>
      </div>

      {/* Camera (placeholder for future) */}
      {showVideo && (
        <div className="space-y-2 opacity-50">
          <label className="text-sm font-medium text-muted-foreground flex items-center gap-2">
            <Video className="w-4 h-4" />
            Camera
            <span className="text-xs px-2 py-0.5 bg-white/10 rounded-full">Coming soon</span>
          </label>
          <select
            disabled
            className="w-full px-4 py-2.5 bg-white/5 border border-white/10 rounded-xl text-muted-foreground text-sm appearance-none cursor-not-allowed"
          >
            {videoInputs.map((device) => (
              <option key={device.deviceId} value={device.deviceId} className="bg-gray-900">
                {device.label}
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  )
}
