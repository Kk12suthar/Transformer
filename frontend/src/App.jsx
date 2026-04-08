import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8100";
const TOKEN_KEY = "mvp_token";
const USER_KEY = "mvp_user";
const PAGE_SIZE = 50;

function parseStoredUser() {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

function toUiError(error) {
  const raw = error instanceof Error ? error.message : String(error || "");
  const firstLine = raw.split("\n").map((line) => line.trim()).find(Boolean) || "Request failed";
  return firstLine.length > 220 ? `${firstLine.slice(0, 220)}...` : firstLine;
}

async function apiRequest(path, options = {}) {
  const { method = "GET", token, body, formData, signal } = options;
  const headers = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (formData) { payload = formData; }
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const response = await fetch(`${API_BASE_URL}${path}`, { method, headers, body: payload, signal });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === "object" && data?.detail ? data.detail
      : typeof data === "string" && data ? data : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return data;
}

async function streamTransform({ token, sessionId, query, chatModel, onEvent }) {
  const response = await fetch(`${API_BASE_URL}/adk-api/transform/stream`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream", "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, query, chat_model: chatModel || undefined }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`Transformation stream failed (${response.status}). Check backend logs.`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  const emitData = (chunk) => {
    const eventText = chunk.trim();
    if (!eventText) return;
    const dataLines = eventText.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim());
    if (!dataLines.length) return;
    const payload = dataLines.join("\n");
    try { onEvent(JSON.parse(payload)); }
    catch { onEvent({ type: "log", text: payload, timestamp: new Date().toISOString() }); }
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) { emitData(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2); boundary = buffer.indexOf("\n\n"); }
  }
  if (buffer.trim()) emitData(buffer);
}

