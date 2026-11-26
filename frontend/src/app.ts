/* ResearchMind AI frontend. Original TypeScript; no third-party UI framework. */

type Workspace = { id: string; name: string; created_at: string };
type Doc = {
  id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
  page_count: number;
  status: string;
  error: string | null;
  created_at: string;
};
type Citation = { document_id: string; filename: string; page: number | null; reference_id: string };
type Answer = {
  answer: string;
  citations: Citation[];
  entities: number;
  relationships: number;
  chunks: number;
  mode: string;
  ai_generated: boolean;
  conversation_id?: string;
};
type Conversation = { id: string; title: string; created_at: string };
type Message = { role: string; content: string; citations: string | null; created_at: string };
type Stats = {
  documents: number;
  indexed: number;
  failed: number;
  pages: number;
  bytes: number;
  by_type: Record<string, number>;
  conversations: number;
};
type Graph = { nodes: Array<{ id: string; label: string }>; edges: Array<{ source: string; target: string; label: string }> };
type Insight = { kind: string; finding: string; ai_generated: boolean; verification_required: boolean; citations: Citation[] };
type SearchResult = { citation: Citation; excerpt: string };
type Comparison = { answer: string; citations: Citation[]; ai_generated: boolean };
type Tab = "documents" | "search" | "ask" | "compare" | "graph" | "insights" | "conversations";

const state = {
  token: localStorage.getItem("rm_token") ?? "",
  email: "",
  authMode: "login" as "login" | "register",
  workspaces: [] as Workspace[],
  wid: "",
  tab: "documents" as Tab,
  docs: [] as Doc[],
  filter: "",
  status: "",
  uploadMessage: "",
  uploadErrors: [] as string[],
  answer: null as Answer | null,
  insight: null as Insight | null,
  comparison: null as Comparison | null,
  searchResults: [] as SearchResult[],
  graph: null as Graph | null,
  conversations: [] as Conversation[],
  messages: [] as Message[],
  activeConversation: "",
};

const root = document.getElementById("app") as HTMLElement;

function el(tag: string, props: Record<string, unknown> = {}, children: Array<Node | string> = []): HTMLElement {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === "class") node.className = String(value);
    else if (key === "text") node.textContent = String(value);
    else if (key === "onclick") node.addEventListener("click", value as EventListener);
    else if (key === "onchange") node.addEventListener("change", value as EventListener);
    else if (key === "onsubmit") node.addEventListener("submit", value as EventListener);
    else node.setAttribute(key, String(value));
  }
  for (const child of children) node.append(child);
  return node;
}

