const ALLOWED_EXTENSIONS = ["pdf", "txt", "doc", "docx"];
const MAX_BYTES = 5 * 1024 * 1024;

const form = document.getElementById("form");
const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const dzText = document.getElementById("dz-text");
const urlInput = document.getElementById("url-input");
const submitBtn = document.getElementById("submit");
const formError = document.getElementById("form-error");
const pasteToggle = document.getElementById("paste-toggle");
const pasteField = document.getElementById("paste-field");
const descInput = document.getElementById("desc-input");
const progress = document.getElementById("progress");
const results = document.getElementById("results");

let resumeFile = null;

// ---------- File selection ----------
function setFile(file) {
  const ext = file.name.split(".").pop().toLowerCase();
  if (!ALLOWED_EXTENSIONS.includes(ext) || !file.name.includes(".")) {
    resumeFile = null;
    dropzone.classList.remove("has-file");
    showFormError("Only PDF, TXT, DOC and DOCX files are allowed.");
    return;
  }
  if (file.size > MAX_BYTES) {
    resumeFile = null;
    showFormError("That file is larger than 5 MB.");
    return;
  }
  resumeFile = file;
  hideFormError();
  dropzone.classList.add("has-file");
  dzText.textContent = `✓ ${file.name}`;
}

fileInput.addEventListener("change", () => fileInput.files[0] && setFile(fileInput.files[0]));
["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); })
);
dropzone.addEventListener("drop", (e) => e.dataTransfer.files[0] && setFile(e.dataTransfer.files[0]));

function showPasteField() {
  pasteField.hidden = false;
  pasteToggle.hidden = true;
}

pasteToggle.addEventListener("click", () => {
  showPasteField();
  descInput.focus();
});

// ---------- Submit flow ----------
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  hideFormError();

  const url = urlInput.value.trim();
  const description = pasteField.hidden ? "" : descInput.value.trim();
  if (!resumeFile) return showFormError("Upload your resume first.");
  if (!description && !/^https?:\/\/\S+\.\S+/i.test(url)) {
    return showFormError("Enter a valid job posting URL (starting with https://), or paste the job description.");
  }

  setBusy(true);
  results.hidden = true;
  results.replaceChildren();
  progress.hidden = false;
  setStep("validate");

  try {
    let job;
    try {
      job = await postJson("/api/validate-job", { url, description });
    } catch (err) {
      if (err.code === "unreadable") {
        // Keep the user in the form: reveal the paste box with the reason.
        showPasteField();
        showFormError(err.message);
        descInput.focus();
        return;
      }
      throw err;
    }
    if (!job.valid) {
      showError("This doesn't look like a job posting", job.reason);
      return;
    }

    setStep("judge");
    const body = new FormData();
    body.append("resume", resumeFile);
    body.append("job_id", job.job_id);
    const report = await request("/api/analyze", { method: "POST", body });
    renderReport(report);
  } catch (err) {
    showError("Something went wrong", err.message);
  } finally {
    setBusy(false);
    progress.hidden = true;
  }
});

async function postJson(url, data) {
  return request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

async function request(url, options) {
  let resp;
  try {
    resp = await fetch(url, options);
  } catch {
    throw new Error("Couldn't reach the server. Is it running?");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = data.detail;
    const message =
      typeof detail === "string" ? detail : detail?.message || `Request failed (HTTP ${resp.status}).`;
    const error = new Error(message);
    error.code = detail?.code;
    throw error;
  }
  return data;
}

function setBusy(busy) {
  submitBtn.disabled = busy;
  submitBtn.textContent = busy ? "Working…" : "Judge my resume";
}

function setStep(current) {
  const order = ["validate", "judge"];
  progress.querySelectorAll("li").forEach((li) => {
    const idx = order.indexOf(li.dataset.step);
    const cur = order.indexOf(current);
    li.classList.toggle("done", idx < cur);
    li.classList.toggle("active", idx === cur);
  });
}

function showFormError(msg) { formError.textContent = msg; formError.hidden = false; }
function hideFormError() { formError.hidden = true; }

// ---------- Rendering ----------
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function tone(score) {
  if (score >= 75) return "good";
  if (score >= 50) return "warn";
  return "bad";
}

function matchLabel(score) {
  if (score >= 75) return "Strong match";
  if (score >= 50) return "Partial match";
  return "Weak match";
}

function showError(title, message) {
  const card = el("section", "card error-card");
  card.append(el("h2", null, title), el("p", null, message));
  results.replaceChildren(card);
  results.hidden = false;
}

function renderReport(r) {
  const frag = document.createDocumentFragment();
  frag.append(heroCard(r), barsCard(r));
  if (r.rejection_reasons.length) frag.append(reasonsCard(r.rejection_reasons));
  if (r.rewritten_bullets.length) frag.append(rewritesCard(r.rewritten_bullets));
  if (r.missing_keywords.length || r.strengths.length) frag.append(extrasCard(r));

  const actions = el("div", "actions");
  const again = el("button", "btn-secondary", "Judge another resume");
  again.type = "button";
  again.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  actions.append(again);
  frag.append(actions);

  results.replaceChildren(frag);
  results.hidden = false;
  results.scrollIntoView({ behavior: "smooth", block: "start" });

  // Kick off the fill animations on the next frame so the transitions run.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    results.querySelectorAll("[data-fill]").forEach((bar) => { bar.style.width = `${bar.dataset.fill}%`; });
    results.querySelectorAll("[data-offset]").forEach((c) => { c.style.strokeDashoffset = c.dataset.offset; });
  }));
}

