/**
 * API client for backend services
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080"

export interface TokenResponse {
  token: string
  room: string
  identity: string
  agent?: {
    name: string
    description: string
    greeting: string | null
  }
}

export interface ConnectionConfig {
  livekitUrl: string
  token: string
  room: string
  identity: string
  agent?: string
}

export interface AgentInfo {
  name: string
  description: string
  instructions: string
  tags: string[]
  version: string
  greeting: string | null
  modules: {
    asr: string
    llm: string
    tts: string
  }
}

export interface AgentsResponse {
  agents: AgentInfo[]
  default: string | null
  count: number
  error?: string
}

/**
 * Fetch list of available agents from the backend
 */
export async function getAgents(): Promise<AgentsResponse> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/agents`, {
      method: "GET",
      headers: {
        "Accept": "application/json",
      },
    })

    if (!response.ok) {
      const error = await response.text()
      throw new Error(`Failed to get agents: ${error}`)
    }

    return response.json()
  } catch (error) {
    console.error("Failed to fetch agents:", error)
    return {
      agents: [],
      default: null,
      count: 0,
      error: error instanceof Error ? error.message : "Unknown error",
    }
  }
}

/**
 * Fetch a LiveKit access token from the backend
 */
export async function getToken(room: string, identity: string, agent?: string): Promise<TokenResponse> {
  const params = new URLSearchParams({ room, identity })
  if (agent) {
    params.set("agent", agent)
  }

  const response = await fetch(`${API_BASE_URL}/api/token?${params}`, {
    method: "GET",
    headers: {
      "Accept": "application/json",
    },
  })

  if (!response.ok) {
    const error = await response.text()
    throw new Error(`Failed to get token: ${error}`)
  }

  return response.json()
}

/**
 * Get connection configuration for LiveKit
 */
export async function getConnectionConfig(room: string, identity: string, agent?: string): Promise<ConnectionConfig> {
  const tokenResponse = await getToken(room, identity, agent)

  // Use browser-accessible URL for LiveKit
  // In Docker, the frontend runs in the browser so needs the external URL
  const livekitUrl = process.env.NEXT_PUBLIC_LIVEKIT_URL || "ws://localhost:7880"

  return {
    livekitUrl,
    token: tokenResponse.token,
    room: tokenResponse.room,
    identity: tokenResponse.identity,
    agent: agent,
  }
}

/**
 * Check backend health
 */
export async function checkHealth(): Promise<{ healthy: boolean; status: string }> {
  try {
    const response = await fetch(`${API_BASE_URL}/healthz`)
    const data = await response.json()
    return { healthy: response.ok, status: data.status || "unknown" }
  } catch (error) {
    return { healthy: false, status: "unreachable" }
  }
}
