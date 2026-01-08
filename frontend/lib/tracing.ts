/**
 * Frontend tracing utilities for correlation with backend distributed traces.
 * Ensures proper parent-child relationships across the frontend-backend boundary.
 */

import React from 'react';

interface TraceContext {
  traceId: string;
  spanId: string;
  traceFlags?: number;
  traceState?: string;
}

interface TrackedOperation {
  name: string;
  startTime: number;
  endTime?: number;
  attributes: Record<string, any>;
  events: Array<{
    name: string;
    timestamp: number;
    attributes?: Record<string, any>;
  }>;
}

/**
 * Frontend tracing client that correlates with backend OpenTelemetry traces.
 */
export class FrontendTracer {
  private currentContext: TraceContext | null = null;
  private operations: Map<string, TrackedOperation> = new Map();
  private sessionId: string;
  private userId?: string;

  constructor() {
    this.sessionId = this.generateId();
  }

  /**
   * Initialize tracing for a new session.
   */
  initSession(userId?: string) {
    this.userId = userId;
    this.sessionId = this.generateId();
    
    // Store session info for correlation
    if (typeof window !== 'undefined') {
      window.sessionStorage.setItem('trace-session-id', this.sessionId);
      if (userId) {
        window.sessionStorage.setItem('trace-user-id', userId);
      }
    }
  }

  /**
   * Extract trace context from backend response headers.
   */
  extractContextFromResponse(response: Response): TraceContext | null {
    const traceId = response.headers.get('x-trace-id');
    const spanId = response.headers.get('x-span-id');
    
    if (traceId && spanId) {
      const context: TraceContext = {
        traceId,
        spanId,
        traceFlags: parseInt(response.headers.get('x-trace-flags') || '1'),
      };
      
      // Extract baggage for additional context
      const baggageHeaders = Array.from(response.headers.entries())
        .filter(([key]) => key.startsWith('x-baggage-'));
      
      if (baggageHeaders.length > 0) {
        context.traceState = baggageHeaders
          .map(([key, value]) => `${key.substring(10)}=${value}`)
          .join(',');
      }
      
      this.currentContext = context;
      return context;
    }
    
    return null;
  }

  /**
   * Inject trace context into request headers for backend correlation.
   */
  injectContextIntoHeaders(headers: HeadersInit = {}): HeadersInit {
    const headersObj = headers instanceof Headers ? headers : new Headers(headers);
    
    if (this.currentContext) {
      // W3C Trace Context headers
      headersObj.set('traceparent', this.formatTraceParent(this.currentContext));
      
      if (this.currentContext.traceState) {
        headersObj.set('tracestate', this.currentContext.traceState);
      }
    }
    
    // Add session correlation headers
    headersObj.set('x-session-id', this.sessionId);
    if (this.userId) {
      headersObj.set('x-user-id', this.userId);
    }
    
    return headersObj;
  }

  /**
   * Inject trace context into WebSocket messages.
   */
  injectContextIntoMessage(message: any): any {
    if (typeof message === 'object' && message !== null) {
      return {
        ...message,
        __trace: {
          traceId: this.currentContext?.traceId || this.generateTraceId(),
          spanId: this.generateSpanId(),
          parentSpanId: this.currentContext?.spanId,
          sessionId: this.sessionId,
          userId: this.userId,
          timestamp: Date.now(),
        },
      };
    }
    
    return message;
  }

  /**
   * Extract trace context from WebSocket messages.
   */
  extractContextFromMessage(message: any): TraceContext | null {
    if (typeof message === 'object' && message !== null && message.__trace) {
      const trace = message.__trace;
      const context: TraceContext = {
        traceId: trace.traceId,
        spanId: trace.spanId,
      };
      
      this.currentContext = context;
      return context;
    }
    
    return null;
  }

  /**
   * Start tracking a frontend operation.
   */
  startOperation(name: string, attributes: Record<string, any> = {}): string {
    const operationId = this.generateSpanId();
    
    const operation: TrackedOperation = {
      name,
      startTime: performance.now(),
      attributes: {
        ...attributes,
        'frontend.session_id': this.sessionId,
        'frontend.user_id': this.userId,
        'frontend.operation_id': operationId,
      },
      events: [],
    };
    
    this.operations.set(operationId, operation);
    
    // Log to console in development
    if (process.env.NODE_ENV === 'development') {
      console.log(`[Trace] Started: ${name}`, {
        operationId,
        traceId: this.currentContext?.traceId,
        attributes,
      });
    }
    
    return operationId;
  }