function clear(node: HTMLElement): void {
  node.replaceChildren();
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers, cache: "no-store" });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch { /* keep the HTTP status */ }
    if (response.status === 401) {
      state.token = "";
      localStorage.removeItem("rm_token");
      renderAuth();
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

function errorBox(message: string): HTMLElement {
  return el("p", { class: "error", text: message });
}

function notice(text: string): HTMLElement {
  return el("div", { class: "notice", text });
}

function fmtBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function renderAuth(): void {
  clear(root);
  const wrap = el("div", { class: "auth-wrap" });
  const card = el("div", { class: "auth-card" });
  const message = el("div", {});
  const email = el("input", { class: "input", type: "email", placeholder: "you@example.com", required: "true" }) as HTMLInputElement;
  const password = el("input", { class: "input", type: "password", placeholder: "At least 8 characters", required: "true" }) as HTMLInputElement;
  const submit = el("button", { class: "btn primary", type: "submit" }) as HTMLButtonElement;
  const toggle = el("button", { class: "btn quiet", type: "button" }) as HTMLButtonElement;

  function labels(): void {
    const registering = state.authMode === "register";
    (card.querySelector("h1") as HTMLElement).textContent = registering ? "Create your account" : "Welcome back";
    submit.textContent = registering ? "Create account" : "Sign in";
    toggle.textContent = registering ? "Already have an account? Sign in" : "New here? Create an account";
  }

  async function submitForm(event: Event): Promise<void> {
    event.preventDefault();
    clear(message);
    submit.disabled = true;
    try {
      const data = await api<{ token: string; email: string; workspace_id?: string }>(
        `/api/auth/${state.authMode}`,
        { method: "POST", body: JSON.stringify({ email: email.value, password: password.value }) },
      );
      state.token = data.token;
      state.email = data.email;
      localStorage.setItem("rm_token", state.token);
      await loadWorkspaces();
      if (data.workspace_id) state.wid = data.workspace_id;
      renderMain();
    } catch (cause) {
      message.append(errorBox(cause instanceof Error ? cause.message : "Authentication failed."));
    } finally {
      submit.disabled = false;
    }
  }

  toggle.addEventListener("click", () => {
    state.authMode = state.authMode === "login" ? "register" : "login";
    labels();
  });

  const form = el("form", { class: "grid", onsubmit: submitForm }, [
    el("div", {}, [el("label", { text: "Email" }), email]),
    el("div", {}, [el("label", { text: "Password" }), password]),
    submit,
    message,
  ]);
  card.append(
    el("h1", { text: "Welcome back" }),
    el("p", { class: "muted", text: "A private workspace for research documents, evidence-backed answers and knowledge graphs." }),
    form,
    toggle,
  );
  labels();
  wrap.append(card);
  root.append(wrap);
}

async function loadWorkspaces(): Promise<void> {
  const rows = await api<Workspace[]>("/api/workspaces");
  state.workspaces = rows;
  if (!rows.some((row) => row.id === state.wid)) state.wid = rows[0]?.id ?? "";
}

async function loadDocuments(): Promise<void> {
  const query = new URLSearchParams();
  if (state.filter) query.set("search", state.filter);
  if (state.status) query.set("status_filter", state.status);
  state.docs = await api<Doc[]>(`/api/workspaces/${state.wid}/documents?${query.toString()}`);
}

async function loadConversations(): Promise<void> {
  state.conversations = await api<Conversation[]>(`/api/workspaces/${state.wid}/conversations`);
}

function renderMain(): void {
  clear(root);
  const shell = el("div", { class: "shell" });
  const sidebar = el("aside", { class: "sidebar" });
  const brand = el("div", { class: "brand", text: "ResearchMind AI" });
  brand.append(el("small", { text: "Research intelligence" }));
  sidebar.append(brand, el("div", { class: "side-label", text: "Workspaces" }));

  const list = el("div", { class: "ws-list" });
  for (const ws of state.workspaces) {
    list.append(
      el("button", {
        class: `ws-item${ws.id === state.wid ? " active" : ""}`,
        type: "button",
        text: ws.name,
        onclick: () => { state.wid = ws.id; state.tab = "documents"; state.answer = null; state.insight = null; state.graph = null; void renderMain(); },
      }),
    );
  }
  sidebar.append(list);
  sidebar.append(
    el("button", {
      class: "btn quiet",
      type: "button",
      text: "+ New workspace",
      onclick: async () => {
        const name = window.prompt("Workspace name");
        if (!name) return;
        try {
          await api("/api/workspaces", { method: "POST", body: JSON.stringify({ name }) });
          await loadWorkspaces();
          renderMain();
        } catch (cause) { window.alert(String(cause)); }
      },
    }),
  );
  const foot = el("div", { class: "side-foot" });
  foot.append(el("div", { text: state.email }));
  foot.append(el("button", {
    type: "button",
    text: "Sign out",
    onclick: () => { state.token = ""; localStorage.removeItem("rm_token"); renderAuth(); },
  }));
  sidebar.append(foot);

  const main = el("main", { class: "main" });
  const current = state.workspaces.find((row) => row.id === state.wid);
  const top = el("div", { class: "topbar" }, [
    el("div", {}, [el("h1", { text: current?.name ?? "Research workspace" }), el("div", { class: "muted", text: "Private workspace · documents stay scoped to your account" })]),
  ]);
  main.append(top);

  const tabs = el("div", { class: "tabs" });
  const tabLabels: Array<[Tab, string]> = [
    ["documents", "Documents"], ["search", "Semantic search"], ["ask", "Ask"],
    ["compare", "Compare papers"], ["graph", "Knowledge graph"],
    ["insights", "Insights"], ["conversations", "Conversations"],
  ];
  for (const [key, label] of tabLabels) {
    tabs.append(el("button", {
      class: `tab${state.tab === key ? " active" : ""}`,
      type: "button",
      text: label,
      onclick: () => { state.tab = key; void renderMain(); },
    }));
  }
  main.append(tabs);

  const content = el("div", {});
  if (state.tab === "documents") renderDocuments(content);
  else if (state.tab === "search") renderSearch(content);
  else if (state.tab === "ask") renderAsk(content);
  else if (state.tab === "compare") renderCompare(content);
  else if (state.tab === "graph") renderGraph(content);
  else if (state.tab === "insights") renderInsights(content);
  else renderConversations(content);
  main.append(content);

  shell.append(sidebar, main);
  root.append(shell);
}

function renderStats(host: HTMLElement): void {
  const grid = el("div", { class: "grid cols-4" });
  grid.append(el("div", { class: "stat", text: "Loading…" }));
  host.append(grid);
  void api<Stats>(`/api/workspaces/${state.wid}/stats`).then((stats) => {
    clear(grid);
    const cells: Array<[string, string]> = [
      [String(stats.documents), "Documents"], [String(stats.indexed), "Indexed"],
      [String(stats.pages), "PDF pages"], [String(stats.conversations), "Conversations"],
    ];
    for (const [value, label] of cells) grid.append(el("div", { class: "stat" }, [el("b", { text: value }), el("span", { text: label })]));
  }).catch((cause) => { clear(grid); grid.append(errorBox(String(cause))); });
}

function renderDocuments(host: HTMLElement): void {
  const status = el("div", {});
  renderStats(host);

  const file = el("input", { class: "fixed", type: "file", accept: ".pdf,.docx", multiple: "true" }) as HTMLInputElement;
  file.addEventListener("change", async () => {
    const chosen = Array.from(file.files ?? []);
    if (!chosen.length) return;
    clear(status);
    state.uploadMessage = "";
    state.uploadErrors = [];
    status.append(el("span", { class: "muted", text: `Uploading ${chosen.length} document${chosen.length === 1 ? "" : "s"}…` }));
    const form = new FormData();
    for (const item of chosen) form.append("files", item);
    try {
      const result = await api<{ uploaded: Doc[]; errors: Array<{ filename: string; error: string }> }>(
        `/api/workspaces/${state.wid}/documents/batch`,
        { method: "POST", body: form },
      );
      state.uploadMessage = `${result.uploaded.length} uploaded · indexing started`;
      state.uploadErrors = result.errors.map((item) => `${item.filename}: ${item.error}`);
      await loadDocuments();
      renderMain();
      if (result.uploaded.length) scheduleIndexPoll();
    } catch (cause) {
      clear(status);
      status.append(errorBox(cause instanceof Error ? cause.message : "Upload failed."));
    }
  });

  const search = el("input", { class: "input", placeholder: "Filter by filename", value: state.filter }) as HTMLInputElement;
  const select = el("select", { class: "select" }) as HTMLSelectElement;
  for (const [value, label] of [["", "All statuses"], ["uploaded", "Uploaded"], ["indexed", "Indexed"], ["failed", "Failed"]]) {
    const option = el("option", { value, text: label }) as HTMLOptionElement;
    if (state.status === value) option.selected = true;
    select.append(option);
  }
  search.addEventListener("change", () => { state.filter = search.value; void renderMain(); });
  select.addEventListener("change", () => { state.status = select.value; void renderMain(); });

  const uploadCard = el("section", { class: "card" }, [
    el("h2", { text: "Add research documents" }),
    el("p", { class: "muted", text: "Select up to 20 PDF or DOCX files (10 MB each). Text is extracted and indexed into this workspace." }),
    el("div", { class: "row" }, [file, status]),
  ]);
  if (state.uploadMessage) uploadCard.append(el("p", { class: "muted", text: state.uploadMessage }));
  for (const message of state.uploadErrors) uploadCard.append(errorBox(message));
  host.append(uploadCard);

  const card = el("section", { class: "card" });
  card.append(el("h2", { text: "Workspace documents" }));
  card.append(el("div", { class: "row" }, [el("div", {}, [el("label", { text: "Search" }), search]), el("div", {}, [el("label", { text: "Status" }), select])]));
  const tableWrap = el("div", {});
  card.append(tableWrap);
  host.append(card);

  function draw(): void {
    clear(tableWrap);
    if (state.docs.length === 0) {
      tableWrap.append(el("p", { class: "muted", text: state.filter || state.status ? "No documents match these filters." : "No documents yet. Upload a PDF or DOCX to begin." }));
      return;
    }
    const table = el("table");
    const head = el("tr");
    for (const label of ["Document", "Type", "Size", "Pages", "Status", "Actions"]) head.append(el("th", { text: label }));
    table.append(el("thead", {}, [head]));
    const body = el("tbody");
    for (const doc of state.docs) {
      const row = el("tr");
      row.append(
        el("td", {}, [el("div", { text: doc.filename }), doc.error ? el("div", { class: "muted", text: doc.error }) : ""]),
        el("td", { text: doc.file_type.toUpperCase().replace(".", "") }),
        el("td", { text: fmtBytes(doc.size_bytes) }),
        el("td", { text: String(doc.page_count || "—") }),
        el("td", {}, [el("span", { class: `badge ${doc.status}`, text: doc.status })]),
        el("td", {}, [
          el("button", {
            class: "btn quiet", type: "button", text: "Re-index",
            onclick: async () => {
              try {
                await api(`/api/workspaces/${state.wid}/documents/${doc.id}/reindex`, { method: "POST" });
                await loadDocuments(); renderMain();
              } catch (cause) { window.alert(String(cause)); }
            },
          }),
          el("button", {
            class: "btn danger", type: "button", text: "Delete",
            onclick: async () => {
              if (!window.confirm(`Delete ${doc.filename}?`)) return;
              try {
                await api(`/api/workspaces/${state.wid}/documents/${doc.id}`, { method: "DELETE" });
                await loadDocuments(); renderMain();
              } catch (cause) { window.alert(String(cause)); }
            },
          }),
        ]),
      );
      body.append(row);
    }
    table.append(body);
    tableWrap.append(table);
  }

  draw();
  void loadDocuments().then(draw).catch((cause) => { clear(tableWrap); tableWrap.append(errorBox(String(cause))); });
}

function scheduleIndexPoll(): void {
  window.setTimeout(async () => {
    try {
      await loadDocuments();
      if (state.docs.some((doc) => doc.status === "uploaded")) {
        renderMain();
        scheduleIndexPoll();
      } else {
        renderMain();
      }
    } catch { /* a transient poll failure should not break the UI */ }
  }, 2000);
}

function renderAsk(host: HTMLElement): void {
  const question = el("textarea", { class: "textarea", placeholder: "Ask a question across this workspace…" }) as HTMLTextAreaElement;
  const mode = el("select", { class: "select" }) as HTMLSelectElement;
  for (const value of ["mix", "local", "global", "hybrid", "naive"]) mode.append(el("option", { value, text: value }));
  const submit = el("button", { class: "btn primary", type: "submit", text: "Ask" }) as HTMLButtonElement;
  const result = el("div", {});
  const form = el("form", { class: "grid", onsubmit: async (event: Event) => {
    event.preventDefault();
    if (!question.value.trim()) return;
    clear(result);
    submit.disabled = true;
    result.append(el("p", { class: "muted", text: "Searching documents and generating an evidence-backed answer…" }));
    try {
      const body: Record<string, string> = { question: question.value, mode: mode.value };
      if (state.answer?.conversation_id) body.conversation_id = state.answer.conversation_id;
      state.answer = await api<Answer>(`/api/workspaces/${state.wid}/ask`, { method: "POST", body: JSON.stringify(body) });
      drawAnswer(result, state.answer);
    } catch (cause) {
      clear(result); result.append(errorBox(cause instanceof Error ? cause.message : "Query failed."));
    } finally { submit.disabled = false; }
  } }, [
    el("div", {}, [el("label", { text: "Question" }), question]),
    el("div", { class: "row" }, [el("div", {}, [el("label", { text: "Retrieval mode" }), mode]), el("button", { class: "btn primary", type: "submit", text: "Ask" })]),
    result,
  ]);
  host.append(el("section", { class: "card" }, [el("h2", { text: "Ask your research" }), form]));
  if (state.answer) drawAnswer(result, state.answer);
}

function renderSearch(host: HTMLElement): void {
  const query = el("input", {
    class: "input",
    type: "search",
    placeholder: "Find methods, findings, or concepts across your papers",
    required: "true",
  }) as HTMLInputElement;
  const area = el("div", {});
  const submit = el("button", { class: "btn primary", type: "submit", text: "Search papers" }) as HTMLButtonElement;
  const form = el("form", { class: "grid", onsubmit: async (event: Event) => {
    event.preventDefault();
    if (!query.value.trim()) return;
    clear(area);
    submit.disabled = true;
    area.append(el("p", { class: "muted", text: "Retrieving relevant passages from the LightRAG index…" }));
    try {
      const result = await api<{ results: SearchResult[] }>(
        `/api/workspaces/${state.wid}/search`,
        { method: "POST", body: JSON.stringify({ query: query.value, limit: 10 }) },
      );
      state.searchResults = result.results;
      drawSearchResults(area, result.results);
    } catch (cause) {
      clear(area);
      area.append(errorBox(cause instanceof Error ? cause.message : "Semantic search failed."));
    } finally { submit.disabled = false; }
  } }, [
    el("div", {}, [el("label", { text: "Search query" }), query]),
    submit,
    area,
  ]);
  host.append(el("section", { class: "card" }, [
    el("h2", { text: "Semantic search" }),
    el("p", { class: "muted", text: "Search indexed passages by meaning, not just filenames. Results link each excerpt to its source paper." }),
    form,
  ]));
}

function drawSearchResults(host: HTMLElement, results: SearchResult[]): void {
  clear(host);
  if (!results.length) {
    host.append(el("p", { class: "muted", text: "No relevant passages were found. Try a broader query or index more papers." }));
    return;
  }
  const list = el("div", { class: "list" });
  for (const result of results) {
    const page = result.citation.page ? ` · page ${result.citation.page}` : "";
    list.append(el("article", { class: "item search-result" }, [
      el("strong", { text: `${result.citation.filename}${page}` }),
      el("p", { class: "excerpt", text: result.excerpt }),
    ]));
  }
  host.append(el("h2", { text: `${results.length} relevant passages` }), list);
}

function renderCompare(host: HTMLElement): void {
  const papers = el("select", { class: "select", multiple: "true", size: "6" }) as HTMLSelectElement;
  for (const doc of state.docs.filter((item) => item.status === "indexed")) {
    papers.append(el("option", { value: doc.id, text: doc.filename }));
  }
  const question = el("textarea", {
    class: "textarea",
    text: "Compare the methodologies, key findings, and stated limitations.",
  }) as HTMLTextAreaElement;
  question.value = "Compare the methodologies, key findings, and stated limitations.";
  const area = el("div", {});
  const submit = el("button", { class: "btn primary", type: "submit", text: "Compare selected papers" }) as HTMLButtonElement;
  const form = el("form", { class: "grid", onsubmit: async (event: Event) => {
    event.preventDefault();
    const document_ids = Array.from(papers.selectedOptions, (option) => option.value);
    if (document_ids.length < 2) {
      clear(area);
      area.append(errorBox("Select at least two indexed papers."));
      return;
    }
    if (!question.value.trim()) {
      clear(area);
      area.append(errorBox("Enter a comparison question."));
      return;
    }
    clear(area);
    submit.disabled = true;
    area.append(el("p", { class: "muted", text: "Retrieving evidence from the selected papers and comparing their claims…" }));
    try {
      state.comparison = await api<Comparison>(`/api/workspaces/${state.wid}/compare`, {
        method: "POST",
        body: JSON.stringify({ document_ids, question: question.value }),
      });
      drawComparison(area, state.comparison);
    } catch (cause) {
      clear(area);
      area.append(errorBox(cause instanceof Error ? cause.message : "Paper comparison failed."));
    } finally { submit.disabled = false; }
  } }, [
    el("div", {}, [el("label", { text: "Indexed papers (select 2–4)" }), papers]),
    el("div", {}, [el("label", { text: "Comparison question" }), question]),
    submit,
    area,
  ]);
  host.append(el("section", { class: "card" }, [
    el("h2", { text: "Compare research papers" }),
    el("p", { class: "muted", text: "LightRAG retrieves passages only from the selected papers. The analysis is generated from those excerpts and includes source citations." }),
    form,
  ]));
}

function drawComparison(host: HTMLElement, comparison: Comparison): void {
  clear(host);
  host.append(el("div", { class: "answer", text: comparison.answer }));
  if (comparison.ai_generated) host.append(notice("AI-generated comparison. Verify every claim against the cited source passages."));
  if (comparison.citations.length) {
    const list = el("div", { class: "list" });
    for (const item of comparison.citations) {
      list.append(el("div", { class: "item", text: `${item.filename}${item.page ? ` · page ${item.page}` : ""}` }));
    }
    host.append(el("h2", { text: "Retrieved sources" }), list);
  }
}

function drawAnswer(host: HTMLElement, answer: Answer): void {
  clear(host);
  host.append(el("div", { class: "answer", text: answer.answer }));
  host.append(notice("AI-generated answer. Verify the cited sources before relying on it."));
  host.append(el("p", { class: "muted", text: `Mode ${answer.mode} · ${answer.entities} entities · ${answer.relationships} relationships · ${answer.chunks} evidence chunks` }));
  if (answer.citations.length) {
    const list = el("div", { class: "list" });
    for (const item of answer.citations) {
      list.append(el("div", { class: "item", text: `${item.filename}${item.page ? ` · page ${item.page}` : ""}` }));
    }
    host.append(el("h2", { text: "Sources" }), list);
  } else {
    host.append(el("p", { class: "muted", text: "No structured source references were returned for this answer." }));
  }
}

function renderGraph(host: HTMLElement): void {
  const area = el("div", {});
  host.append(el("section", { class: "card" }, [el("h2", { text: "Knowledge graph" }), area]));
  void api<Graph>(`/api/workspaces/${state.wid}/graph?limit=120`).then((graph) => {
    state.graph = graph;
    clear(area);
    if (!graph.nodes.length) {
      area.append(el("p", { class: "muted", text: "No knowledge graph yet. Index documents to extract entities and relationships." }));
      return;
    }
    area.append(el("p", { class: "muted", text: `${graph.nodes.length} entities · ${graph.edges.length} relationships · select a node to inspect connections` }));
    const controls = el("div", { class: "graph-controls" });
    const zoomLabel = el("label", { text: "Zoom" });
    const zoom = el("input", { type: "range", min: "70", max: "160", value: "100", "aria-label": "Graph zoom" }) as HTMLInputElement;
    controls.append(zoomLabel, zoom);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "knowledge-graph");
    svg.setAttribute("viewBox", "0 0 1000 620");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Interactive research knowledge graph");
    const content = document.createElementNS("http://www.w3.org/2000/svg", "g");
    svg.append(content);
    const positions = new Map<string, { x: number; y: number }>();
    const centerX = 500;
    const centerY = 310;
    const radius = Math.max(100, Math.min(250, graph.nodes.length * 3.2));
    graph.nodes.forEach((node, index) => {
      const angle = (2 * Math.PI * index) / graph.nodes.length - Math.PI / 2;
      positions.set(node.id, { x: centerX + radius * Math.cos(angle), y: centerY + radius * Math.sin(angle) });
    });
    for (const edge of graph.edges) {
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) continue;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", String(source.x));
      line.setAttribute("y1", String(source.y));
      line.setAttribute("x2", String(target.x));
      line.setAttribute("y2", String(target.y));
      line.setAttribute("class", "graph-edge");
      line.append(document.createElementNS("http://www.w3.org/2000/svg", "title"));
      line.querySelector("title")!.textContent = edge.label || `${edge.source} → ${edge.target}`;
      content.append(line);
    }
    const details = el("p", { class: "graph-details muted", text: "Choose an entity to see its relationships." });
    const nodeGroups = new Map<string, SVGGElement>();
    for (const node of graph.nodes) {
      const position = positions.get(node.id);
      if (!position) continue;
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      group.setAttribute("class", "graph-node");
      group.setAttribute("tabindex", "0");
      group.setAttribute("role", "button");
      group.setAttribute("aria-label", `Entity ${node.label}`);
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", String(position.x));
      circle.setAttribute("cy", String(position.y));
      circle.setAttribute("r", "16");
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", String(position.x));
      label.setAttribute("y", String(position.y + 34));
      label.textContent = node.label.length > 26 ? `${node.label.slice(0, 25)}…` : node.label;
      group.append(circle, label);
      const selectNode = () => {
        for (const entry of nodeGroups.values()) entry.classList.remove("selected");
        group.classList.add("selected");
        const connections = graph.edges
          .filter((edge) => edge.source === node.id || edge.target === node.id)
          .map((edge) => `${edge.source} → ${edge.target}${edge.label ? `: ${edge.label}` : ""}`);
        details.textContent = connections.length
          ? `${node.label}: ${connections.join(" · ")}`
          : `${node.label}: no relationships in the current graph view.`;
      };
      group.addEventListener("click", selectNode);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectNode();
        }
      });
      nodeGroups.set(node.id, group);
      content.append(group);
    }
    zoom.addEventListener("input", () => {
      const scale = Number(zoom.value) / 100;
      content.setAttribute("transform", `translate(${centerX * (1 - scale)} ${centerY * (1 - scale)}) scale(${scale})`);
    });
    area.append(controls, svg, details);
  }).catch((cause) => { clear(area); area.append(errorBox(String(cause))); });
}

