/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // Disable strict mode for LiveKit compatibility
  reactStrictMode: false,
  // Environment variables exposed to the browser
  env: {
    NEXT_PUBLIC_LIVEKIT_URL: process.env.NEXT_PUBLIC_LIVEKIT_URL,
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
    NEXT_PUBLIC_ENABLE_VIDEO: process.env.NEXT_PUBLIC_ENABLE_VIDEO,
  },
}

module.exports = nextConfig
