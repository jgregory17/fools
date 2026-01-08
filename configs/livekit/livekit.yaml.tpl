# LiveKit Server Configuration Template
#
# This template is processed at container startup to inject secrets
# Placeholders: {{API_KEY}} and {{API_SECRET}}

port: 7880
rtc:
  port_range_start: 50000
  port_range_end: 50010
  tcp_port: 7881
  use_external_ip: false

# API Keys (injected from Docker secrets)
keys:
  {{API_KEY}}: {{API_SECRET}}

# Logging
logging:
  level: info
  json: false

# Room settings
room:
  auto_create: true
  empty_timeout: 300
  max_participants: 10

# Turn server (for NAT traversal in dev)
turn:
  enabled: true
  udp_port: 7882
  tls_port: 0

# Webhooks (optional - for production monitoring)
# webhook:
#   urls:
#     - http://agent:8080/api/v1/webhook