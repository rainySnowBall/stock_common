const state = {
  sessionId: localStorage.getItem("finance-router-session-id") || null,
  webSearch: localStorage.getItem("finance-router-web-search") === "true",
  pending: false,
};

const messagesEl = document.getElementById("messages");
const emptyStateEl = document.getElementById("emptyState");
const composerEl = document.getElementById("composer");
const inputEl = document.getElementById("messageInput");
const sendButtonEl = document.getElementById("sendButton");
const webSearchToggleEl = document.getElementById("webSearchToggle");
const newChatButtonEl = document.getElementById("newChatButton");
const sessionIdEl = document.getElementById("sessionId");
const healthDotEl = document.getElementById("healthDot");
const healthTextEl = document.getElementById("healthText");
const turnStateEl = document.getElementById("turnState");

function init() {
  updateSessionLabel();
  webSearchToggleEl.checked = state.webSearch;
  checkHealth();
  bindEvents();
  inputEl.focus();
}

function bindEvents() {
  composerEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitMessage(inputEl.value);
  });

  inputEl.addEventListener("input", () => {
    autoResizeInput();
    sendButtonEl.disabled = state.pending || inputEl.value.trim().length === 0;
  });

  inputEl.addEventListener("keydown", async (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      await submitMessage(inputEl.value);
    }
  });

  newChatButtonEl.addEventListener("click", resetSession);

  webSearchToggleEl.addEventListener("change", () => {
    state.webSearch = webSearchToggleEl.checked;
    localStorage.setItem("finance-router-web-search", String(state.webSearch));
  });

  document.querySelectorAll(".prompt-chip").forEach((button) => {
    button.addEventListener("click", async () => {
      await submitMessage(button.textContent.trim());
    });
  });
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) {
      throw new Error("health check failed");
    }
    const payload = await response.json();
    healthDotEl.className = "health-dot ok";
    healthTextEl.textContent = `在线 · ${payload.sessions} sessions`;
  } catch (error) {
    healthDotEl.className = "health-dot error";
    healthTextEl.textContent = "离线";
  }
}

async function resetSession() {
  setPending(true);
  try {
    const response = await fetch("/api/session/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId }),
    });
    const payload = await response.json();
    state.sessionId = payload.session_id;
    localStorage.setItem("finance-router-session-id", state.sessionId);
    messagesEl.querySelectorAll(".message-row").forEach((node) => node.remove());
    emptyStateEl.classList.remove("hidden");
    turnStateEl.textContent = "新对话";
    updateSessionLabel();
    await checkHealth();
  } finally {
    setPending(false);
  }
}

async function submitMessage(rawMessage) {
  const message = rawMessage.trim();
  if (!message || state.pending) {
    return;
  }

  emptyStateEl.classList.add("hidden");
  appendMessage("user", message);
  inputEl.value = "";
  autoResizeInput();
  setPending(true);
  const typingNode = appendTyping();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        message,
        web_search: state.webSearch,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "request failed");
    }

    state.sessionId = payload.session_id;
    localStorage.setItem("finance-router-session-id", state.sessionId);
    typingNode.remove();
    appendMessage("assistant", payload.message, payload.response);
    updateSessionLabel();
    updateTopbar(payload.response);
    await checkHealth();
  } catch (error) {
    typingNode.remove();
    appendMessage("assistant", `请求失败：${error.message}`);
    turnStateEl.textContent = "请求失败";
  } finally {
    setPending(false);
  }
}