function heroCard(r) {
  const card = el("section", "card hero");
  const t = tone(r.overall_match);

  const radius = 72;
  const circumference = 2 * Math.PI * radius;
  const gauge = el("div", "gauge");
  gauge.innerHTML = `
    <svg width="168" height="168" viewBox="0 0 168 168" aria-hidden="true">
      <circle class="g-track" cx="84" cy="84" r="${radius}" fill="none" stroke-width="14"/>
      <circle class="g-fill" cx="84" cy="84" r="${radius}" fill="none" stroke-width="14"
        stroke="var(--${t})" stroke-linecap="round"
        stroke-dasharray="${circumference}" stroke-dashoffset="${circumference}"
        data-offset="${circumference * (1 - r.overall_match / 100)}"/>
    </svg>`;
  const label = el("div", "gauge-label");
  label.append(el("span", `gauge-value tone-${t}`, `${r.overall_match}%`), el("span", "gauge-caption", "job match"));
  gauge.append(label);
  gauge.setAttribute("role", "img");
  gauge.setAttribute("aria-label", `${r.overall_match}% match`);

  const info = el("div");
  if (r.job_title) {
    const role = el("p", "role");
    role.append("Judged against ", el("strong", null, r.job_title));
    if (r.company) role.append(` at ${r.company}`);
    info.append(role);
  }
  info.append(el("span", `badge bg-${t}`, matchLabel(r.overall_match)), el("p", "verdict", r.verdict));

  card.append(gauge, info);
  return card;
}

function barsCard(r) {
  const card = el("section", "card");
  card.append(el("h2", null, "Scorecard"));
  const bars = el("div", "bars");
  const rows = [{ name: "Overall resume quality", score: r.resume_quality, comment: "" }, ...r.category_scores];
  for (const row of rows) {
    const wrap = el("div");
    const head = el("div", "bar-head");
    head.append(el("span", null, row.name), el("span", `pct tone-${tone(row.score)}`, `${row.score}%`));
    const track = el("div", "bar-track");
    const fill = el("div", "bar-fill");
    fill.style.background = `var(--${tone(row.score)})`;
    fill.dataset.fill = row.score;
    track.append(fill);
    wrap.append(head, track);
    if (row.comment) wrap.append(el("p", "bar-comment", row.comment));
    bars.append(wrap);
  }
  card.append(bars);
  return card;
}

function reasonsCard(reasons) {
  const card = el("section", "card");
  card.append(el("h2", null, "Top 3 reasons this resume gets rejected"));
  const list = el("div", "reasons");
  reasons.forEach((reason, i) => {
    const item = el("article", "reason");
    const h = el("h3");
    h.append(el("span", "num", String(i + 1)), el("span", null, reason.title));

    const compare = el("div", "compare");
    const req = el("div");
    req.append(el("span", "kicker", "The job asks for"), reason.job_requires);
    const has = el("div");
    has.append(el("span", "kicker", "Your resume shows"), reason.resume_shows);
    compare.append(req, has);

    const fix = el("p", "fix");
    fix.append(el("strong", null, "Fix: "), reason.fix);

    item.append(h, compare, fix);
    list.append(item);
  });
  card.append(list);
  return card;
}

function rewritesCard(bullets) {
  const card = el("section", "card");
  card.append(el("h2", null, "Rewrite these 3 bullets for more impact"));
  const list = el("div", "rewrites");
  for (const b of bullets) {
    const item = el("article", "rewrite");
    const after = el("div", "after", b.rewritten);
    const copy = el("button", "copy-btn", "Copy");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(b.rewritten);
        copy.textContent = "Copied";
      } catch {
        copy.textContent = "Failed";
      }
      setTimeout(() => (copy.textContent = "Copy"), 1500);
    });
    after.append(copy);
    item.append(el("div", "before", b.original), after, el("p", "why", b.why));
    list.append(item);
  }
  card.append(list);
  return card;
}

function extrasCard(r) {
  const card = el("section", "card two-col");
  if (r.missing_keywords.length) {
    const col = el("div");
    col.append(el("h2", null, "Missing keywords"));
    const chips = el("div", "chips");
    r.missing_keywords.forEach((k) => chips.append(el("span", "chip", k)));
    col.append(chips);
    card.append(col);
  }
  if (r.strengths.length) {
    const col = el("div");
    col.append(el("h2", null, "What's already working"));
    const ul = el("ul", "strengths");
    r.strengths.forEach((s) => ul.append(el("li", null, s)));
    col.append(ul);
    card.append(col);
  }
  return card;
}