  /**
   * End a tracked operation.
   */
  endOperation(operationId: string, status: 'ok' | 'error' = 'ok', error?: Error) {
    const operation = this.operations.get(operationId);
    
    if (operation) {
      operation.endTime = performance.now();
      const duration = operation.endTime - operation.startTime;
      
      // Send telemetry to backend
      this.sendTelemetry({
        type: 'operation',
        operation: {
          ...operation,
          duration,
          status,
          error: error?.message,
        },
        context: this.currentContext,
      });
      
      // Log in development
      if (process.env.NODE_ENV === 'development') {
        console.log(`[Trace] Ended: ${operation.name}`, {
          operationId,
          duration: `${duration.toFixed(2)}ms`,
          status,
          error: error?.message,
        });
      }
      
      this.operations.delete(operationId);
    }
  }

  /**
   * Add an event to a tracked operation.
   */
  addEvent(operationId: string, eventName: string, attributes?: Record<string, any>) {
    const operation = this.operations.get(operationId);
    
    if (operation) {
      operation.events.push({
        name: eventName,
        timestamp: performance.now(),
        attributes,
      });
      
      // Log in development
      if (process.env.NODE_ENV === 'development') {
        console.log(`[Trace] Event: ${eventName}`, {
          operationId,
          operation: operation.name,
          attributes,
        });
      }
    }
  }

  /**
   * Track a user interaction.
   */
  trackInteraction(
    action: string,
    target: string,
    metadata: Record<string, any> = {}
  ) {
    const operationId = this.startOperation(`interaction.${action}`, {
      'interaction.action': action,
      'interaction.target': target,
      ...metadata,
    });
    
    // Auto-end after a short delay (for clicks, etc.)
    setTimeout(() => {
      this.endOperation(operationId);
    }, 100);
    
    return operationId;
  }

  /**
   * Track a page navigation.
   */
  trackNavigation(from: string, to: string) {
    const operationId = this.startOperation('navigation', {
      'navigation.from': from,
      'navigation.to': to,
    });
    
    return operationId;
  }

  /**
   * Track an API call.
   */
  async trackApiCall<T>(
    method: string,
    url: string,
    fetchFn: () => Promise<T>
  ): Promise<T> {
    const operationId = this.startOperation('api.call', {
      'http.method': method,
      'http.url': url,
    });
    
    try {
      const result = await fetchFn();
      this.endOperation(operationId, 'ok');
      return result;
    } catch (error) {
      this.endOperation(operationId, 'error', error as Error);
      throw error;
    }
  }