function appendMessage(role, content, response) {
  const row = document.createElement("article");
  row.className = `message-row ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "U" : "AI";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const contentEl = document.createElement("div");
  contentEl.className = "bubble-content";
  if (role === "assistant") {
    contentEl.classList.add("markdown-content");
    contentEl.innerHTML = renderMarkdown(content);
  } else {
    contentEl.textContent = content;
  }
  bubble.appendChild(contentEl);

  if (response) {
    bubble.dataset.status = response.status || "";
    const artifactsEl = renderBacktestArtifacts(response);
    if (artifactsEl) {
      bubble.appendChild(artifactsEl);
    }
  }

  row.appendChild(avatar);
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

function appendTyping() {
  const row = document.createElement("article");
  row.className = "message-row assistant";

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "AI";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = '<div class="typing" aria-label="处理中"><span></span><span></span><span></span></div>';

  row.appendChild(avatar);
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

function renderMarkdown(markdown) {
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let index = 0;

  function flushParagraph() {
    if (paragraph.length === 0) {
      return;
    }
    blocks.push(`<p>${paragraph.map(formatInline).join("<br>")}</p>`);
    paragraph = [];
  }

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      index += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      flushParagraph();
      const codeLines = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    if (trimmed.startsWith("\\[") && trimmed.endsWith("\\]") && trimmed.length > 4) {
      flushParagraph();
      blocks.push(renderDisplayMath(trimmed.slice(2, -2).trim()));
      index += 1;
      continue;
    }

    if (trimmed === "\\[") {
      flushParagraph();
      const mathLines = [];
      index += 1;
      while (index < lines.length && lines[index].trim() !== "\\]") {
        mathLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push(renderDisplayMath(mathLines.join("\n")));
      continue;
    }

    if (trimmed.startsWith("$$") && trimmed.endsWith("$$") && trimmed.length > 4) {
      flushParagraph();
      blocks.push(renderDisplayMath(trimmed.slice(2, -2).trim()));
      index += 1;
      continue;
    }

    if (isLikelyLatexLine(trimmed)) {
      flushParagraph();
      blocks.push(renderDisplayMath(trimmed));
      index += 1;
      continue;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph();
      const level = Math.min(heading[1].length + 1, 5);
      blocks.push(`<h${level}>${formatInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      flushParagraph();
      const items = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^[-*]\s+/, ""));
        index += 1;
      }
      blocks.push(`<ul>${items.map((item) => `<li>${formatInline(item)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      flushParagraph();
      const items = [];
      while (index < lines.length && /^\d+\.\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^\d+\.\s+/, ""));
        index += 1;
      }
      blocks.push(`<ol>${items.map((item) => `<li>${formatInline(item)}</li>`).join("")}</ol>`);
      continue;
    }

    paragraph.push(line);
    index += 1;
  }

  flushParagraph();
  return blocks.join("");
}

