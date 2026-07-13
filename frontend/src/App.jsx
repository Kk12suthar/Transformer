import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "https://127.0.0.1:8100";
const TOKEN_KEY = "mvp_token";
const USER_KEY = "mvp_user";
const PAGE_SIZE = 50;
const TABLE_TEMPLATES = [
  { id: "contacts", label: "Contacts", prompt: "Profile the selected customer or contact table, find missing fields, duplicate records, likely identifiers, and suggest a safe cleaning plan." },
  { id: "sales", label: "Sales", prompt: "Analyze the selected sales/order table for missing values, duplicate rows, date-like columns, numeric measures, and useful segment columns." },
  { id: "support", label: "Support", prompt: "Inspect the selected support/ticket table for status consistency, stale records, missing ownership, duplicate tickets, and date/time quality." },
  { id: "finance", label: "Finance", prompt: "Review the selected transaction table for duplicate rows, missing amounts or dates, suspicious numeric ranges, and category consistency." },
  { id: "inventory", label: "Inventory", prompt: "Analyze the selected inventory/product table for duplicate SKUs, missing product fields, inconsistent categories, and numeric outliers." },
];

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

async function cleanTableCopy({ token, sessionId, tableName }) {
  return apiRequest(
    `/api/v1/tables/${encodeURIComponent(tableName)}/clean?session_id=${encodeURIComponent(sessionId)}`,
    { method: "POST", token },
  );
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
    return t.length > 220 ? t.slice(0, 220) + "…" : t;
  }
  if (event.type === "function_request" && event.tool_args) {
    try {
      const args = typeof event.tool_args === "string" ? JSON.parse(event.tool_args) : event.tool_args;
      const s = JSON.stringify(args, null, 2);
      return s.length > 320 ? s.slice(0, 320) + "…" : s;
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
        <span className="stb-meta">{items.length} events • {toolCount} tools</span>
      </div>
    </div>
  );
}