  /**
   * Send telemetry data to backend.
   */
  private async sendTelemetry(data: any) {
    // Skip in test environment
    if (typeof window === 'undefined') {
      return;
    }
    
    try {
      // Use sendBeacon for reliability
      const payload = JSON.stringify(data);
      
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/telemetry', payload);
      } else {
        // Fallback to fetch
        fetch('/api/telemetry', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...Object.fromEntries(
              Object.entries(this.injectContextIntoHeaders({}))
                .filter(([_, v]) => typeof v === 'string')
            ),
          },
          body: payload,
          keepalive: true,
        }).catch(() => {
          // Ignore telemetry failures
        });
      }
    } catch (error) {
      // Ignore telemetry errors
      if (process.env.NODE_ENV === 'development') {
        console.warn('[Trace] Failed to send telemetry:', error);
      }
    }
  }

  /**
   * Format trace context as W3C traceparent header.
   */
  private formatTraceParent(context: TraceContext): string {
    const version = '00';
    const traceFlags = (context.traceFlags || 1).toString(16).padStart(2, '0');
    return `${version}-${context.traceId}-${context.spanId}-${traceFlags}`;
  }

  /**
   * Generate a random trace ID (32 hex chars).
   */
  private generateTraceId(): string {
    return this.generateRandomHex(32);
  }

  /**
   * Generate a random span ID (16 hex chars).
   */
  private generateSpanId(): string {
    return this.generateRandomHex(16);
  }

  /**
   * Generate a random ID.
   */
  private generateId(): string {
    return `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
  }

  /**
   * Generate random hex string of specified length.
   */
  private generateRandomHex(length: number): string {
    const bytes = new Uint8Array(length / 2);
    if (typeof window !== 'undefined' && window.crypto) {
      window.crypto.getRandomValues(bytes);
    } else {
      // Fallback for Node.js environment
      for (let i = 0; i < bytes.length; i++) {
        bytes[i] = Math.floor(Math.random() * 256);
      }
    }
    
    return Array.from(bytes)
      .map(b => b.toString(16).padStart(2, '0'))
      .join('');
  }
}

// Singleton instance
let tracerInstance: FrontendTracer | null = null;

/**
 * Get or create the frontend tracer singleton.
 */
export function getTracer(): FrontendTracer {
  if (!tracerInstance) {
    tracerInstance = new FrontendTracer();
  }
  return tracerInstance;
}

/**
 * Enhanced fetch that automatically propagates trace context.
 */
export async function tracedFetch(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<Response> {
  const tracer = getTracer();
  const url = typeof input === 'string' ? input : input.toString();
  const method = init?.method || 'GET';
  
  // Start operation
  const operationId = tracer.startOperation('fetch', {
    'http.method': method,
    'http.url': url,
  });
  
  try {
    // Inject trace context into headers
    const headers = tracer.injectContextIntoHeaders(init?.headers || {});
    
    const response = await fetch(input, {
      ...init,
      headers,
    });
    
    // Extract trace context from response
    tracer.extractContextFromResponse(response);
    
    // End operation
    tracer.endOperation(operationId, response.ok ? 'ok' : 'error');
    
    return response;
  } catch (error) {
    tracer.endOperation(operationId, 'error', error as Error);
    throw error;
  }
}

/**
 * WebSocket wrapper with automatic trace propagation.
 */
export class TracedWebSocket extends WebSocket {
  private tracer: FrontendTracer;
  private connectionOperationId?: string;

  constructor(url: string | URL, protocols?: string | string[]) {
    super(url, protocols);
    this.tracer = getTracer();
    
    // Track connection
    this.connectionOperationId = this.tracer.startOperation('websocket.connect', {
      'websocket.url': url.toString(),
    });
    
    // Set up event handlers for tracing
    this.addEventListener('open', () => {
      if (this.connectionOperationId) {
        this.tracer.endOperation(this.connectionOperationId, 'ok');
      }
    });
    
    this.addEventListener('error', (event) => {
      if (this.connectionOperationId) {
        this.tracer.endOperation(
          this.connectionOperationId,
          'error',
          new Error('WebSocket connection failed')
        );
      }
    });
  }

  /**
   * Send a message with trace context injection.
   */
  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    // Try to inject context if it's a JSON message
    if (typeof data === 'string') {
      try {
        const message = JSON.parse(data);
        const tracedMessage = this.tracer.injectContextIntoMessage(message);
        super.send(JSON.stringify(tracedMessage));
        return;
      } catch {
        // Not JSON, send as-is
      }
    }
    
    super.send(data);
  }
}

/**
 * React hook for component-level tracing.
 */
export function useTracing(componentName: string) {
  const tracer = getTracer();
  const [renderOperationId] = React.useState(() =>
    tracer.startOperation(`component.render.${componentName}`)
  );
  
  React.useEffect(() => {
    // End render operation after mount
    tracer.endOperation(renderOperationId);
    
    // Track component lifecycle
    const mountOperationId = tracer.startOperation(`component.mount.${componentName}`);
    
    return () => {
      tracer.endOperation(mountOperationId);
    };
  }, [componentName, renderOperationId, tracer]);
  
  return {
    trackInteraction: (action: string, metadata?: Record<string, any>) =>
      tracer.trackInteraction(action, componentName, metadata),
    trackEvent: (eventName: string, attributes?: Record<string, any>) =>
      tracer.addEvent(renderOperationId, eventName, attributes),
  };
}