function renderInsights(host: HTMLElement): void {
  const area = el("div", {});
  const buttons = el("div", { class: "row" });
  async function load(kind: string): Promise<void> {
    clear(area);
    area.append(el("p", { class: "muted", text: "Analysing the workspace…" }));
    try {
      state.insight = await api<Insight>(`/api/workspaces/${state.wid}/insights`, { method: "POST", body: JSON.stringify({ kind }) });
      clear(area);
      area.append(el("div", { class: "answer", text: state.insight.finding }));
      area.append(notice("AI-generated finding for exploration only. Confirm every claim against the cited documents."));
      for (const citation of state.insight.citations) {
        area.append(el("p", { class: "muted", text: `Source: ${citation.filename}${citation.page ? ` · page ${citation.page}` : ""}` }));
      }
    } catch (cause) { clear(area); area.append(errorBox(String(cause))); }
  }
  buttons.append(
    el("button", { class: "btn", type: "button", text: "Explore contradictions", onclick: () => void load("contradictions") }),
    el("button", { class: "btn", type: "button", text: "Explore research gaps", onclick: () => void load("gaps") }),
  );
  host.append(el("section", { class: "card" }, [el("h2", { text: "Research exploration" }), el("p", { class: "muted", text: "Requires at least two indexed documents. Findings are generated by the configured LLM and must be verified." }), buttons, area]));
  if (state.insight) area.append(el("div", { class: "answer", text: state.insight.finding }), notice("AI-generated finding; verification required."));
}

