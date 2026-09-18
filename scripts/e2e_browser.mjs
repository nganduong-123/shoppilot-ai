import { spawn } from "node:child_process";
import { rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";

const base = process.env.SHOPPILOT_URL || "http://127.0.0.1:8000";
const chromePath = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const shop = "mint-fashion";
const timeoutMs = 45_000;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

async function waitFor(check, label, timeout = timeoutMs) {
  const started = Date.now();
  let lastError;
  while (Date.now() - started < timeout) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await sleep(250);
  }
  throw new Error(`Timeout: ${label}${lastError ? ` (${lastError.message})` : ""}`);
}

class CdpClient {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(url);
  }

  async connect() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", event => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || "Browser evaluation failed");
    }
    return result.result.value;
  }

  async navigate(url, readySelector) {
    await this.send("Page.navigate", { url });
    await waitFor(
      () => this.evaluate(`document.readyState === "complete" && !!document.querySelector(${JSON.stringify(readySelector)})`),
      `page ready: ${url}`,
    );
  }
}

async function api(route, options) {
  const response = await fetch(`${base}${route}`, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${route}: ${response.status} ${JSON.stringify(body)}`);
  return body;
}

const health = await api("/api/health");
if (health.status !== "ok" || !/^\d+\.\d+\.\d+$/.test(health.version) || !health.channels?.web) {
  throw new Error(`Unexpected server health: ${JSON.stringify(health)}`);
}

const port = await freePort();
const userData = path.join(os.tmpdir(), `shoppilot-e2e-${Date.now()}`);
const chrome = spawn(chromePath, [
  "--headless=new",
  "--disable-gpu",
  "--disable-extensions",
  "--hide-scrollbars",
  "--no-first-run",
  "--no-default-browser-check",
  "--remote-allow-origins=*",
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${userData}`,
  "about:blank",
], { windowsHide: true, stdio: "ignore" });

try {
  const targets = await waitFor(async () => {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    const rows = await response.json();
    const page = rows.find(row => row.type === "page");
    return page ? [page] : null;
  }, "Chrome DevTools endpoint");
  const client = new CdpClient(targets[0].webSocketDebuggerUrl);
  await client.connect();
  await client.send("Page.enable");
  await client.send("Runtime.enable");

  const marker = `E2E-${Date.now()}`;
  const widgetUrl = `${base}/static/widget.html?shop=${shop}`;
  await client.navigate(widgetUrl, "#form");
  await client.evaluate(`document.querySelector("#input").value=${JSON.stringify(`Tìm áo sơ mi trắng size M ${marker}`)};document.querySelector("#form").requestSubmit()`);
  const externalId = await waitFor(
    () => client.evaluate(`localStorage.getItem("shoppilot:${shop}:conversation")`),
    "widget creates conversation",
  );
  await waitFor(
    () => client.evaluate(`document.querySelectorAll(".message.assistant").length > 0 && !document.querySelector(".typing")`),
    "AI reply appears in widget",
  );

  const findConversation = async () => {
    const rows = await api(`/api/shops/${shop}/inbox/conversations`);
    return rows.find(row => row.external_conversation_id === externalId);
  };
  const conversation = await waitFor(findConversation, "conversation appears in inbox API");
  let history = await api(`/api/channels/web/${shop}/conversations/${externalId}/messages`);
  if (history.length < 2 || history.at(-1).direction !== "outbound") {
    throw new Error("Initial AI exchange was not persisted correctly");
  }

  await client.navigate(`${base}/`, "#conversation-list");
  await waitFor(() => client.evaluate(`document.querySelectorAll(".conversation-item").length > 0`), "inbox list");
  await waitFor(() => client.evaluate(`!document.querySelector("#thread-content").hidden`), "inbox thread opens");
  await client.evaluate(`document.querySelector("#bot-enabled").click()`);
  await waitFor(async () => !(await findConversation()).bot_enabled, "human takeover enabled");

  const humanReply = `Nhân viên đã tiếp nhận ${marker}`;
  await client.evaluate(`document.querySelector("#thread-reply-input").value=${JSON.stringify(humanReply)};document.querySelector("#thread-reply-form").requestSubmit()`);
  await waitFor(async () => {
    history = await api(`/api/channels/web/${shop}/conversations/${externalId}/messages`);
    return history.some(message => message.sender_type === "human" && message.content === humanReply);
  }, "human reply is persisted");

  await client.navigate(widgetUrl, "#form");
  await waitFor(
    () => client.evaluate(`document.querySelector("#messages").innerText.includes(${JSON.stringify(humanReply)})`),
    "human reply reaches widget",
  );
  const pausedMessage = `AI có đang tắt không ${marker}`;
  await client.evaluate(`document.querySelector("#input").value=${JSON.stringify(pausedMessage)};document.querySelector("#form").requestSubmit()`);
  await waitFor(async () => {
    history = await api(`/api/channels/web/${shop}/conversations/${externalId}/messages`);
    return history.at(-1)?.content === pausedMessage && history.at(-1)?.direction === "inbound";
  }, "message waits for human while AI is paused");

  await client.navigate(`${base}/`, "#conversation-list");
  await waitFor(() => client.evaluate(`!document.querySelector("#thread-content").hidden`), "thread reopens");
  await waitFor(() => client.evaluate(`document.querySelector("#bot-enabled").checked === false`), "takeover state shown");
  await client.evaluate(`document.querySelector("#bot-enabled").click()`);
  await waitFor(async () => (await findConversation()).bot_enabled, "AI is re-enabled");

  await client.navigate(widgetUrl, "#form");
  const resumedMessage = `Cho tôi xem sản phẩm khác ${marker}`;
  await client.evaluate(`document.querySelector("#input").value=${JSON.stringify(resumedMessage)};document.querySelector("#form").requestSubmit()`);
  await waitFor(async () => {
    history = await api(`/api/channels/web/${shop}/conversations/${externalId}/messages`);
    const lastTwo = history.slice(-2);
    return lastTwo[0]?.content === resumedMessage && lastTwo[1]?.direction === "outbound";
  }, "AI replies after being re-enabled");

  console.log(JSON.stringify({
    passed: true,
    version: health.version,
    conversation_id: conversation.id,
    checks: [
      "widget_to_ai",
      "unified_inbox",
      "human_takeover",
      "human_reply_to_widget",
      "paused_ai_does_not_reply",
      "ai_resume",
    ],
  }, null, 2));
  client.socket.close();
} finally {
  const exited = new Promise(resolve => chrome.once("exit", resolve));
  chrome.kill();
  await Promise.race([exited, sleep(3_000)]);
  await rm(userData, { recursive: true, force: true }).catch(() => {});
}