function formatInline(text) {
  if (isLikelyLatexLine(text)) {
    return renderInlineMath(text);
  }

  const tokens = [];
  const storeToken = (html) => {
    const token = `\u0000TOKEN${tokens.length}\u0000`;
    tokens.push([token, html]);
    return token;
  };

  let source = String(text);
  source = source.replace(/`([^`]+)`/g, (_, code) => {
    return storeToken(`<code>${escapeHtml(code)}</code>`);
  });
  source = source.replace(/\\\(([\s\S]+?)\\\)/g, (_, math) => {
    return storeToken(renderInlineMath(math));
  });
  source = source.replace(/\$([^$\n]+)\$/g, (_, math) => {
    return storeToken(renderInlineMath(math));
  });

  let value = escapeHtml(source);

  value = value.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  value = value.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
  );

  tokens.forEach(([token, html]) => {
    value = value.split(token).join(html);
  });
  return value;
}

function renderDisplayMath(latex) {
  return `<div class="math-block">${renderLatexMath(latex)}</div>`;
}

function renderInlineMath(latex) {
  return `<span class="math-inline">${renderLatexMath(latex)}</span>`;
}

function renderLatexMath(latex) {
  const input = String(latex || "").replace(/\s+/g, " ").trim();
  let position = 0;

  function parseExpression(stopChar = "") {
    const parts = [];
    while (position < input.length) {
      if (stopChar && input[position] === stopChar) {
        position += 1;
        break;
      }
      if (!stopChar && input[position] === "}") {
        position += 1;
        continue;
      }

      const atom = parseAtom();
      if (atom) {
        parts.push(parseScripts(atom));
      }
    }
    return parts.join("");
  }

  function parseAtom() {
    const char = input[position];
    if (!char) {
      return "";
    }
    if (/\s/.test(char)) {
      position += 1;
      return '<span class="math-space"></span>';
    }
    if (char === "\\") {
      return parseCommand();
    }
    if (char === "{") {
      position += 1;
      return parseExpression("}");
    }
    if (char === "^" || char === "_") {
      position += 1;
      return renderLiteral(char);
    }

    const identifier = input.slice(position).match(/^[A-Za-z0-9]+/);
    if (identifier) {
      position += identifier[0].length;
      return `<span class="math-ident">${escapeHtml(identifier[0])}</span>`;
    }

    const text = input.slice(position).match(/^[\u4e00-\u9fa5]+/);
    if (text) {
      position += text[0].length;
      return `<span class="math-text">${escapeHtml(text[0])}</span>`;
    }

    position += 1;
    return renderLiteral(char);
  }

  function parseCommand() {
    position += 1;
    if (position >= input.length) {
      return renderLiteral("\\");
    }

    let command = "";
    if (/[A-Za-z]/.test(input[position])) {
      const start = position;
      while (position < input.length && /[A-Za-z]/.test(input[position])) {
        position += 1;
      }
      command = input.slice(start, position);
    } else {
      command = input[position];
      position += 1;
    }

    if (command === "frac" || command === "dfrac" || command === "tfrac") {
      const numerator = parseRequiredGroup();
      const denominator = parseRequiredGroup();
      return [
        '<span class="math-frac">',
        `<span class="math-num">${numerator}</span>`,
        `<span class="math-den">${denominator}</span>`,
        "</span>",
      ].join("");
    }

    if (command === "text" || command === "mathrm" || command === "operatorname") {
      return `<span class="math-text">${escapeHtml(readRawGroup())}</span>`;
    }

    if (command === "sqrt") {
      return [
        '<span class="math-root">',
        '<span class="math-root-symbol">√</span>',
        `<span class="math-root-body">${parseRequiredGroup()}</span>`,
        "</span>",
      ].join("");
    }

    if (command === "left" || command === "right") {
      skipMathSpaces();
      if (input[position] === "\\") {
        return parseCommand();
      }
      const delimiter = input[position] || "";
      position += delimiter ? 1 : 0;
      return renderLiteral(delimiter);
    }

    const symbol = latexCommandSymbols[command];
    if (symbol) {
      return renderLiteral(symbol);
    }
    if (latexSpaceCommands.has(command)) {
      return '<span class="math-space"></span>';
    }

    return `<span class="math-ident">${escapeHtml(command)}</span>`;
  }

  function parseRequiredGroup() {
    skipMathSpaces();
    if (input[position] === "{") {
      position += 1;
      return parseExpression("}");
    }
    return parseAtom();
  }

  function parseScripts(base) {
    let superscript = "";
    let subscript = "";
    while (input[position] === "^" || input[position] === "_") {
      const marker = input[position];
      position += 1;
      if (marker === "^") {
        superscript = parseScriptArgument();
      } else {
        subscript = parseScriptArgument();
      }
    }
    if (!superscript && !subscript) {
      return base;
    }
    return [
      '<span class="math-scripts">',
      base,
      '<span class="math-script-stack">',
      superscript ? `<sup>${superscript}</sup>` : "",
      subscript ? `<sub>${subscript}</sub>` : "",
      "</span>",
      "</span>",
    ].join("");
  }

  function parseScriptArgument() {
    skipMathSpaces();
    if (input[position] === "{") {
      position += 1;
      return parseExpression("}");
    }
    return parseAtom();
  }

  function readRawGroup() {
    skipMathSpaces();
    if (input[position] !== "{") {
      return "";
    }

    position += 1;
    let depth = 1;
    let value = "";
    while (position < input.length && depth > 0) {
      const char = input[position];
      position += 1;
      if (char === "{") {
        depth += 1;
        value += char;
      } else if (char === "}") {
        depth -= 1;
        if (depth > 0) {
          value += char;
        }
      } else {
        value += char;
      }
    }
    return value;
  }

  function skipMathSpaces() {
    while (position < input.length && /\s/.test(input[position])) {
      position += 1;
    }
  }

  return `<span class="math-formula">${parseExpression()}</span>`;
}

const latexCommandSymbols = {
  "%": "%",
  ",": " ",
  ";": " ",
  ":": " ",
  "!": "",
  alpha: "α",
  beta: "β",
  gamma: "γ",
  delta: "δ",
  epsilon: "ε",
  theta: "θ",
  lambda: "λ",
  mu: "μ",
  pi: "π",
  sigma: "σ",
  omega: "ω",
  times: "×",
  cdot: "·",
  div: "÷",
  le: "≤",
  leq: "≤",
  ge: "≥",
  geq: "≥",
  ne: "≠",
  neq: "≠",
  approx: "≈",
  pm: "±",
  infty: "∞",
};

const latexSpaceCommands = new Set(["quad", "qquad", "enspace", "thinspace"]);

function renderLiteral(value) {
  const text = escapeHtml(value);
  if (!text) {
    return "";
  }
  if (/^[=+\-−×÷*/·<>≤≥≠≈±]$/.test(value)) {
    return `<span class="math-op">${text}</span>`;
  }
  if (/^[()[\]{}|]$/.test(value)) {
    return `<span class="math-delim">${text}</span>`;
  }
  if (/^[,.:;]$/.test(value)) {
    return `<span class="math-punct">${text}</span>`;
  }
  return `<span class="math-ident">${text}</span>`;
}

function isLikelyLatexLine(value) {
  const text = String(value || "").trim();
  return (
    text.length > 0 &&
    text.length < 600 &&
    /(\\frac\s*\{|\\dfrac\s*\{|\\tfrac\s*\{|\\sqrt\s*\{|\\times|\\cdot|\\text\s*\{|[_^]\s*\{)/.test(text) &&
    !/[。；！？]/.test(text)
  );
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderBacktestArtifacts(response) {
  const artifact = extractBacktestArtifact(response);
  if (!artifact) {
    return null;
  }

  const container = document.createElement("section");
  container.className = "backtest-dashboard";

  const metrics = artifact.metrics?.summary || {};
  const cards = [
    ["年化收益", formatPercent(metrics.performance?.annual_return)],
    ["年化超额", formatPercent(metrics.performance?.annual_excess_return)],
    ["Sharpe", formatNumber(metrics.risk?.sharpe_ratio)],
    ["最大回撤", formatPercent(metrics.risk?.max_drawdown)],
    ["年换手", formatPercent(metrics.trading?.annual_turnover)],
  ];

  const cardGrid = document.createElement("div");
  cardGrid.className = "metric-grid";
  cards.forEach(([label, value]) => {
    const card = document.createElement("div");
    card.className = "metric-tile";
    card.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>`;
    cardGrid.appendChild(card);
  });
  container.appendChild(cardGrid);

  const chartGrid = document.createElement("div");
  chartGrid.className = "chart-grid";
  const chartMap = new Map((artifact.charts?.charts || []).map((chart) => [chart.chart_id, chart]));
  [
    ["nav_curve", renderLineChart, ["strategy_nav", "benchmark_nav", "excess_nav"]],
    ["drawdown_curve", renderLineChart, ["strategy_drawdown", "benchmark_drawdown"]],
    ["monthly_return_heatmap", renderMonthlyHeatmap, []],
    ["annual_return_comparison", renderAnnualBars, []],
  ].forEach(([chartId, renderer, fields]) => {
    const chart = chartMap.get(chartId);
    if (!chart || chart.status === "empty" || !Array.isArray(chart.data) || chart.data.length === 0) {
      return;
    }
    const panel = document.createElement("div");
    panel.className = "chart-panel-mini";
    const title = document.createElement("div");
    title.className = "chart-title";
    title.textContent = chart.title || chartId;
    panel.appendChild(title);
    panel.appendChild(renderer(chart, fields));
    chartGrid.appendChild(panel);
  });

  if (chartGrid.children.length > 0) {
    container.appendChild(chartGrid);
  }
  if (artifact.report) {
    const reportLink = renderReportLink(artifact.report);
    if (reportLink) {
      container.appendChild(reportLink);
    }
  }
  return container;
}