function renderConversations(host: HTMLElement): void {
  const layout = el("div", { class: "grid cols-2" });
  const list = el("div", { class: "list" });
  const chat = el("div", { class: "chat" });
  layout.append(el("div", {}, [el("h2", { text: "Conversation history" }), list]), el("div", {}, [el("h2", { text: "Messages" }), chat]));
  host.append(el("section", { class: "card" }, [layout]));

  void loadConversations().then(() => {
    clear(list);
    if (!state.conversations.length) { list.append(el("p", { class: "muted", text: "No conversations yet. Ask a question to start one." })); return; }
    for (const conversation of state.conversations) {
      list.append(el("button", {
        class: "ws-item", type: "button", text: conversation.title,
        onclick: () => void api<Message[]>(`/api/workspaces/${state.wid}/conversations/${conversation.id}`).then((messages) => {
          state.messages = messages;
          state.activeConversation = conversation.id;
          drawMessages(chat);
        }).catch((cause) => chat.append(errorBox(String(cause)))),
      }));
    }
  }).catch((cause) => list.append(errorBox(String(cause))));
  drawMessages(chat);
}

function drawMessages(host: HTMLElement): void {
  clear(host);
  if (!state.messages.length) { host.append(el("p", { class: "muted", text: "Select a conversation to read its messages." })); return; }
  for (const message of state.messages) {
    const bubble = el("div", { class: `msg ${message.role}`, text: message.content });
    if (message.citations) {
      try {
        const citations = JSON.parse(message.citations) as Citation[];
        if (citations.length) bubble.append(el("div", { class: "muted", text: `Sources: ${citations.map((item) => item.filename).join(", ")}` }));
      } catch { /* ignore malformed historical citations */ }
    }
    host.append(bubble);
  }
}

async function boot(): Promise<void> {
  if (!state.token) { renderAuth(); return; }
  try {
    const me = await api<{ email: string }>("/api/me");
    state.email = me.email;
    await loadWorkspaces();
    await loadDocuments();
    renderMain();
  } catch { renderAuth(); }
}

void boot();
