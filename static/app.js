(() => {
  "use strict";

  const statuses = ["Applied", "Interview", "Offer", "Rejected"];
  const state = {
    applications: [],
    user: null,
    mode: "login",
    editingId: null,
    loadRequest: 0,
    authConfigured: null,
  };
  const setupMessage = "Account access isn't connected yet. Configure Supabase by following “Deploy to Vercel” in README.md, then restart the app.";
  const $ = (selector) => document.querySelector(selector);
  const authView = $("#authView");
  const appView = $("#appView");
  const authForm = $("#authForm");
  const recoveryForm = $("#recoveryForm");
  const authHint = $("#authHint");
  const toast = $("#toast");
  let toastTimer;

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    let data = {};
    try {
      data = await response.json();
    } catch {
      throw new Error("The server returned an unexpected response.");
    }
    if (!response.ok) {
      if (response.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/session") {
        showAuth("Your session expired. Please sign in again.");
      }
      throw new Error(data.error || "Something went wrong. Please try again.");
    }
    return data;
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 3200);
  }

  function setHint(message, kind = "") {
    authHint.textContent = message;
    authHint.dataset.kind = kind;
  }

  function setAuthMode(mode, message = "") {
    state.mode = mode;
    const isSignup = mode === "signup";
    const isRecovery = mode === "recovery";
    const isVerify = mode === "verify";
    $("#authTitle").textContent = isSignup ? "Create your account." : isRecovery ? "Reset your password." : isVerify ? "Check your inbox." : "Your next move, in view.";
    $("#authDescription").textContent = isSignup
      ? "Start organizing your job search."
      : isRecovery
        ? "We’ll send a secure password reset link."
        : isVerify
          ? "Verify your email address to finish signing up."
          : "Keep every application organized and private.";
    $(".auth-tabs").hidden = isRecovery || isVerify;
    $("#passwordLabel").hidden = isRecovery || isVerify;
    $("#confirmPasswordLabel").hidden = !isSignup;
    $("#authSubmit").hidden = isVerify;
    $("#forgotPassword").hidden = isSignup || isRecovery || isVerify;
    $("#resendVerification").hidden = isSignup || isRecovery;
    $("#backToSignIn").hidden = !isRecovery;
    recoveryForm.hidden = mode !== "new-password";
    authForm.hidden = mode === "new-password";
    if (mode === "new-password") {
      $(".auth-tabs").hidden = true;
      $("#passwordLabel").hidden = true;
      $("#confirmPasswordLabel").hidden = true;
      $("#authSubmit").hidden = true;
      $("#forgotPassword").hidden = true;
    }
    $("#authSubmit").textContent = isSignup ? "Create account" : isRecovery ? "Send reset link" : "Sign in";
    $("#passwordLabel").querySelector("input").autocomplete = isSignup ? "new-password" : "current-password";
    $("#authEmail").required = !isVerify;
    $("#authPassword").required = !isRecovery && !isVerify;
    $("#confirmPassword").required = isSignup;
    $("#authPassword").value = "";
    $("#confirmPassword").value = "";
    setHint(message || (state.authConfigured === false ? setupMessage : ""));
    $("#loginTab").classList.toggle("active", !isSignup);
    $("#signupTab").classList.toggle("active", isSignup);
    $("#loginTab").setAttribute("aria-selected", String(!isSignup));
    $("#signupTab").setAttribute("aria-selected", String(isSignup));
  }

  function showAuth(message = "") {
    state.user = null;
    state.applications = [];
    appView.hidden = true;
    authView.hidden = false;
    setAuthMode("login", message);
  }

  function showApp(user) {
    state.user = user;
    authView.hidden = true;
    appView.hidden = false;
    $("#accountEmail").textContent = user.email || "";
    loadApplications();
  }

  async function loadApplications() {
    const requestId = ++state.loadRequest;
    try {
      const { applications } = await api("/api/applications");
      if (requestId !== state.loadRequest) return;
      state.applications = Array.isArray(applications) ? applications : [];
      renderBoard();
      renderStats();
      renderOverview();
    } catch (error) {
      if (requestId === state.loadRequest) showToast(error.message);
    }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Date unavailable";
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date);
  }

  function daysSince(value) {
    const time = new Date(value).getTime();
    if (Number.isNaN(time)) return "";
    const days = Math.max(0, Math.floor((Date.now() - time) / 86400000));
    return days === 0 ? "Updated today" : `${days} day${days === 1 ? "" : "s"} ago`;
  }

  function cardFor(application) {
    const card = element("article", "card");
    card.draggable = true;
    card.tabIndex = 0;
    card.dataset.id = application.id;
    card.setAttribute("aria-label", `${application.company}, ${application.role}, ${application.status}`);
    const company = element("div", "co", application.company);
    const role = element("div", "role", application.role);
    const meta = element("div", "meta");
    meta.append(element("span", "days", daysSince(application.updated_at)));
    if (application.stale) meta.append(element("span", "stale-badge", "Follow up"));
    card.append(company, role, meta);
    if (application.link) {
      const link = element("a", "company-link", "Open job posting ↗");
      link.href = application.link;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", `Open job posting for ${application.company} in a new tab`);
      card.append(link);
    }
    if (application.notes) {
      const notes = element("p", "notes-preview", application.notes);
      card.append(notes);
    }
    const actions = element("div", "card-actions");
    const edit = element("button", "edit", "Edit");
    edit.type = "button";
    edit.setAttribute("aria-label", `Edit ${application.company}`);
    edit.addEventListener("click", () => openEditor(application));
    const remove = element("button", "del", "Delete");
    remove.type = "button";
    remove.setAttribute("aria-label", `Delete ${application.company}`);
    remove.addEventListener("click", () => deleteApplication(application));
    actions.append(edit, remove);
    card.append(actions);
    card.addEventListener("dragstart", (event) => {
      card.classList.add("dragging");
      event.dataTransfer.setData("text/plain", application.id);
      event.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    return card;
  }

  function renderBoard() {
    const board = $("#board");
    board.replaceChildren();
    const search = $("#searchApplications").value.trim().toLocaleLowerCase();
    const filter = $("#filterStatus").value;
    statuses.forEach((status) => {
      const column = element("section", "col");
      column.dataset.status = status;
      column.setAttribute("aria-label", `${status} applications`);
      const heading = element("h3");
      heading.append(element("span", "", status));
      const matching = state.applications.filter((application) => {
        if (application.status !== status || (filter && filter !== status)) return false;
        const searchable = `${application.company} ${application.role} ${application.notes || ""}`.toLocaleLowerCase();
        return searchable.includes(search);
      });
      heading.append(element("span", "", String(matching.length)));
      column.append(heading);
      matching.forEach((application) => column.append(cardFor(application)));
      column.addEventListener("dragover", (event) => {
        event.preventDefault();
        column.classList.add("dragover");
      });
      column.addEventListener("dragleave", () => column.classList.remove("dragover"));
      column.addEventListener("drop", async (event) => {
        event.preventDefault();
        column.classList.remove("dragover");
        const id = event.dataTransfer.getData("text/plain");
        const application = state.applications.find((item) => item.id === id);
        if (application && application.status !== status) await updateApplication(id, { status });
      });
      board.append(column);
    });
  }

  function renderStats() {
    const counts = Object.fromEntries(statuses.map((status) => [status, 0]));
    state.applications.forEach((application) => {
      if (Object.hasOwn(counts, application.status)) counts[application.status] += 1;
    });
    const largest = Math.max(1, ...Object.values(counts));
    statuses.forEach((status) => {
      $(`#count${status}`).textContent = String(counts[status]);
      $(`.fbar[data-k="${status}"] .fill`).style.width = `${(counts[status] / largest) * 100}%`;
    });
    const stale = state.applications.filter((application) => application.stale).length;
    const responded = state.applications.filter((application) => application.status !== "Applied").length;
    $("#overviewTotal").textContent = String(state.applications.length);
    $("#overviewResponse").textContent = `${state.applications.length ? Math.round((responded / state.applications.length) * 100) : 0}%`;
    $("#overviewStale").textContent = String(stale);
  }

  function renderOverview() {
    const container = $("#recentApplications");
    container.replaceChildren();
    const recent = [...state.applications]
      .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
      .slice(0, 5);
    if (!recent.length) {
      container.append(element("p", "empty-state", "Your recent applications will appear here."));
      return;
    }
    recent.forEach((application) => {
      const item = element("button", "recent-item");
      item.type = "button";
      const details = element("span", "recent-details");
      details.append(element("strong", "", `${application.company} — ${application.role}`));
      details.append(element("span", "", `Updated ${formatDate(application.updated_at)}`));
      item.append(details, element("span", `recent-status status-${application.status.toLowerCase()}`, application.status));
      item.addEventListener("click", () => openEditor(application));
      container.append(item);
    });
  }

  function openEditor(application = null) {
    state.editingId = application?.id || null;
    $("#applicationModalTitle").textContent = application ? "Edit application" : "Add application";
    $("#saveApplication").textContent = application ? "Save changes" : "Save application";
    $("#company").value = application?.company || "";
    $("#role").value = application?.role || "";
    $("#applicationLink").value = application?.link || "";
    $("#applicationStatus").value = application?.status || "Applied";
    $("#notes").value = application?.notes || "";
    $("#historyPanel").hidden = !application;
    const backdrop = $("#applicationBackdrop");
    backdrop.hidden = false;
    requestAnimationFrame(() => backdrop.classList.add("show"));
    $("#company").focus();
    if (application) loadHistory(application.id);
    else $("#applicationHistory").replaceChildren();
  }

  function closeEditor() {
    const backdrop = $("#applicationBackdrop");
    backdrop.classList.remove("show");
    backdrop.hidden = true;
    state.editingId = null;
    $("#applicationForm").reset();
  }

  async function loadHistory(id) {
    const list = $("#applicationHistory");
    list.replaceChildren(element("li", "", "Loading history…"));
    try {
      const { history } = await api(`/api/applications/${encodeURIComponent(id)}/history`);
      list.replaceChildren();
      if (!history.length) list.append(element("li", "", "No status changes yet."));
      history.forEach((entry) => list.append(element("li", "", `${entry.status} · ${formatDate(entry.changed_at)}`)));
    } catch (error) {
      list.replaceChildren(element("li", "", error.message));
    }
  }

  async function saveApplication(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = Object.fromEntries(new FormData(form).entries());
    const id = state.editingId;
    $("#saveApplication").disabled = true;
    try {
      if (id) {
        await api(`/api/applications/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(data) });
        showToast("Application updated.");
      } else {
        await api("/api/applications", { method: "POST", body: JSON.stringify(data) });
        showToast("Application added.");
      }
      closeEditor();
      await loadApplications();
    } catch (error) {
      showToast(error.message);
    } finally {
      $("#saveApplication").disabled = false;
    }
  }

  async function updateApplication(id, changes) {
    try {
      await api(`/api/applications/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(changes) });
      await loadApplications();
      showToast("Application status updated.");
    } catch (error) {
      showToast(error.message);
    }
  }

  async function deleteApplication(application) {
    if (!window.confirm(`Delete the application for ${application.company}? This cannot be undone.`)) return;
    try {
      await api(`/api/applications/${encodeURIComponent(application.id)}`, { method: "DELETE" });
      state.applications = state.applications.filter((item) => item.id !== application.id);
      renderBoard();
      renderStats();
      renderOverview();
      showToast("Application deleted.");
    } catch (error) {
      showToast(error.message);
    }
  }

  function csvValue(value) {
    let text = String(value ?? "");
    if (/^[\s]*[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  }

  function exportCsv() {
    const columns = ["Company", "Role", "Status", "Job link", "Notes", "Created", "Updated"];
    const rows = state.applications.map((application) => [
      application.company,
      application.role,
      application.status,
      application.link,
      application.notes,
      application.created_at,
      application.updated_at,
    ]);
    const csv = [columns, ...rows].map((row) => row.map(csvValue).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = element("a");
    link.href = url;
    link.download = "pipeline-applications.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function submitAuth(event) {
    event.preventDefault();
    if (!authForm.reportValidity()) return;
    if (!state.authConfigured) {
      setHint(setupMessage, "error");
      return;
    }
    const email = $("#authEmail").value.trim();
    const password = $("#authPassword").value;
    const mode = state.mode;
    if (mode === "signup" && password !== $("#confirmPassword").value) {
      setHint("Your passwords do not match.", "error");
      return;
    }
    const submit = $("#authSubmit");
    submit.disabled = true;
    setHint("");
    try {
      if (mode === "recovery") {
        const result = await api("/api/auth/recovery", { method: "POST", body: JSON.stringify({ email }) });
        setAuthMode("login", result.message);
        return;
      }
      if (mode === "signup") {
        const result = await api("/api/auth/signup", { method: "POST", body: JSON.stringify({ email, password }) });
        if (result.verified) {
          const session = await api("/api/auth/session");
          if (session.user) showApp(session.user);
        } else {
          setAuthMode("verify", result.message);
          $("#authEmail").value = email;
        }
        return;
      }
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      const session = await api("/api/auth/session");
      if (!session.user) throw new Error("Your session could not be verified. Please try signing in again.");
      showApp(session.user);
    } catch (error) {
      setHint(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  }

  async function resendVerification() {
    const email = $("#authEmail").value.trim();
    if (!email) {
      setHint("Enter your email address first.", "error");
      $("#authEmail").focus();
      return;
    }
    try {
      const result = await api("/api/auth/resend", { method: "POST", body: JSON.stringify({ email }) });
      setHint(result.message);
    } catch (error) {
      setHint(error.message, "error");
    }
  }

  async function saveNewPassword(event) {
    event.preventDefault();
    if (!recoveryForm.reportValidity()) return;
    const password = $("#newPassword").value;
    if (password !== $("#confirmNewPassword").value) {
      setHint("Your passwords do not match.", "error");
      return;
    }
    $("#savePassword").disabled = true;
    try {
      await api("/api/auth/password", { method: "POST", body: JSON.stringify({ password }) });
      await api("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
      showAuth("Your password has been updated. Sign in with your new password.");
    } catch (error) {
      setHint(error.message, "error");
    } finally {
      $("#savePassword").disabled = false;
    }
  }

  async function handleEmailCallback() {
    const params = new URLSearchParams(window.location.search);
    const tokenHash = params.get("token_hash");
    const flow = params.get("type");
    if (!tokenHash) return false;
    window.history.replaceState({}, "", window.location.pathname);
    try {
      const result = await api("/api/auth/verify", {
        method: "POST",
        body: JSON.stringify({ token_hash: tokenHash, type: flow }),
      });
      if (result.recovery) {
        setAuthMode("new-password", "Choose a new password for your account.");
        authView.hidden = false;
        return true;
      }
      const session = await api("/api/auth/session");
      if (session.user) {
        showApp(session.user);
      } else {
        setAuthMode("login", "Email verified. You can now sign in.");
      }
    } catch (error) {
      showAuth(error.message);
    }
    return true;
  }

  async function initialize() {
    $("#loginTab").addEventListener("click", () => setAuthMode("login"));
    $("#signupTab").addEventListener("click", () => setAuthMode("signup"));
    authForm.addEventListener("submit", submitAuth);
    recoveryForm.addEventListener("submit", saveNewPassword);
    $("#forgotPassword").addEventListener("click", () => setAuthMode("recovery"));
    $("#backToSignIn").addEventListener("click", () => setAuthMode("login"));
    $("#resendVerification").addEventListener("click", resendVerification);
    $("#addApplication").addEventListener("click", () => openEditor());
    $("#cancelApplication").addEventListener("click", closeEditor);
    $("#applicationForm").addEventListener("submit", saveApplication);
    $("#applicationBackdrop").addEventListener("click", (event) => {
      if (event.target === $("#applicationBackdrop")) closeEditor();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !$("#applicationBackdrop").hidden) closeEditor();
    });
    $("#searchApplications").addEventListener("input", renderBoard);
    $("#filterStatus").addEventListener("change", renderBoard);
    $("#exportApplications").addEventListener("click", exportCsv);
    $("#boardNav").addEventListener("click", () => switchView("board"));
    $("#overviewNav").addEventListener("click", () => switchView("overview"));
    $("#signOut").addEventListener("click", async () => {
      try {
        await api("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
        showAuth("You have been signed out.");
      } catch (error) {
        showToast(error.message);
      }
    });

    try {
      const config = await api("/api/config");
      state.authConfigured = config.configured === true;
      if (!config.configured) {
        setHint(setupMessage, "error");
        return;
      }
      if (await handleEmailCallback()) return;
      const session = await api("/api/auth/session");
      if (session.user) showApp(session.user);
    } catch (error) {
      setHint(error.message, "error");
    }
  }

  function switchView(view) {
    const board = view === "board";
    $("#boardView").hidden = !board;
    $("#overviewView").hidden = board;
    $("#boardNav").classList.toggle("active", board);
    $("#overviewNav").classList.toggle("active", !board);
    $("#boardNav").setAttribute("aria-current", board ? "page" : "false");
    $("#overviewNav").setAttribute("aria-current", board ? "false" : "page");
  }

  initialize();
})();