function extractBacktestArtifact(response) {
  for (const result of response?.task_results || []) {
    const metadata = result?.metadata || {};
    if (metadata.metrics && metadata.charts) {
      return { metrics: metadata.metrics, charts: metadata.charts, report: metadata.report };
    }
  }
  return null;
}

function renderReportLink(report) {
  if (!report.url) {
    return null;
  }
  const actions = document.createElement("div");
  actions.className = "report-actions";

  const link = document.createElement("a");
  link.className = "report-download";
  link.href = report.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.download = report.filename || "";
  link.textContent = "下载 PDF 报告";
  actions.appendChild(link);
  return actions;
}

function renderLineChart(chart, fields) {
  const rows = chart.data || [];
  const series = fields
    .map((field) => {
      const points = rows
        .map((row, index) => ({ index, value: numericOrNull(row[field]) }))
        .filter((point) => point.value !== null);
      return { field, points };
    })
    .filter((item) => item.points.length > 1);

  if (series.length === 0) {
    return renderEmptyChart();
  }

  const allValues = series.flatMap((item) => item.points.map((point) => point.value));
  const minValue = Math.min(...allValues);
  const maxValue = Math.max(...allValues);
  const range = maxValue - minValue || Math.max(Math.abs(maxValue), 1);
  const width = 640;
  const height = 220;
  const pad = { left: 42, right: 16, top: 18, bottom: 28 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const maxIndex = Math.max(rows.length - 1, 1);
  const palette = ["#0b7f63", "#2563eb", "#7c3aed", "#b45309"];

  const paths = series.map((item, seriesIndex) => {
    const d = item.points
      .map((point, pointIndex) => {
        const x = pad.left + (point.index / maxIndex) * innerWidth;
        const y = pad.top + ((maxValue - point.value) / range) * innerHeight;
        return `${pointIndex === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
      })
      .join(" ");
    return `<path d="${d}" fill="none" stroke="${palette[seriesIndex % palette.length]}" stroke-width="2.2" />`;
  });

  const zeroY = minValue < 0 && maxValue > 0
    ? pad.top + ((maxValue - 0) / range) * innerHeight
    : null;
  const legend = series
    .map((item, index) => {
      const x = pad.left + index * 142;
      const y = height - 7;
      return `<g><circle cx="${x}" cy="${y - 4}" r="4" fill="${palette[index % palette.length]}"></circle><text x="${x + 8}" y="${y}" class="svg-label">${escapeHtml(chartFieldLabel(item.field))}</text></g>`;
    })
    .join("");

  return svgElement(`
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(chart.title)}">
      <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${height - pad.bottom}" class="svg-axis"></line>
      <line x1="${pad.left}" y1="${height - pad.bottom}" x2="${width - pad.right}" y2="${height - pad.bottom}" class="svg-axis"></line>
      ${zeroY ? `<line x1="${pad.left}" y1="${zeroY}" x2="${width - pad.right}" y2="${zeroY}" class="svg-zero"></line>` : ""}
      <text x="8" y="${pad.top + 4}" class="svg-label">${formatCompact(maxValue)}</text>
      <text x="8" y="${height - pad.bottom}" class="svg-label">${formatCompact(minValue)}</text>
      ${paths.join("")}
      ${legend}
    </svg>
  `);
}

function renderMonthlyHeatmap(chart) {
  const rows = (chart.data || []).filter((row) => Number.isFinite(row.monthly_return));
  if (rows.length === 0) {
    return renderEmptyChart();
  }
  const years = Array.from(new Set(rows.map((row) => row.year))).sort();
  const values = rows.map((row) => row.monthly_return);
  const maxAbs = Math.max(...values.map((value) => Math.abs(value)), 0.01);
  const cell = 24;
  const left = 44;
  const top = 18;
  const width = left + 12 * cell + 12;
  const height = top + years.length * cell + 24;
  const rowMap = new Map(rows.map((row) => [`${row.year}-${row.month}`, row.monthly_return]));

  const cells = [];
  years.forEach((year, rowIndex) => {
    cells.push(`<text x="6" y="${top + rowIndex * cell + 17}" class="svg-label">${year}</text>`);
    for (let month = 1; month <= 12; month += 1) {
      const value = rowMap.get(`${year}-${month}`);
      const x = left + (month - 1) * cell;
      const y = top + rowIndex * cell;
      cells.push(`<rect x="${x}" y="${y}" width="${cell - 2}" height="${cell - 2}" rx="3" fill="${heatColor(value, maxAbs)}"></rect>`);
    }
  });

  return svgElement(`
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(chart.title)}">
      ${Array.from({ length: 12 }, (_, index) => `<text x="${left + index * cell + 5}" y="12" class="svg-label">${index + 1}</text>`).join("")}
      ${cells.join("")}
    </svg>
  `);
}

function renderAnnualBars(chart) {
  const rows = (chart.data || []).filter((row) => Number.isFinite(row.strategy_annual_return));
  if (rows.length === 0) {
    return renderEmptyChart();
  }
  const width = 640;
  const height = 220;
  const pad = { left: 42, right: 16, top: 18, bottom: 34 };
  const values = rows.flatMap((row) => [
    row.strategy_annual_return,
    numericOrNull(row.benchmark_annual_return) ?? 0,
  ]);
  const minValue = Math.min(...values, 0);
  const maxValue = Math.max(...values, 0);
  const range = maxValue - minValue || 1;
  const baseY = pad.top + ((maxValue - 0) / range) * (height - pad.top - pad.bottom);
  const band = (width - pad.left - pad.right) / rows.length;
  const barWidth = Math.max(5, Math.min(18, band / 3));
  const bars = rows
    .map((row, index) => {
      const x = pad.left + index * band + band / 2;
      const strategy = row.strategy_annual_return;
      const benchmark = numericOrNull(row.benchmark_annual_return) ?? 0;
      return [
        barRect(x - barWidth - 1, strategy, barWidth, baseY, minValue, maxValue, height, pad, "#0b7f63"),
        barRect(x + 1, benchmark, barWidth, baseY, minValue, maxValue, height, pad, "#2563eb"),
        band > 34 ? `<text x="${x - 12}" y="${height - 10}" class="svg-label">${row.year}</text>` : "",
      ].join("");
    })
    .join("");

  return svgElement(`
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(chart.title)}">
      <line x1="${pad.left}" y1="${baseY}" x2="${width - pad.right}" y2="${baseY}" class="svg-zero"></line>
      ${bars}
      <text x="8" y="${pad.top + 4}" class="svg-label">${formatPercent(maxValue)}</text>
      <text x="8" y="${height - pad.bottom}" class="svg-label">${formatPercent(minValue)}</text>
    </svg>
  `);
}

function barRect(x, value, width, baseY, minValue, maxValue, height, pad, color) {
  const range = maxValue - minValue || 1;
  const innerHeight = height - pad.top - pad.bottom;
  const y = pad.top + ((maxValue - value) / range) * innerHeight;
  const top = Math.min(y, baseY);
  const barHeight = Math.max(1, Math.abs(baseY - y));
  return `<rect x="${x.toFixed(2)}" y="${top.toFixed(2)}" width="${width.toFixed(2)}" height="${barHeight.toFixed(2)}" fill="${color}"></rect>`;
}

function renderEmptyChart() {
  return svgElement(`
    <svg viewBox="0 0 640 120" role="img" aria-label="无数据">
      <text x="24" y="66" class="svg-label">无可展示数据</text>
    </svg>
  `);
}

function svgElement(source) {
  const wrapper = document.createElement("div");
  wrapper.className = "svg-chart";
  wrapper.innerHTML = source.trim();
  return wrapper;
}

function numericOrNull(value) {
  return Number.isFinite(value) ? value : null;
}

function formatPercent(value) {
  if (!Number.isFinite(value)) {
    return "N/A";
  }
  return `${(value * 100).toFixed(1)}%`;
}

function formatNumber(value) {
  if (!Number.isFinite(value)) {
    return "N/A";
  }
  return Number(value).toFixed(2);
}

function formatCompact(value) {
  if (!Number.isFinite(value)) {
    return "N/A";
  }
  if (Math.abs(value) < 1) {
    return formatPercent(value);
  }
  return Number(value).toFixed(2);
}

function chartFieldLabel(field) {
  const labels = {
    strategy_nav: "策略",
    benchmark_nav: "基准",
    excess_nav: "超额",
    strategy_drawdown: "策略回撤",
    benchmark_drawdown: "基准回撤",
    rolling_strategy_return: "策略",
    rolling_benchmark_return: "基准",
    rolling_excess_return: "超额",
    rolling_sharpe: "Sharpe",
  };
  return labels[field] || field;
}

function heatColor(value, maxAbs) {
  if (!Number.isFinite(value)) {
    return "#eef2f7";
  }
  const intensity = Math.min(Math.abs(value) / maxAbs, 1);
  if (value >= 0) {
    const green = Math.round(130 + intensity * 76);
    return `rgb(${Math.round(230 - intensity * 116)}, ${green}, ${Math.round(214 - intensity * 118)})`;
  }
  return `rgb(${Math.round(245 - intensity * 66)}, ${Math.round(216 - intensity * 102)}, ${Math.round(210 - intensity * 96)})`;
}

function updateTopbar(response) {
  turnStateEl.textContent = response.status;
}

function updateSessionLabel() {
  sessionIdEl.textContent = state.sessionId || "未创建";
}

function setPending(pending) {
  state.pending = pending;
  sendButtonEl.disabled = pending || inputEl.value.trim().length === 0;
  inputEl.disabled = pending;
  webSearchToggleEl.disabled = pending;
  turnStateEl.textContent = pending ? "处理中" : "等待输入";
}

function autoResizeInput() {
  inputEl.style.height = "auto";
  inputEl.style.height = `${Math.min(inputEl.scrollHeight, 180)}px`;
}

function scrollToBottom() {
  messagesEl.scrollTo({
    top: messagesEl.scrollHeight,
    behavior: "smooth",
  });
}

init();