async function downloadTable({ token, sessionId, tableName }) {
  const response = await fetch(
    `${API_BASE_URL}/api/v1/tables/${encodeURIComponent(tableName)}/download?session_id=${encodeURIComponent(sessionId)}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!response.ok) { const text = await response.text(); throw new Error(text || "Download failed"); }
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = `${tableName}.csv`;
  document.body.appendChild(link); link.click(); link.remove();
  window.URL.revokeObjectURL(url);
}

function eventSummary(event) {
  return event.message || event.text || event.final_output || event?.response?.result || JSON.stringify(event);
}

function eventKind(type) {
  if (type === "error") return "err";
  if (type === "completion" || type === "final_response") return "ok";
  if (type === "function_request") return "tool-req";
  if (type === "function_response") return "tool-res";
  if (type === "agent_thinking") return "thinking";
  return "log";
}

function traceIcon(type) {
  if (type === "function_request") return <IconTool />;
  if (type === "function_response") return <IconToolResponse />;
  if (type === "agent_thinking") return <IconBrain />;
  if (type === "agent_start") return <IconAgentBot />;
  if (type === "error") return <IconErrorSmall />;
  if (type === "completion") return <IconCheck />;
  return <IconAgentBot />;
}

function traceLabel(event) {
  if (event.type === "agent_start") return "Start";
  if (event.type === "agent_thinking") {
    return event.agent_name ? `${event.agent_name}` : "Thinking";
  }
  if (event.type === "function_request") {
    return event.tool_name || "Tool call";
  }
  if (event.type === "function_response") {
    return event.tool_name || "Tool result";
  }
  if (event.type === "error") return "Error";
  if (event.type === "completion") return "Done";
  return event.type || "log";
}

function traceDetail(event) {
  if (event.type === "agent_thinking" && event.text) {
    const t = event.text.trim();
    return t.length > 220 ? t.slice(0, 220) + "â€¦" : t;
  }
  if (event.type === "function_request" && event.tool_args) {
    try {
      const args = typeof event.tool_args === "string" ? JSON.parse(event.tool_args) : event.tool_args;
      const s = JSON.stringify(args, null, 2);
      return s.length > 320 ? s.slice(0, 320) + "â€¦" : s;
    } catch { return String(event.tool_args).slice(0, 200); }
  }
  if (event.type === "function_response" && event.response) {
    try {
      const r = typeof event.response === "object" ? event.response : JSON.parse(event.response);
      const s = JSON.stringify(r, null, 2);
      return s.length > 320 ? s.slice(0, 320) + "…" : s;
    } catch { return String(event.response).slice(0, 200); }
  }
  if (event.type === "error") return event.message || "";
  return null;
}

// ── Vertical Timeline Trace ────────────────────────────────────────────────────────────────────────────────────────────

function formatTraceTime(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return ""; }
}

function traceToolName(event) {
  return event.tool_name || "Tool";
}

function compactTraceText(value, limit = 280) {
  if (value == null) return "";
  let text;
  if (typeof value === "string") {
    text = value.trim();
  } else {
    try {
      text = JSON.stringify(value, null, 2);
    } catch {
      text = String(value);
    }
  }
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function traceRequestText(event) {
  return compactTraceText(
    event.tool_args || event.text || event.message || event.request || event.content || event.response,
  );
}

function traceResponseText(event) {
  return compactTraceText(
    event.response || event.text || event.message || event.result || event.content,
  );
}

function groupTraceEvents(events) {
  const items = [];
  const pendingTools = [];

  events.forEach((event, index) => {
    if (event.type === "completion" || event.type === "final_response" || event.type === "status") {
      return;
    }

    if (event.type === "agent_thinking") {
      items.push({
        id: `${index}-thinking-${items.length}`,
        type: "thinking",
        agent_name: event.agent_name || "Agent",
        text: event.text || "",
        timestamp: event.timestamp || event.ts || null,
      });
      return;
    }

    if (event.type === "agent_start") {
      items.push({
        id: `${index}-start-${items.length}`,
        type: "agent_start",
        agent_name: event.agent_name || "Agent",
        timestamp: event.timestamp || event.ts || null,
      });
      return;
    }

    if (event.type === "function_request") {
      const item = {
        id: `${index}-${event.tool_name || "tool"}-${items.length}`,
        type: "tool_turn",
        tool_name: traceToolName(event),
        agent_name: event.agent_name || "",
        request: event,
        response: null,
        timestamp: event.timestamp || event.ts || null,
      };
      items.push(item);
      pendingTools.push(item);
      return;
    }

    if (event.type === "function_response") {
      const matched = [...pendingTools].reverse().find(
        (item) => !item.response && (!event.tool_name || item.tool_name === traceToolName(event)),
      );
      if (matched) {
        matched.response = event;
        matched.responseTimestamp = event.timestamp || event.ts || null;
        return;
      }
      return;
    }

    if (event.type === "error") {
      items.push({
        id: `${index}-error`,
        type: "error",
        message: event.message || "Stream error",
        timestamp: event.timestamp || event.ts || null,
      });
    }
  });

  return items;
}

function StreamingTraceBlock({ events, isStreaming }) {
  const [collapsed, setCollapsed] = useState(false);
  const completionEvent = events.find((e) => e.type === "completion");
  const items = groupTraceEvents(events);
  const toolCount = items.filter((item) => item.type === "tool_turn").length;

  return (
    <div className="streaming-trace-block">
      <div className="stb-header" onClick={() => setCollapsed((v) => !v)}>
        <span className="stb-icon">
          {isStreaming ? <IconSpinner /> : <IconCheck />}
        </span>
        <span className="stb-title">
          {isStreaming ? "Working…" : `Completed in ${completionEvent?.time_taken ?? "?"}s`}
        </span>
        <span className="stb-step-count">{toolCount}</span>
        <span className="stb-caret" style={{ transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)" }}>›</span>
      </div>
      {!collapsed && (
        <div className="stb-timeline">
          {items.length ? (
            items.map((item, i) => {
              const isLast = i === items.length - 1;
              const rowZ = items.length - i;
              if (item.type === "thinking") {
                return <TimelineThinkingRow key={item.id} item={item} isLast={isLast} isStreaming={isStreaming} zIndex={rowZ} />;
              }
              if (item.type === "agent_start") {
                return <TimelineAgentStartRow key={item.id} item={item} isLast={isLast} zIndex={rowZ} />;
              }
              if (item.type === "tool_turn") {
                return <TimelineToolCallRow key={item.id} item={item} isLast={isLast} isStreaming={isStreaming} zIndex={rowZ} />;
              }
              if (item.type === "error") {
                return <TimelineErrorRow key={item.id} item={item} isLast={isLast} zIndex={rowZ} />;
              }
              return null;
            })
          ) : (
            <div className="trace-empty-state">
              <IconSpinner />
              <span>Waiting for activity…</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Individual timeline rows
function TimelineAgentStartRow({ item, isLast, zIndex }) {
  return (
    <div className={`tl-row${isLast ? " tl-last" : ""}`} style={{ position: "relative", zIndex }}>
      <div className="tl-connector">
        <div className="tl-dot tl-dot-start"><IconAgentBot /></div>
        {!isLast && <div className="tl-line" />}
      </div>
      <div className="tl-content">
        <div className="tl-row-header">
          <span className="tl-label tl-label-start">{item.agent_name}</span>
          <span className="tl-time">{formatTraceTime(item.timestamp)}</span>
        </div>
      </div>
    </div>
  );
}

function TimelineThinkingRow({ item, isLast, isStreaming, zIndex }) {
  const [expanded, setExpanded] = useState(false);
  const preview = item.text ? (item.text.length > 60 ? item.text.slice(0, 60) + "…" : item.text) : "";

  return (
    <div className={`tl-row${isLast ? " tl-last" : ""}`} style={{ position: "relative", zIndex }}>
      <div className="tl-connector">
        <div className="tl-dot tl-dot-thinking"><IconBrain /></div>
        {!isLast && <div className="tl-line" />}
      </div>
      <div className="tl-content">
        <div className="tl-row-header" onClick={() => item.text && setExpanded((v) => !v)} style={{ cursor: item.text ? "pointer" : "default" }}>
          <span className="tl-label tl-label-thinking">Thinking</span>
          <span className="tl-preview">{preview}</span>
          {isLast && isStreaming && <span className="tl-live-pulse" />}
          <span className="tl-time">{formatTraceTime(item.timestamp)}</span>
          {item.text && <span className="tl-caret" style={{ transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }}>›</span>}
        </div>
        {expanded && item.text && (
          <pre className="tl-detail">{item.text}</pre>
        )}
      </div>
    </div>
  );
}

function TimelineToolCallRow({ item, isLast, isStreaming, zIndex }) {
  const [expanded, setExpanded] = useState(false);
  const running = !item.response && isStreaming;
  const requestText = traceRequestText(item.request);

  return (
    <div className={`tl-row${isLast ? " tl-last" : ""}${running ? " tl-running" : ""}`} style={{ position: "relative", zIndex }}>
      <div className="tl-connector">
        <div className={`tl-dot tl-dot-tool${running ? " tl-dot-running" : ""}`}>
          {running ? <IconSpinner /> : <IconTool />}
        </div>
        {!isLast && <div className="tl-line" />}
      </div>
      <div className="tl-content">
        <div className="tl-row-header" onClick={() => setExpanded((v) => !v)} style={{ cursor: "pointer" }}>
          <span className="tl-label tl-label-tool">{item.tool_name}</span>
          {item.agent_name && <span className="tl-badge">by {item.agent_name}</span>}
          {item.response && <span className="tl-check"><IconCheck /></span>}
          {running && <span className="tl-live-pulse" />}
          <span className="tl-time">{formatTraceTime(item.timestamp)}</span>
          <span className="tl-caret" style={{ transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }}>›</span>
        </div>
        {expanded && (
          <div className="tl-tool-detail">
            <div className="tl-tool-section">
              <span className="tl-tool-kicker">Request</span>
              <pre className="tl-tool-code">{requestText || "—"}</pre>
            </div>
            {item.response ? (
              <div className="tl-tool-section">
                <span className="tl-tool-kicker">Response</span>
                <pre className="tl-tool-code">{traceResponseText(item.response) || "—"}</pre>
              </div>
            ) : (
              <div className="tl-tool-section">
                <span className="tl-tool-kicker">Response</span>
                <div className="tl-tool-waiting"><IconSpinner /> <span>Waiting…</span></div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}



function TimelineErrorRow({ item, isLast, zIndex }) {
  return (
    <div className={`tl-row${isLast ? " tl-last" : ""}`} style={{ position: "relative", zIndex }}>
      <div className="tl-connector">
        <div className="tl-dot tl-dot-error"><IconErrorSmall /></div>
        {!isLast && <div className="tl-line" />}
      </div>
      <div className="tl-content">
        <div className="tl-row-header">
          <span className="tl-label tl-label-error">Error</span>
          <span className="tl-error-msg">{item.message}</span>
        </div>
      </div>
    </div>
  );
}

// ── icons (inline SVG) ────────────────────────────────────────────────────── â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const IconPlus = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <line x1="7" y1="1" x2="7" y2="13" /><line x1="1" y1="7" x2="13" y2="7" />
  </svg>
);
const IconTrash = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="1 3.5 13 3.5" /><path d="M5.5 3.5V2.5a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1M3 3.5l.7 8a1 1 0 0 0 1 .9h4.6a1 1 0 0 0 1-.9l.7-8" />
  </svg>
);
const IconDownload = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M7 1v8M4.5 6.5 7 9l2.5-2.5" /><path d="M2 11h10" />
  </svg>
);
const IconUpload = () => (
  <svg width="15" height="15" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M7 9V1M4.5 3.5 7 1l2.5 2.5" /><path d="M2 11h10" />
  </svg>
);
const IconSettings = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.65 1.65 0 0 0 15 19.4a1.65 1.65 0 0 0-1 .6 1.65 1.65 0 0 0-.33 1V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-.33-1 1.65 1.65 0 0 0-1-.6 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-.6-1 1.65 1.65 0 0 0-1-.33H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1-.33 1.65 1.65 0 0 0 .6-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-.6 1.65 1.65 0 0 0 .33-1V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 .33 1 1.65 1.65 0 0 0 1 .6 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9c.23.3.49.53.82.6H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1 .33 1.65 1.65 0 0 0-.51 1.07z" />
  </svg>
);
const IconChevron = ({ open }) => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    style={{ transform: open ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }}>
    <polyline points="2 5 7 10 12 5" />
  </svg>
);
const IconSend = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2 1 7.5 6 8.5M14 2 8.5 15 6 8.5M14 2 6 8.5" />
  </svg>
);
const IconCollapseLeft = ({ collapsed }) => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
    style={{ transform: collapsed ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.25s" }}>
    <polyline points="9 2 4 7 9 12" />
  </svg>
);
const IconTable = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <rect x="1" y="1" width="12" height="12" rx="1.5" />
    <line x1="1" y1="5" x2="13" y2="5" />
    <line x1="1" y1="9" x2="13" y2="9" />
    <line x1="5" y1="5" x2="5" y2="13" />
  </svg>
);
const IconSpinner = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    style={{ animation: "spin 0.9s linear infinite", display: "inline-block" }}>
    <path d="M7 1a6 6 0 1 0 6 6" opacity="0.3" />
    <path d="M7 1a6 6 0 0 1 6 6" />
  </svg>
);
const IconBrain = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9.5 2a2.5 2.5 0 0 1 5 0M12 2v4M9.5 6C6.46 6 4 8.46 4 11.5c0 1.4.52 2.67 1.37 3.63A4.5 4.5 0 0 0 7.5 21h9a4.5 4.5 0 0 0 2.13-8.37A7.5 7.5 0 0 0 14.5 6H9.5z" />
    <line x1="12" y1="12" x2="12" y2="16" />
  </svg>
);
const IconTool = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.77 3.77z" />
  </svg>
);
const IconToolResponse = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);
const IconAgentBot = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="11" width="18" height="11" rx="2" />
    <path d="M12 2a3 3 0 0 1 3 3v6H9V5a3 3 0 0 1 3-3z" />
    <line x1="8" y1="16" x2="8" y2="16" strokeWidth="2" />
    <line x1="16" y1="16" x2="16" y2="16" strokeWidth="2" />
  </svg>
);
const IconCheck = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <polyline points="2 7 5.5 10.5 12 4" />
  </svg>
);
const IconErrorSmall = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <line x1="7" y1="2" x2="7" y2="8" /><circle cx="7" cy="11" r="0.7" fill="currentColor" stroke="none" />
  </svg>
);

// â”€â”€ Upload Modal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function UploadModal({ show, onClose, selectedSessionId, token, onUploaded }) {
  const [fileToUpload, setFileToUpload] = useState(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadStatus, setUploadStatus] = useState("");
  const [dragOver, setDragOver] = useState(false);

  const handleFile = (f) => { setFileToUpload(f); setUploadError(""); setUploadStatus(""); };
  const onDrop = (e) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files?.[0]; if (f) handleFile(f); };

  async function submit() {
    if (!selectedSessionId || !fileToUpload || !token) return;
    setUploadBusy(true); setUploadError(""); setUploadStatus("");
    try {
      const formData = new FormData();
      formData.append("session_id", selectedSessionId);
      formData.append("file", fileToUpload);
      const res = await apiRequest("/api/v1/upload/files", { method: "POST", token, formData });
      setUploadStatus(res.message || "Uploaded successfully.");
      setFileToUpload(null);
      onUploaded(res.table_name);
    } catch (err) { setUploadError(toUiError(err)); }
    finally { setUploadBusy(false); }
  }

  if (!show) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Upload Table</h3>
          <button className="modal-close" onClick={onClose}>âœ•</button>
        </div>
        <p style={{ color: "var(--text-1)", fontSize: "0.88rem" }}>
          CSV, XLS, or XLSX â€” file is stored as a session table in Postgres.
        </p>
        <div
          className={`drop-zone${dragOver ? " drag-over" : ""}${fileToUpload ? " has-file" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => document.getElementById("modal-file-input").click()}
        >
          <input id="modal-file-input" type="file" accept=".csv,.xlsx,.xls"
            onChange={(e) => handleFile(e.target.files?.[0] || null)} style={{ display: "none" }} />
          <span className="drop-icon"><IconUpload /></span>
          <span className="drop-label">
            {fileToUpload ? fileToUpload.name : "Drag & drop or click to choose file"}
          </span>
          {fileToUpload && <span className="drop-sub">{(fileToUpload.size / 1024).toFixed(1)} KB</span>}
        </div>
        {uploadError && <div className="banner is-error">{uploadError}</div>}
        {uploadStatus && <div className="banner is-success">{uploadStatus}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={submit} disabled={!fileToUpload || uploadBusy || !selectedSessionId}>
            {uploadBusy ? <><IconSpinner /> Uploadingâ€¦</> : "Upload"}
          </button>
        </div>
      </div>
    </div>
  );
}

// â”€â”€ Main App â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function ModelConfigModal({ show, onClose, initialConfig, onSave, saving }) {
  const [providerKeys, setProviderKeys] = useState({ google: "", openai: "", anthropic: "" });
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [newModelName, setNewModelName] = useState("");
  const [newModelType, setNewModelType] = useState("google");
  
  const [newProviderName, setNewProviderName] = useState("");
  const [localError, setLocalError] = useState("");

  useEffect(() => {
    if (!show) return;
    const config = initialConfig || {};
    const keys = config.provider_keys || {};
    
    // Merge existing keys with defaults
    const mergedKeys = { google: "", openai: "", anthropic: "", ...keys };
    
    const nextModels = Array.isArray(config.all_models) && config.all_models.length
      ? config.all_models
      : [{ model_name: "gemini-2.5-flash", model_type: "google" }];
    const nextSelected = config.selected_model && nextModels.some((m) => m.model_name === config.selected_model)
      ? config.selected_model
      : nextModels[0].model_name;

    setProviderKeys(mergedKeys);
    setModels(nextModels);
    setSelectedModel(nextSelected);
    setNewModelName("");
    setNewModelType(Object.keys(mergedKeys)[0] || "google");
    setNewProviderName("");
    setLocalError("");
  }, [show, initialConfig]);

  if (!show) return null;

  function addModel() {
    const modelName = newModelName.trim();
    if (!modelName) return;
    const exists = models.some(
      (model) =>
        model.model_name.toLowerCase() === modelName.toLowerCase(),
    );
    if (exists) {
      setLocalError("This model is already added.");
      return;
    }
    const updated = [...models, { model_name: modelName, model_type: newModelType }];
    setModels(updated);
    if (!selectedModel) setSelectedModel(modelName);
    setNewModelName("");
    setLocalError("");
  }

  function removeModel(modelName, modelType) {
    const updated = models.filter(
      (model) => !(model.model_name === modelName && model.model_type === modelType),
    );
    if (!updated.length) {
      setLocalError("At least one model is required.");
      return;
    }
    setModels(updated);
    if (!updated.some((model) => model.model_name === selectedModel)) {
      setSelectedModel(updated[0].model_name);
    }
  }

  function addProvider() {
    const providerName = newProviderName.trim().toLowerCase();
    if (!providerName) return;
    if (Object.prototype.hasOwnProperty.call(providerKeys, providerName)) {
      setLocalError("This provider is already added.");
      return;
    }
    setProviderKeys(prev => ({ ...prev, [providerName]: "" }));
    setNewProviderName("");
    setNewModelType(providerName);
    setLocalError("");
  }

  function removeProvider(providerName) {
    const keys = { ...providerKeys };
    delete keys[providerName];
    setProviderKeys(keys);
    
    // Also remove models associated with this provider
    const updatedModels = models.filter(m => m.model_type !== providerName);
    if (!updatedModels.length) {
       setModels([]);
       setSelectedModel("");
    } else {
       setModels(updatedModels);
       if (!updatedModels.some(m => m.model_name === selectedModel)) {
         setSelectedModel(updatedModels[0].model_name);
       }
    }
  }

  async function saveSettings() {
    if (!models.length) {
      setLocalError("Add at least one model.");
      return;
    }

    let finalSelected = selectedModel;
    if (!finalSelected || !models.some((model) => model.model_name === finalSelected)) {
      finalSelected = models[0].model_name;
    }

    setLocalError("");
    await onSave({
      provider_keys: providerKeys,
      all_models: models,
      selected_model: finalSelected,
    });
  }

  const providerList = Object.keys(providerKeys);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card model-modal" onClick={(e) => e.stopPropagation()} style={{ width: 'min(960px, calc(100vw - 32px))' }}>
        <div className="modal-head">
          <h3>Agent Panel Configuration</h3>
          <button className="modal-close" onClick={onClose}>X</button>
        </div>

        <div className="agent-panels-container">
          <div className="agent-panel">
            <div className="section-head"><IconSettings /><span>Providers</span></div>
            <div className="provider-list">
              {providerList.map((provider) => (
                <div key={provider} className="provider-row">
                  <div className="provider-header">
                    <span style={{textTransform: 'capitalize', fontSize: '0.86rem', color: 'var(--text-0)'}}>{provider}</span>
                    {!["google", "openai", "anthropic"].includes(provider) && (
                      <button className="session-del" onClick={() => removeProvider(provider)} title="Remove provider" style={{ opacity: 1 }}><IconTrash /></button>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                    <input
                      type="password"
                      value={providerKeys[provider]}
                      onChange={(e) => setProviderKeys((prev) => ({ ...prev, [provider]: e.target.value }))}
                      placeholder={`${provider} API Key`}
                      autoComplete="off"
                      style={{ flex: 1, marginTop: 0 }}
                    />
                    <button 
                      className="btn-ghost" 
                      onClick={() => saveSettings()} 
                      disabled={saving}
                      title={`Save ${provider} settings`}
                      style={{ padding: '0 12px', height: 'auto' }}
                    >
                      Save
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <div className="add-provider-row" style={{ marginTop: 'auto', paddingTop: '12px' }}>
               <input
                 type="text"
                 placeholder="New provider name"
                 value={newProviderName}
                 onChange={(e) => setNewProviderName(e.target.value)}
               />
               <button className="btn-ghost" onClick={addProvider} style={{ whiteSpace: 'nowrap' }}>
                 <IconPlus /> Add Provider
               </button>
            </div>
          </div>

          <div className="agent-panel">
            <div className="section-head"><IconTable /><span>Models</span></div>
            <div className="model-list">
              {models.map((model) => (
                <div key={`${model.model_name}-${model.model_type}`} className="model-row">
                  <div className="model-choice">
                    <span>{model.model_name}</span>
                    <small>{model.model_type}</small>
                  </div>
                  <button
                    className="session-del"
                    onClick={() => removeModel(model.model_name, model.model_type)}
                    title="Remove model"
                  >
                    <IconTrash />
                  </button>
                </div>
              ))}
            </div>

            <div className="add-model-row" style={{ marginTop: 'auto', paddingTop: '12px' }}>
              <input
                type="text"
                placeholder="Model name"
                value={newModelName}
                onChange={(e) => setNewModelName(e.target.value)}
              />
              <select
                value={newModelType}
                onChange={(e) => setNewModelType(e.target.value)}
                className="model-select"
              >
                {providerList.map(p => (
                  <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
                ))}
              </select>
              <button className="btn-ghost" onClick={addModel}>
                <IconPlus /> Add
              </button>
            </div>
          </div>
        </div>

        {localError && <div className="banner is-error">{localError}</div>}

        <div className="modal-actions" style={{ paddingTop: '8px' }}>
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={saveSettings} disabled={saving}>
            {saving ? <><IconSpinner /> Saving...</> : "Save Settings"}
          </button>
        </div>
      </div>
    </div>
  );
}

function LandingPage({ onGetStarted }) {
  return (
    <div className="landing-wrap">
      <div className="ambient ambient-a" />
      <div className="ambient ambient-b" />
      
      <header className="landing-nav">
        <div className="brand">
          <svg width="32" height="32" viewBox="0 0 28 28" fill="none">
            <rect width="28" height="28" rx="8" fill="url(#ag3)" />
            <path d="M14 3 L5 15 L13 15 L14 25 L23 13 L15 13 Z" fill="#fff" />
            <defs><linearGradient id="ag3" x1="0" y1="0" x2="28" y2="28" gradientUnits="userSpaceOnUse"><stop stopColor="#6C63FF" /><stop offset="1" stopColor="#00C6FF" /></linearGradient></defs>
          </svg>
          <span className="brand-name" style={{fontSize: "1.4rem"}}>Transformer</span>
        </div>
        <button className="btn-ghost" onClick={onGetStarted}>Sign In</button>
      </header>

      <main className="landing-main">
        <section className="hero-section">
          <h1 className="hero-title">Welcome to <span className="text-gradient">Transformer</span></h1>
          <p className="hero-sub">The ultimate Agentic table cleaning & transformation workspace.</p>
          <button className="btn-primary main-cta" onClick={onGetStarted}>Get Started for Free</button>
        </section>

        <section className="grid-section">
          <div className="glass-card">
            <h3>⚙️ How It Works</h3>
            <p>Upload your messy CSVs and Excels. Our autonomous AI pipelines instantly parse, clean, and transform columns exactly how you instruct them via plain English.</p>
          </div>
          <div className="glass-card">
            <h3>🚀 The Impact</h3>
            <p>Turn hours of manual spreadsheet formatting into seconds of automated processing. Focus on the insights, let Transformer handle the data janitorial work securely.</p>
          </div>
          <div className="glass-card">
            <h3>💻 Tech Stack</h3>
            <p>Built for scale with <strong>React + Vite</strong> on the frontend, powered by <strong>Python FastAPI</strong> and <strong>SQLAlchemy</strong> on the backend. Data resides safely in <strong>PostgreSQL</strong> via an MCP Agent interface.</p>
          </div>
        </section>
      </main>
    </div>
  );
}

export default function App() {
  const [showLanding, setShowLanding] = useState(true);
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || "");
  const [user, setUser] = useState(() => parseStoredUser());
  const [authMode, setAuthMode] = useState("signin");
  const [authForm, setAuthForm] = useState({ email: "", password: "", full_name: "" });
  const [authBusy, setAuthBusy] = useState(false);

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sessions, setSessions] = useState([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [tables, setTables] = useState([]);
  const [selectedTableName, setSelectedTableName] = useState("");
  const [previewData, setPreviewData] = useState(null);
  const [previewPage, setPreviewPage] = useState(1);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [hasMorePreview, setHasMorePreview] = useState(false);

  const [showUpload, setShowUpload] = useState(false);
  const [showModelConfig, setShowModelConfig] = useState(false);
  const [modelConfig, setModelConfig] = useState({
    provider_keys: { google: "", openai: "", anthropic: "" },
    all_models: [{ model_name: "gemini-2.5-flash", model_type: "google" }],
    selected_model: "gemini-2.5-flash",
  });
  const [chatModel, setChatModel] = useState("gemini-2.5-flash");
  const [modelConfigSaving, setModelConfigSaving] = useState(false);
  const [query, setQuery] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [events, setEvents] = useState([]);
  const [chatMessages, setChatMessages] = useState([]);
  const [latestOutputTable, setLatestOutputTable] = useState("");

  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const chatScrollRef = useRef(null);
  const traceScrollRef = useRef(null);
  const wasCompactRef = useRef(false);
  const canRun = Boolean(selectedSessionId && query.trim() && !isStreaming && chatModel);
  const [uploadPendingSession, setUploadPendingSession] = useState(false);
  const [isCompactLayout, setIsCompactLayout] = useState(
    () => window.matchMedia("(max-width: 860px)").matches,
  );

  const activeSession = useMemo(() => sessions.find((s) => s.id === selectedSessionId) || null, [sessions, selectedSessionId]);

  useEffect(() => {
    if (!token) return;
    refreshSessions();
    refreshModelConfig();
  }, [token]);
  useEffect(() => { if (!selectedSessionId || !token) return; refreshTables(selectedSessionId); }, [selectedSessionId, token]);
  useEffect(() => {
    const availableModels = modelConfig?.all_models || [];
    if (!availableModels.length) {
      setChatModel("");
      return;
    }
    if (!chatModel || !availableModels.some((model) => model.model_name === chatModel)) {
      setChatModel(modelConfig.selected_model || availableModels[0].model_name);
    }
  }, [modelConfig, chatModel]);
  useEffect(() => {
    const container = chatScrollRef.current;
    if (!container) return;
    container.scrollTop = container.scrollHeight;
  }, [chatMessages, isStreaming]);
  useEffect(() => {
    const container = traceScrollRef.current;
    if (!container) return;
    container.scrollTop = container.scrollHeight;
  }, [events]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 860px)");
    const handleMediaChange = (event) => setIsCompactLayout(event.matches);
    setIsCompactLayout(media.matches);
    if (media.addEventListener) media.addEventListener("change", handleMediaChange);
    else media.addListener(handleMediaChange);
    return () => {
      if (media.removeEventListener) media.removeEventListener("change", handleMediaChange);
      else media.removeListener(handleMediaChange);
    };
  }, []);
  useEffect(() => {
    if (isCompactLayout) {
      setSidebarOpen(false);
    } else if (wasCompactRef.current) {
      setSidebarOpen(true);
    }
    wasCompactRef.current = isCompactLayout;
  }, [isCompactLayout]);
  useEffect(() => {
    if (!error && !status) return;
    const timer = setTimeout(() => {
      setError("");
      setStatus("");
    }, 4000);
    return () => clearTimeout(timer);
  }, [error, status]);

  async function refreshSessions() {
    try {
      const data = await apiRequest("/api/v1/chat/sessions", { token });
      setSessions(data);
      if (!selectedSessionId && data.length) setSelectedSessionId(data[0].id);
    } catch (err) { setError(toUiError(err)); }
  }

  async function refreshModelConfig() {
    try {
      const config = await apiRequest("/api/v1/model-config/chat", { token });
      setModelConfig(config);
      const selected = config.selected_model || config.all_models?.[0]?.model_name || "";
      setChatModel(selected);
    } catch (err) {
      setError(toUiError(err));
    }
  }

  async function saveModelConfig(nextConfig) {
    setModelConfigSaving(true);
    try {
      const config = await apiRequest("/api/v1/model-config/chat", {
        method: "PUT",
        token,
        body: nextConfig,
      });
      setModelConfig(config);
      const selected = config.selected_model || config.all_models?.[0]?.model_name || "";
      setChatModel(selected);
      setShowModelConfig(false);
      setStatus("Agent settings updated.");
    } catch (err) {
      setError(toUiError(err));
    } finally {
      setModelConfigSaving(false);
    }
  }

  async function refreshTables(sessionId) {
    try {
      const data = await apiRequest(`/api/v1/chat/sessions/${sessionId}/tables`, { token });
      setTables(data);
      if (data.length && !data.some((t) => t.table_name === selectedTableName)) {
        setSelectedTableName(data[0].table_name);
        loadPreview(data[0].table_name, sessionId, 1);
      }
      if (!data.length) { setSelectedTableName(""); setPreviewData(null); }
    } catch (err) { setError(toUiError(err)); }
  }

  async function loadPreview(tableName, sessionId = selectedSessionId, page = 1) {
    setPreviewLoading(true);
    try {
      const data = await apiRequest(
        `/api/v1/tables/${encodeURIComponent(tableName)}/preview?session_id=${encodeURIComponent(sessionId)}&page=${page}&limit=${PAGE_SIZE}`,
        { token },
      );
      const incomingRows = data.rows?.length || 0;
      const loadedBefore = page === 1 ? 0 : (previewData?.rows?.length || 0);
      const loadedRows = loadedBefore + incomingRows;
      const totalRows = Number(data.total ?? loadedRows);
      if (page === 1) {
        setPreviewData(data);
      } else {
        setPreviewData((prev) => prev ? { ...prev, rows: [...prev.rows, ...data.rows] } : data);
      }
      setSelectedTableName(tableName);
      setPreviewPage(page);
      setHasMorePreview(loadedRows < totalRows);
    } catch (err) { setError(toUiError(err)); }
    finally { setPreviewLoading(false); }
  }

  async function deleteSession(id, e) {
    e.stopPropagation();
    try {
      await apiRequest(`/api/v1/chat/sessions/${id}`, { method: "DELETE", token });
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (selectedSessionId === id) {
        const remaining = sessions.filter((s) => s.id !== id);
        setSelectedSessionId(remaining[0]?.id || "");
        setTables([]); setPreviewData(null); setEvents([]); setChatMessages([]);
      }
    } catch (err) { setError(toUiError(err)); }
  }

  async function handleAuthSubmit(event) {
    event.preventDefault(); setError(""); setAuthBusy(true);
    try {
      const path = authMode === "signin" ? "/api/v1/auth/signin" : "/api/v1/auth/signup";
      const payload = authMode === "signin"
        ? { email: authForm.email, password: authForm.password }
        : { email: authForm.email, password: authForm.password, full_name: authForm.full_name };
      const response = await apiRequest(path, { method: "POST", body: payload });
      localStorage.setItem(TOKEN_KEY, response.access_token);
      localStorage.setItem(USER_KEY, JSON.stringify(response.user));
      setToken(response.access_token); setUser(response.user);
      setAuthForm({ email: "", password: "", full_name: "" });
    } catch (err) { setError(toUiError(err)); }
    finally { setAuthBusy(false); }
  }

  async function createSession() {
    if (!token) return; setError("");
    try {
      const session = await apiRequest("/api/v1/chat/sessions", { method: "POST", token, body: {} });
      setSessions((prev) => [session, ...prev]);
      setSelectedSessionId(session.id);
      setTables([]); setPreviewData(null); setChatMessages([]); setEvents([]);
    } catch (err) { setError(toUiError(err)); }
  }

  async function openUpload() {
    // If no session yet, create one first then open modal
    if (!selectedSessionId) {
      setUploadPendingSession(true);
      await createSession();
      setUploadPendingSession(false);
    }
    setShowUpload(true);
  }

  function onUploaded(tableName) {
    refreshTables(selectedSessionId);
    if (tableName) loadPreview(tableName, selectedSessionId, 1);
    setShowUpload(false);
    setStatus("Table uploaded successfully.");
  }

  async function handleRunTransform() {
    if (!canRun) return;
    setError(""); setStatus(""); setLatestOutputTable("");
    setEvents([]);
    const userMsg = { role: "user", text: query.trim(), ts: Date.now() };
    // Immediately push user message + a live-trace placeholder into chat
    const traceId = `trace-${Date.now()}`;
    setChatMessages((prev) => [...prev, userMsg, { role: "trace", traceId, ts: Date.now() }]);
    setQuery("");
    setIsStreaming(true);
    let outputTableFromRun = "";
    let finalText = "";
    let streamFailed = false;
    let streamError = "";
    let allEvents = [];

    try {
      const activation = await apiRequest("/adk-api/transform/activate", {
        method: "POST", token, body: { session_id: selectedSessionId },
      });
      if (activation.status !== "ready") throw new Error(activation.message || "Transformation is locked by another user.");

      await streamTransform({
        token, sessionId: selectedSessionId, query: userMsg.text, chatModel,
        onEvent: (event) => {
          allEvents = [...allEvents, event];
          setEvents([...allEvents]);
          if (event.type === "final_response" && event.text) finalText = event.text;
          if (event.type === "completion") {
            if (event.success === false) {
              streamFailed = true;
              streamError = event.error || "Transformation failed";
            }
            if (event.final_output) finalText = event.final_output;
            if (event.table_name) { outputTableFromRun = event.table_name; setLatestOutputTable(event.table_name); }
          }
          if (event.type === "error") {
            streamFailed = true;
            streamError = event.message || "Stream error";
          }
        },
      });

      if (streamFailed) {
        throw new Error(streamError || "Transformation failed");
      }

      // Freeze the trace snapshot into the chat message, then push the final response
      setChatMessages((prev) => prev.map((m) =>
        m.role === "trace" && m.traceId === traceId
          ? { ...m, frozenEvents: allEvents }
          : m
      ));
      if (finalText) {
        setChatMessages((prev) => [...prev, { role: "agent", text: finalText, ts: Date.now(), table: outputTableFromRun }]);
      }
      await refreshTables(selectedSessionId);
      if (outputTableFromRun) await loadPreview(outputTableFromRun, selectedSessionId, 1);
      setStatus("Transformation completed.");
    } catch (err) {
      setError(toUiError(err));
      // Freeze trace even on error
      setChatMessages((prev) => prev.map((m) =>
        m.role === "trace" && m.traceId === traceId
          ? { ...m, frozenEvents: allEvents }
          : m
      ));
    }
    finally {
      setIsStreaming(false);
      setEvents([]);
      try { await apiRequest("/adk-api/transform/deactivate", { method: "POST", token, body: { session_id: selectedSessionId } }); } catch { /* best-effort */ }
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY);
    setToken(""); setUser(null); setSessions([]); setSelectedSessionId(""); setTables([]);
    setSelectedTableName(""); setPreviewData(null); setEvents([]); setChatMessages([]);
    setLatestOutputTable(""); setError(""); setStatus("");
    setShowModelConfig(false);
    setModelConfig({
      provider_keys: { google: "", openai: "", anthropic: "" },
      all_models: [{ model_name: "gemini-2.5-flash", model_type: "google" }],
      selected_model: "gemini-2.5-flash",
    });
    setChatModel("gemini-2.5-flash");
    setModelConfigSaving(false);
  }

  // â”€â”€ Auth screen â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  if (!token) {
    if (showLanding) {
      return <LandingPage onGetStarted={() => setShowLanding(false)} />;
    }

    return (
      <div className="auth-shell">
        <div className="ambient ambient-a" /><div className="ambient ambient-b" />
        <form className="auth-card fade-in" onSubmit={handleAuthSubmit}>
          <div className="auth-logo" onClick={() => setShowLanding(true)} style={{cursor: 'pointer'}} title="Return to Home">
            <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
              <rect width="28" height="28" rx="8" fill="url(#ag)" />
              <path d="M14 3 L5 15 L13 15 L14 25 L23 13 L15 13 Z" fill="#fff" />
              <defs><linearGradient id="ag" x1="0" y1="0" x2="28" y2="28" gradientUnits="userSpaceOnUse">
                <stop stopColor="#6C63FF" /><stop offset="1" stopColor="#00C6FF" />
              </linearGradient></defs>
            </svg>
            <h1>Transformer</h1>
          </div>
          <p>Agentic table cleaning &amp; transformation workspace.</p>
          <div className="auth-tabs">
            <button type="button" className={authMode === "signin" ? "tab is-active" : "tab"} onClick={() => setAuthMode("signin")}>Sign In</button>
            <button type="button" className={authMode === "signup" ? "tab is-active" : "tab"} onClick={() => setAuthMode("signup")}>Sign Up</button>
          </div>
          <label>Email<input type="email" required value={authForm.email} onChange={(e) => setAuthForm((p) => ({ ...p, email: e.target.value }))} /></label>
          {authMode === "signup" && <label>Full Name<input type="text" minLength={2} required value={authForm.full_name} onChange={(e) => setAuthForm((p) => ({ ...p, full_name: e.target.value }))} /></label>}
          <label>Password<input type="password" minLength={8} required value={authForm.password} onChange={(e) => setAuthForm((p) => ({ ...p, password: e.target.value }))} /></label>
          {error && <div className="banner is-error">{error}</div>}
          <button className="btn-primary auth-submit" type="submit" disabled={authBusy}>
            {authBusy ? "Please wait…" : authMode === "signin" ? "Enter Workspace" : "Create Account"}
          </button>
        </form>
      </div>
    );
  }

  // â”€â”€ Workspace â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  return (
    <div className={`workspace-shell${sidebarOpen ? "" : " sidebar-collapsed"}`}>
      <div className="ambient ambient-a" /><div className="ambient ambient-b" />

      <UploadModal
        show={showUpload}
        onClose={() => setShowUpload(false)}
        selectedSessionId={selectedSessionId}
        token={token}
        onUploaded={onUploaded}
      />

      <ModelConfigModal
        show={showModelConfig}
        onClose={() => setShowModelConfig(false)}
        initialConfig={modelConfig}
        onSave={saveModelConfig}
        saving={modelConfigSaving}
      />

      {/* â”€â”€ Sidebar â”€â”€ */}
      <aside className="sidebar">
        <div className="sidebar-head">
          <div className="brand">
            <svg width="20" height="20" viewBox="0 0 28 28" fill="none">
              <rect width="28" height="28" rx="8" fill="url(#ag2)" />
              <path d="M14 3 L5 15 L13 15 L14 25 L23 13 L15 13 Z" fill="#fff" />
              <defs><linearGradient id="ag2" x1="0" y1="0" x2="28" y2="28" gradientUnits="userSpaceOnUse">
                <stop stopColor="#6C63FF" /><stop offset="1" stopColor="#00C6FF" />
              </linearGradient></defs>
            </svg>
            {sidebarOpen && <span className="brand-name">Transformer</span>}
          </div>
          <button className="collapse-btn" onClick={() => setSidebarOpen((o) => !o)} title={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}>
            <IconCollapseLeft collapsed={!sidebarOpen} />
          </button>
        </div>

        <button className="new-session-btn" onClick={createSession} title="New Chat Session">
          <IconPlus />
          {sidebarOpen && <span>New Session</span>}
        </button>

        <div className="session-list">
          {sessions.length ? sessions.map((session) => (
            <div key={session.id} className={`session-item${session.id === selectedSessionId ? " is-active" : ""}`}
              onClick={() => setSelectedSessionId(session.id)} title={session.title}>
              {sidebarOpen ? (
                <>
                  <div className="session-info">
                    <span className="session-title">{session.title}</span>
                    <small className="session-time">{new Date(session.updated_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</small>
                  </div>
                  <button className="session-del" onClick={(e) => deleteSession(session.id, e)} title="Delete session">
                    <IconTrash />
                  </button>
                </>
              ) : (
                <div className="session-dot" />
              )}
            </div>
          )) : (
            sidebarOpen && <div className="empty-state" style={{ fontSize: "0.82rem" }}>No sessions yet.</div>
          )}
        </div>

        <div className="sidebar-foot">
          {sidebarOpen ? (
            <>
              <div className="user-chip">
                <div className="user-avatar">{(user?.full_name || "U")[0].toUpperCase()}</div>
                <div><strong>{user?.full_name || "User"}</strong><span>{user?.email}</span></div>
              </div>
              <button className="btn-ghost btn-block" onClick={logout}>Sign Out</button>
            </>
          ) : (
            <button className="btn-ghost avatar-only" onClick={logout} title="Sign Out">
              <div className="user-avatar sm">{(user?.full_name || "U")[0].toUpperCase()}</div>
            </button>
          )}
        </div>
      </aside>

      {/* â”€â”€ Main â”€â”€ */}
      <main className="main-pane">

        {/* Topbar */}
        <header className="topbar">
          <div className="topbar-left">
            <h1 className="session-heading">{activeSession?.title || "Select a session"}</h1>
            <div className={`status-pill${isStreaming ? " live" : ""}`}>
              {isStreaming ? <><IconSpinner /> Streaming</> : "Idle"}
            </div>
          </div>
          <div className="topbar-actions">
            {error && <div className="banner is-error topbar-banner">{error}<button className="banner-close" onClick={() => setError("")}>âœ•</button></div>}
            {status && <div className="banner is-success topbar-banner">{status}</div>}
            <button className="btn-upload" onClick={() => setShowModelConfig(true)} title="Agent panel">
              <IconSettings /> Agent Panel
            </button>
            <button className="btn-upload" onClick={openUpload} disabled={uploadPendingSession} title="Upload table">
              <IconUpload /> Upload Table
            </button>
          </div>
        </header>

        {/* Split content */}
        <div className="split-pane">

          {/* â”€â”€ LEFT: Data Panel â”€â”€ */}
          <section className="data-panel">

            {/* Table cards */}
            <div className="data-section">
              <div className="section-head"><IconTable /><span>Session Tables</span></div>
              <div className="table-cards">
                {tables.length ? tables.map((table) => (
                  <div
                    key={table.id}
                    className={`table-card${table.table_name === selectedTableName ? " is-selected" : ""}`}
                    onClick={() => { setSelectedTableName(table.table_name); loadPreview(table.table_name, selectedSessionId, 1); }}
                  >
                    <div className="tc-info">
                      <span className="tc-name">{table.table_name}</span>
                      <span className="tc-role">{table.table_role}</span>
                    </div>
                    <div className="tc-actions">
                      <button className="tc-btn" title="Download CSV"
                        onClick={(e) => { e.stopPropagation(); downloadTable({ token, sessionId: selectedSessionId, tableName: table.table_name }).catch((err) => setError(toUiError(err))); }}>
                        <IconDownload />
                      </button>
                    </div>
                  </div>
                )) : <div className="empty-state">No tables in this session yet.</div>}
              </div>
            </div>

            {/* Preview */}
            <div className="data-section preview-section">
              <div className="section-head">
                <IconTable />
                <span>Preview{selectedTableName ? ` â€” ${selectedTableName}` : ""}</span>
                {selectedTableName && latestOutputTable === selectedTableName && (
                  <button className="tc-btn" style={{ marginLeft: "auto" }} title="Download CSV"
                    onClick={() => downloadTable({ token, sessionId: selectedSessionId, tableName: selectedTableName }).catch((err) => setError(toUiError(err)))}>
                    <IconDownload /> <span style={{ fontSize: "0.78rem", marginLeft: 3 }}>Download</span>
                  </button>
                )}
              </div>
              {previewData?.rows?.length ? (
                <>
                  <div className="preview-wrap">
                    <table className="preview-table">
                      <thead><tr>{previewData.columns.map((col) => <th key={col}>{col}</th>)}</tr></thead>
                      <tbody>
                        {previewData.rows.map((row, i) => (
                          <tr key={i}>{previewData.columns.map((col) => <td key={col}>{String(row[col] ?? "")}</td>)}</tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {hasMorePreview && (
                    <button className="load-more-btn" onClick={() => loadPreview(selectedTableName, selectedSessionId, previewPage + 1)} disabled={previewLoading}>
                      {previewLoading ? <><IconSpinner /> Loadingâ€¦</> : `Load More Rows (showing ${previewData.rows.length})`}
                    </button>
                  )}
                </>
              ) : (
                <div className="empty-state">{selectedTableName ? "No rows to preview." : "Select a table to preview."}</div>
              )}
            </div>
          </section>

          {/* â”€â”€ RIGHT: Chat Panel â”€â”€ */}
          <section className="chat-panel">
            <div className="chat-messages" ref={chatScrollRef}>
              {chatMessages.length === 0 && events.length === 0 && (
                <div className="chat-empty">
                  <div className="chat-empty-icon">âœ¦</div>
                  <p>Ask the agent to clean, transform, or analyze your uploaded tables.</p>
                </div>
              )}

              {/* Chat messages + inline trace blocks */}
              {chatMessages.map((msg, idx) => {
                if (msg.role === "user") {
                  return (
                    <div key={idx} className="chat-bubble user">
                      <div className="bubble-body user-body">
                        <div className="bubble-text">{msg.text}</div>
                      </div>
                      <div className="bubble-avatar user-avatar">{(user?.full_name || "U")[0].toUpperCase()}</div>
                    </div>
                  );
                }
                if (msg.role === "trace") {
                  // Use live events if still streaming this trace, else frozen snapshot
                  const traceEvents = msg.frozenEvents ?? (isStreaming ? events : []);
                  if (!traceEvents.length) return null;
                  return (
                    <div key={idx} className="chat-trace-wrapper">
                      <StreamingTraceBlock events={traceEvents} isStreaming={isStreaming && !msg.frozenEvents} />
                    </div>
                  );
                }
                if (msg.role === "agent") {
                  return (
                    <div key={idx} className="chat-bubble agent">
                      <div className="bubble-avatar agent-avatar">A</div>
                      <div className="bubble-body">
                        <div className="bubble-text">{msg.text}</div>
                        {msg.table && (
                          <div className="bubble-table-ref">
                            <IconTable /> Created table: <code>{msg.table}</code>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                }
                return null;
              })}
            </div>

            {/* Input bar */}
            <div className="chat-input-wrap">
              <div className="chat-model-row">
                <span>Model</span>
                <select
                  className="chat-model-select"
                  value={chatModel}
                  onChange={(e) => setChatModel(e.target.value)}
                  disabled={isStreaming}
                >
                  {(modelConfig.all_models || []).map((model) => (
                    <option key={`${model.model_name}-${model.model_type}`} value={model.model_name}>
                      {model.model_name} ({model.model_type})
                    </option>
                  ))}
                </select>
              </div>
              <textarea
                className="chat-input"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey) { e.preventDefault(); if (canRun) handleRunTransform(); } }}
                placeholder="Describe what you want the agent to do with your tableâ€¦ (Enter to send)"
                rows={2}
                disabled={isStreaming}
              />
              <button className="send-btn" onClick={handleRunTransform} disabled={!canRun} title="Run Agent">
                {isStreaming ? <IconSpinner /> : <IconSend />}
              </button>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

